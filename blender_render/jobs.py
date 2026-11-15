"""One lifecycle for every render request: submit, observe, wait, fetch, cancel.

co-pymol, Structure Studio, the PyMOL bridge and the CLI all go through here, so a
run submitted by one client can be checked or recovered by any other.

Local renders run synchronously through ``local.render_payload``. Lab renders go
through ``cluster.Client`` and are identified by one receipt, ``<output>.cluster.json``,
which the client writes before uploading. Everything after submission starts from
that receipt. Nothing here resubmits: an ambiguous submission is only observed, and
the gateway treats a repeated request ID as the same job.
"""
from __future__ import annotations

from contextlib import contextmanager
import time
from pathlib import Path

from .cluster.protocol import JobStatus, stop_waiting

LOCAL, LAB = 'local', 'lab'
_BACKENDS = {'local': LOCAL, 'lab': LAB, 'cluster': LAB, 'gpu': LAB}
POLL_SECONDS = 3
MAX_BACKOFF_SECONDS = 30


def backend(value: str) -> str:
    """Canonical backend name; 'cluster' and 'gpu' are accepted aliases for the lab."""
    if value not in _BACKENDS:
        raise ValueError("backend must be 'local' or 'lab'")
    return _BACKENDS[value]


def receipt_for(output) -> Path:
    """The lab receipt that a render to this output writes before uploading."""
    return Path(output).expanduser().absolute().with_suffix('.cluster.json')


@contextmanager
def lab_connection(profile: str, *, keep: bool = False):
    """Use the saved lab connection, connecting once if its tunnel is closed.

    A profile configured with only a URL or credential has no SSH connection to
    manage. When this call opened the tunnel and keep is false, it is closed again
    afterwards; a tunnel that was already open (a terminal, another chat) is left
    alone. Queued Slurm jobs keep running either way, and their receipts still fetch.
    """
    from .cluster import connect
    info = {'opened': False, 'connection': 'not managed (no saved SSH connection)'}
    if not connect.load_state(profile):
        yield info
        return
    info['opened'] = not connect.connect(profile).get('tunnel_reused')
    try:
        yield info
    finally:
        if not info['opened']:
            info['connection'] = 'left open (it was already open)'
        elif keep:
            info['connection'] = 'kept open'
        else:
            try:
                connect.disconnect(profile)
                info['connection'] = 'closed'
            except Exception as exc:  # The render outcome matters more than the tunnel.
                info['connection'] = f'disconnect failed: {exc}'


def submit(payload: dict, options: dict, output, *, backend_name: str = LOCAL, profile: str = 'lab',
           timeout: float = 600, cancelled=None) -> dict:
    """Render locally (blocking) or submit to the lab (returns once the gateway accepts).

    The caller owns the lab connection (see lab_connection).
    """
    if backend(backend_name) == LOCAL:
        from .local import render_payload
        result = render_payload(payload, output=output, options=options, timeout=timeout, cancelled=cancelled)
        return {**result, 'backend': LOCAL}
    from .cluster.client import Client
    result = Client(profile).submit(payload, options, output)
    return {**result, 'backend': LAB, 'profile': profile}


def _record(receipt) -> dict:
    from .cluster.jsonio import read_json
    return read_json(receipt, 64 * 1024)


def observe(receipt) -> dict:
    """The current state of the run a receipt names. Never submits anything.

    A receipt without a job ID means the upload's outcome was never confirmed. The
    gateway is asked by request ID: a job it knows is adopted, and its ID is saved
    to the receipt. A request it never received is reported as ``prepared``.
    """
    from .cluster.client import Client, RemoteError
    from .cluster.jsonio import atomic_json
    receipt = Path(receipt)
    record = _record(receipt)
    client = Client(record['profile'])
    try:
        status = client.recover(receipt)
    except RemoteError as exc:
        if record.get('job_id') or 'HTTP 404' not in str(exc):
            raise
        return {'status': JobStatus.PREPARED, 'receipt': str(receipt), 'request_id': record.get('request_id'),
                'message': 'The gateway has no record of this request: the upload never arrived and no job '
                           'exists. Submitting again creates the first job, not a duplicate.'}
    if status.get('job_id') and record.get('job_id') != status['job_id']:
        atomic_json(receipt, {**record, 'job_id': status['job_id'], 'status': status.get('status')})
    return {**status, 'receipt': str(receipt)}


def cancel(receipt) -> dict:
    """Ask the gateway to cancel; the job's own status confirms it later."""
    from .cluster.client import Client
    status = observe(receipt)
    if not status.get('job_id'):
        return status
    record = _record(receipt)
    return Client(record['profile']).api('POST', '/v1/jobs/' + status['job_id'] + '/cancel')


def fetch(receipt, output=None) -> dict:
    """Download and verify a completed job's files next to the receipt's output.

    Client.fetch confirms completion itself, so a receipt with a job ID costs no
    extra status round trip.
    """
    from .cluster.client import Client
    record = _record(receipt)
    job = record.get('job_id')
    if not job:
        status = observe(receipt)
        if status.get('status') != JobStatus.COMPLETED:
            return status
        job = status['job_id']
    return Client(record['profile']).fetch(job, output or record['output'])


def wait(receipt, *, timeout: float, interval: float = POLL_SECONDS, on_update=None, cancelled=None) -> dict:
    """Poll a run until it stops, the deadline passes, or its submission needs attention.

    Connection errors back off and are only observed: reconnecting never submits.
    ``cancelled`` is checked every round; the first time it is true, cancellation is
    requested once and polling continues until the gateway confirms it. The result
    carries ``waiting_stopped: True`` when the deadline passed first.
    """
    from .cluster.client import RemoteError
    deadline = time.monotonic() + timeout
    delay, cancel_sent, status = interval, False, {'status': 'unknown'}
    while True:
        try:
            if cancelled and not cancel_sent and cancelled():
                cancel(receipt)
                cancel_sent = True
            status = observe(receipt)
        except FileNotFoundError:
            raise  # A missing receipt is permanent, not a network outage.
        except (RemoteError, OSError) as exc:  # URLError, ConnectionError and TimeoutError are OSErrors.
            status = {'status': 'recovering', 'error': f'{type(exc).__name__}: {exc}', 'receipt': str(receipt)}
            pause, delay = delay, min(delay * 2, MAX_BACKOFF_SECONDS)
        else:
            pause, delay = interval, interval
            if stop_waiting(status['status']) or status['status'] == JobStatus.PREPARED:
                return {**status, 'cancel_requested': cancel_sent}
        status['cancel_requested'] = cancel_sent
        if on_update:
            on_update(status)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**status, 'waiting_stopped': True,
                    'message': 'Stopped waiting; the job may still be running. No new job was submitted.'}
        time.sleep(min(pause, remaining))
