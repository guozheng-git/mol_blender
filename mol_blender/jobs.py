"""One lifecycle for every render request: submit, observe, wait, fetch, cancel.

co-pymol, molblender-studio, the PyMOL bridge and the CLI all go through here, so a
run submitted by one client can be checked or recovered by any other.

Local renders run synchronously through ``local.render_payload``. Lab renders go
through ``cluster.Client`` and are identified by one receipt, ``<output>.cluster.json``,
which the client writes before uploading. Everything after submission starts from
that receipt. A request ID is never sent twice with a new identity: submitting to an
output whose receipt is unconfirmed uploads the same request ID again, and the gateway
treats a repeated request ID as the same job.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
import time
import uuid
from pathlib import Path

from .cluster.protocol import JobStatus, stop_waiting

LOCAL, LAB = 'local', 'lab'
_BACKENDS = {'local': LOCAL, 'lab': LAB, 'cluster': LAB, 'gpu': LAB}
POLL_SECONDS = 3
MAX_BACKOFF_SECONDS = 30
MAX_WAIT_SECONDS = 7 * 86400


def backend(value: str) -> str:
    """Canonical backend name; 'cluster' and 'gpu' are accepted aliases for the lab."""
    if value not in _BACKENDS:
        raise ValueError("backend must be 'local' or 'lab'")
    return _BACKENDS[value]


def receipt_for(output) -> Path:
    """The lab receipt that a render to this output writes before uploading."""
    return Path(output).expanduser().absolute().with_suffix('.cluster.json')


# -- The lab connection ------------------------------------------------------
# Several clients can share one tunnel: a Studio command, a co-pymol helper and a
# terminal. Each holds a lease while it uses the tunnel, and a tunnel is closed only
# when nobody holds one. A client that opened the tunnel and wanted it closed leaves
# a note, so the last client to finish closes it instead.

@contextmanager
def _leases_locked(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # Held only around connect/disconnect bookkeeping.
        yield


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _live_leases(directory: Path) -> list[Path]:
    live = []
    for lease in directory.glob('*.lease'):
        pid = lease.name.split('-', 1)[0]
        if pid.isdigit() and _pid_alive(int(pid)):
            live.append(lease)
        else:
            lease.unlink(missing_ok=True)  # Its process exited without releasing it.
    return live


@contextmanager
def lab_connection(profile: str, *, keep: bool = False, required: bool = True):
    """Use the saved lab connection, connecting once if its tunnel is closed.

    A profile configured with only a URL or credential has no SSH connection to
    manage. A tunnel this call opened is closed afterwards unless keep is true or
    another client is still using it; a tunnel that was already open (a terminal,
    another chat) is left alone. Queued Slurm jobs keep running either way, and
    their receipts still fetch.

    With required false, a failed connect is reported and the saved profile is used
    as it is: recovering an existing receipt must not fail just because SSH does.
    """
    from .cluster import connect
    info = {'opened': False, 'connection': 'not managed (no saved SSH connection)'}
    if not connect.load_state(profile):
        yield info
        return
    leases = connect.state_path(profile, create=True).with_suffix('.leases')
    close_note = leases / 'close-when-idle'
    with _leases_locked(leases):
        try:
            info['opened'] = not connect.connect(profile).get('tunnel_reused')
        except Exception as exc:
            if required:
                raise
            info['connection'] = f'connect failed, used the saved profile as is: {exc}'
            lease = None
        else:
            if info['opened']:
                close_note.unlink(missing_ok=True)  # A new tunnel; an older note no longer applies.
            lease = leases / f'{os.getpid()}-{uuid.uuid4().hex}.lease'
            lease.touch(mode=0o600)
    if lease is None:
        yield info
        return
    try:
        yield info
    finally:
        with _leases_locked(leases):
            lease.unlink(missing_ok=True)
            wants_close = not keep and (info['opened'] or close_note.exists())
            if not wants_close:
                info['connection'] = 'kept open' if info['opened'] else 'left open (it was already open)'
            elif _live_leases(leases):
                close_note.touch(mode=0o600)
                info['connection'] = 'left open (another render is still using it)'
            else:
                try:
                    connect.disconnect(profile)
                    close_note.unlink(missing_ok=True)
                    info['connection'] = 'closed'
                except Exception as exc:  # The render outcome matters more than the tunnel.
                    info['connection'] = f'disconnect failed: {exc}'


# -- The lifecycle -------------------------------------------------------------

def _record(receipt) -> dict:
    from .cluster.jsonio import read_json
    return read_json(receipt, 64 * 1024)


def _client(record: dict, client=None):
    from .cluster.client import Client
    return client or Client(record['profile'])


def submit(payload: dict, options: dict, output, *, backend_name: str = LOCAL, profile: str = 'lab',
           timeout: float = 600, cancelled=None) -> dict:
    """Render locally (blocking) or submit to the lab (returns once the gateway accepts).

    Submitting to the lab again with the same output never creates a second job: a
    receipt with a known job reports that job, and an upload the gateway never
    received is sent again under its original request ID. The caller owns the lab
    connection (see lab_connection).
    """
    if backend(backend_name) == LOCAL:
        from .local import render_payload
        result = render_payload(payload, output=output, options=options, timeout=timeout, cancelled=cancelled)
        return {**result, 'backend': LOCAL}
    receipt = receipt_for(output)
    if not receipt.exists():
        result = _client({'profile': profile}).submit(payload, options, output)
        return {**result, 'backend': LAB, 'profile': profile}
    record = _record(receipt)
    client = _client(record)
    status = observe(receipt, client=client)
    if status['status'] != JobStatus.PREPARED:
        return {**status, 'backend': LAB, 'profile': record['profile'], 'resubmitted': False}
    result = client.submit(payload, options, output, request_id=record['request_id'])
    return {**result, 'backend': LAB, 'profile': record['profile'], 'resubmitted': True}


def observe(receipt, *, client=None) -> dict:
    """The current state of the run a receipt names. Never submits anything.

    A receipt without a job ID means the upload's outcome was never confirmed. The
    gateway is asked by request ID: a job it knows is adopted, and its ID is saved
    to the receipt. A request it has no record of is reported as ``prepared``.
    """
    from .cluster.client import RemoteError
    from .cluster.jsonio import atomic_json
    receipt = Path(receipt)
    record = _record(receipt)
    client = _client(record, client)
    try:
        status = client.recover(receipt)
    except RemoteError as exc:
        if record.get('job_id') or 'HTTP 404' not in str(exc):
            raise
        return {'status': JobStatus.PREPARED, 'receipt': str(receipt), 'request_id': record.get('request_id'),
                'message': 'The gateway has no record of this request yet. Submitting again to the same '
                           'output sends the same request ID, so it can never create a second job.'}
    if status.get('job_id') and record.get('job_id') != status['job_id']:
        atomic_json(receipt, {**record, 'job_id': status['job_id'], 'status': status.get('status')})
    return {**status, 'receipt': str(receipt)}


def cancel(receipt, *, client=None) -> dict:
    """Ask the gateway to cancel; the job's own status confirms it later."""
    client = _client(_record(receipt), client)
    status = observe(receipt, client=client)
    if not status.get('job_id'):
        return status
    return client.api('POST', '/v1/jobs/' + status['job_id'] + '/cancel')


def fetch(receipt, output=None, *, client=None) -> dict:
    """Download and verify a completed job's files next to the receipt's output.

    Client.fetch confirms completion itself, so a receipt with a job ID costs no
    extra status round trip.
    """
    record = _record(receipt)
    client = _client(record, client)
    job = record.get('job_id')
    if not job:
        status = observe(receipt, client=client)
        if status.get('status') != JobStatus.COMPLETED:
            return status
        job = status['job_id']
    return client.fetch(job, output or record['output'])


def poll(check, *, timeout: float, interval: float = POLL_SECONDS, on_update=None, cancelled=None,
         request_cancel=None, done=stop_waiting, context: dict | None = None) -> dict:
    """Call check() until done(status) is true or the deadline passes.

    The one poll loop for lab work. Connection errors back off and are only
    observed: reconnecting never submits. ``cancelled`` is checked every round; the
    first time it is true, ``request_cancel`` is called until it succeeds, and the
    status is still observed every round, so a job that cannot be cancelled still
    reports where it stands. The result carries ``waiting_stopped: True`` when the
    deadline passed first.
    """
    from .cluster.client import RemoteError
    if not 0 < timeout <= MAX_WAIT_SECONDS:
        raise ValueError('Wait timeout must be between 0 and seven days')
    context = context or {}
    deadline = time.monotonic() + timeout
    delay, cancel_sent, status = interval, False, {'status': 'unknown'}
    while True:
        cancel_error = None
        if cancelled and request_cancel and not cancel_sent and cancelled():
            try:
                request_cancel()
                cancel_sent = True
            except FileNotFoundError:
                raise
            except (RemoteError, OSError) as exc:
                cancel_error = f'{type(exc).__name__}: {exc}'
        try:
            status = check()
        except FileNotFoundError:
            raise  # A missing receipt is permanent, not a network outage.
        except (RemoteError, OSError) as exc:  # URLError, ConnectionError and TimeoutError are OSErrors.
            status = {**context, 'status': 'recovering', 'error': f'{type(exc).__name__}: {exc}'}
            pause, delay = delay, min(delay * 2, MAX_BACKOFF_SECONDS)
        else:
            pause, delay = interval, interval
            if done(status['status']):
                return {**status, 'cancel_requested': cancel_sent}
        status['cancel_requested'] = cancel_sent
        if cancel_error:
            status['cancel_error'] = cancel_error
        if on_update:
            on_update(status)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**status, 'waiting_stopped': True,
                    'message': 'Stopped waiting; the job may still be running. No new job was submitted.'}
        time.sleep(min(pause, remaining))


def wait(receipt, *, timeout: float, interval: float = POLL_SECONDS, on_update=None, cancelled=None) -> dict:
    """Poll the run a receipt names until it stops or its submission needs attention."""
    client = _client(_record(receipt))  # One client for the whole wait, not one per round.
    return poll(lambda: observe(receipt, client=client), timeout=timeout, interval=interval,
                on_update=on_update, cancelled=cancelled, request_cancel=lambda: cancel(receipt, client=client),
                done=lambda state: stop_waiting(state) or state == JobStatus.PREPARED,
                context={'receipt': str(receipt)})
