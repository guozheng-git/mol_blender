"""Connect this computer to a user's own temporary gateway on a Slurm login node.

`cluster connect` uses the user's existing SSH login (keys, no prompts). It
starts or reuses a token-authenticated gateway bound to 127.0.0.1 on ONE pinned
login node, opens an SSH tunnel to that same node, and imports a fresh
credential into the ordinary cluster profile. The token is streamed straight
from SSH into a private file and never printed. Jobs then go through the
existing `cluster submit/status/fetch` commands and run under the user's own
Slurm account. Nothing is installed on the cluster: an administrator-installed
controller and matching image are required.
"""
import json
import os
from pathlib import Path
import random
import re
import shlex
import socket
import subprocess
import tempfile
import time

from .common import TERMINAL, atomic_json, one_line, read_json
from .connect_remote import file_lock, listeners

HOST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}')
USER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
SETTINGS = ('ssh_host', 'node', 'remote_executable', 'server_config', 'connection_dir', 'remote_user', 'local_port')
TUNNEL_WAIT = 20
SSH = 'ssh'


def state_path(profile, *, create=False):
    from ..config import config_root
    if not USER.fullmatch(profile):
        raise ValueError('Invalid cluster profile name')
    directory = config_root() / 'cluster-connections'
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory / (profile + '.json')


def load_state(profile):
    path = state_path(profile)
    return read_json(path, 64 * 1024) if path.exists() else {}


def remote_path(name, value):
    if not isinstance(value, str) or not value.startswith('/') or any(c in value for c in '\n\r\0'):
        raise ValueError(name + ' must be an absolute path on the gateway host')
    return value


def settings(profile, given):
    """Merge saved settings with explicit ones; a saved connection keeps its identity."""
    saved = load_state(profile)
    merged = dict(saved)
    for key, value in given.items():
        if value in (None, ''):
            continue
        if key in saved and saved[key] != value and key in ('connection_dir', 'remote_user', 'local_port'):
            raise ValueError(key + ' differs from the saved connection; disconnect with --revoke first')
        merged[key] = value
    missing = [key for key in ('ssh_host', 'node', 'remote_executable', 'server_config') if not merged.get(key)]
    if missing:
        raise ValueError('First connection needs: ' + ', '.join('--' + key.replace('_', '-') for key in missing))
    for key in ('ssh_host', 'node'):
        if not HOST.fullmatch(merged[key]):  # Also rejects a leading '-', which ssh would read as an option.
            raise ValueError('Invalid ' + key)
    remote_path('remote_executable', merged['remote_executable'])
    remote_path('server_config', merged['server_config'])
    if not merged.get('remote_user'):
        merged['remote_user'] = re.sub(r'[^A-Za-z0-9_.-]', '-', 'client-' + socket.gethostname().split('.')[0])[:64]
    if not USER.fullmatch(merged['remote_user']):
        raise ValueError('Invalid remote user name')
    merged.setdefault('connection_dir', str(Path(merged['server_config']).parent / 'connections'
                                            / (profile + '-' + merged['remote_user'])))
    remote_path('connection_dir', merged['connection_dir'])
    if not merged.get('local_port'):
        merged['local_port'] = free_port()
    if type(merged['local_port']) is not int or not 1024 <= merged['local_port'] <= 65535:
        raise ValueError('local_port must be between 1024 and 65535')
    return merged


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def ssh_base(conf):
    # HostName pins every command and the tunnel to the same login node while the
    # alias keeps the user's own key, user name and jump settings.
    return [SSH, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'HostName=' + conf['node'], conf['ssh_host']]


def remote(conf, mode, *args, stdout=None, timeout=120):
    python = str(Path(conf['remote_executable']).parent / 'python')
    script = Path(__file__).with_name('connect_remote.py').read_bytes()
    command = ssh_base(conf) + [shlex.join([python, '-I', '-', mode, *map(str, args)])]
    result = subprocess.run(command, input=script, stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout)
    if result.returncode:
        raise RuntimeError('Gateway host ' + mode + ' failed: ' + one_line(result.stderr.decode(errors='replace')))
    return None if stdout else json.loads(result.stdout.decode().strip().splitlines()[-1])


def process_command(pid):
    result = subprocess.run(['ps', '-o', 'command=', '-p', str(pid)], capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else ''


def forward_spec(conf):
    return '127.0.0.1:%d:127.0.0.1:%d' % (conf['local_port'], conf['remote_port'])


def tunnel_process(conf):
    pid = conf.get('tunnel_pid')
    if not pid or not conf.get('remote_port'):
        return False
    command = process_command(pid).split()
    return all(value in command for value in (forward_spec(conf), 'HostName=' + conf['node'], conf['ssh_host']))


def tunnel_alive(conf):
    return tunnel_process(conf) and conf['tunnel_pid'] in listeners(conf['local_port'])


def open_tunnel(conf, log):
    command = [SSH, '-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
               '-o', 'ControlPath=none', '-o', 'ForkAfterAuthentication=no',
               '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3',
               '-o', 'HostName=' + conf['node'], '-L', forward_spec(conf), conf['ssh_host']]
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'ab') as handle:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    try:
        deadline = time.monotonic() + TUNNEL_WAIT
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('SSH tunnel exited; see ' + str(log))
            # A saved port may now belong to another application. Never send a token
            # until this dedicated SSH process has actually acquired the listener.
            if process.pid in listeners(conf['local_port']) and process.poll() is None:
                return process.pid
            time.sleep(.25)
        raise RuntimeError('SSH tunnel did not open within %d seconds' % TUNNEL_WAIT)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise


def close_tunnel(conf):
    if tunnel_process(conf):
        os.kill(conf['tunnel_pid'], 15)
        deadline = time.monotonic() + 5
        while tunnel_process(conf) and time.monotonic() < deadline:
            time.sleep(.1)
        return True
    return False


def import_credential(profile, conf):
    """Stream a fresh credential from the gateway host into a private file, import it, delete it."""
    from .client import configure, profile_path
    url = 'http://127.0.0.1:%d' % conf['local_port']
    with tempfile.TemporaryDirectory(prefix='.blender-render-credential-') as temporary:
        transfer = Path(temporary) / 'credential.json'
        fd = os.open(transfer, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            remote(conf, 'issue', conf['connection_dir'], conf['remote_user'], url, stdout=handle)
        profile_path(profile).unlink(missing_ok=True)
        configure(profile, transfer)
    return url


def info(profile):
    from .client import Client
    return Client(profile).api('GET', '/v1/info')


def connect(profile='lab', *, reissue=False, **given):
    with file_lock(state_path(profile, create=True).with_suffix('.lock')):
        return _connect(profile, reissue=reissue, **given)


def _connect(profile, *, reissue, **given):
    from .client import profile_path, RemoteError
    saved = load_state(profile)
    conf = settings(profile, given)
    if saved and any(saved.get(key) != conf.get(key) for key in SETTINGS):
        close_tunnel(saved)
        # Stop on the old host before moving the shared spool to another node.
        # On the same node, the replacement controller can stop an old gateway
        # even when an administrator has removed the previous installation.
        stopping = dict(saved)
        if saved['node'] == conf['node']:
            stopping['remote_executable'] = conf['remote_executable']
        remote(stopping, 'stop', saved['connection_dir'])
        conf.pop('tunnel_pid', None)
    reused_tunnel = tunnel_alive(conf)
    if reused_tunnel and profile_path(profile).exists() and not reissue:
        try:
            details = info(profile)
            return summary(profile, conf, details, gateway_started=False, tunnel_reused=True)
        except (RemoteError, OSError, ValueError):
            close_tunnel(conf)
            reused_tunnel = False
    ports = random.SystemRandom().sample(range(20000, 60000), 3)
    gateway = remote(conf, 'ensure', conf['server_config'], conf['connection_dir'], *ports)
    if conf.get('remote_port') != gateway['port']:
        close_tunnel(conf)
        reused_tunnel = False
    conf.update(remote_port=gateway['port'], gateway_pid=gateway['pid'])
    atomic_json(state_path(profile, create=True), conf)
    if not tunnel_alive(conf):
        conf['tunnel_pid'] = open_tunnel(conf, state_path(profile).with_suffix('.tunnel.log'))
        atomic_json(state_path(profile), conf)
    credential_url = 'http://127.0.0.1:%d' % conf['local_port']
    if reissue or not profile_path(profile).exists() or read_json(profile_path(profile), 64 * 1024).get('url') != credential_url:
        import_credential(profile, conf)
    try:
        details = info(profile)
    except RemoteError as exc:
        if 'HTTP 401' not in str(exc):
            raise
        import_credential(profile, conf)  # A gateway restart with a stale or revoked token.
        details = info(profile)
    return summary(profile, conf, details, gateway_started=gateway['started'], tunnel_reused=reused_tunnel)


def summary(profile, conf, details, **extra):
    return {'status': 'connected', 'profile': profile, 'url': 'http://127.0.0.1:%d' % conf['local_port'],
            'ssh_host': conf['ssh_host'], 'node': conf['node'], 'remote_port': conf.get('remote_port'),
            'engine_build': details.get('engine_build'), 'version': details.get('version'), **extra}


def active_jobs(profile):
    from .client import Client
    try:
        return [job['job_id'] for job in Client(profile).api('GET', '/v1/jobs') if job.get('status') not in TERMINAL]
    except Exception:
        return None


def disconnect(profile='lab', *, keep_gateway=False, revoke=False):
    with file_lock(state_path(profile, create=True).with_suffix('.lock')):
        return _disconnect(profile, keep_gateway=keep_gateway, revoke=revoke)


def _disconnect(profile, *, keep_gateway, revoke):
    from .client import profile_path
    conf = load_state(profile)
    if not conf:
        raise ValueError('No saved connection for profile ' + profile)
    pending = active_jobs(profile) if profile_path(profile).exists() and tunnel_alive(conf) else None
    tunnel = close_tunnel(conf)
    gateway = {'stopped': False} if keep_gateway else remote(conf, 'stop', conf['connection_dir'])
    result = {'status': 'disconnected', 'profile': profile, 'tunnel_closed': tunnel,
              'gateway_stopped': gateway['stopped'], 'active_jobs': pending}
    if pending:
        result['note'] = ('Slurm jobs keep running without the gateway. Run `blender-render cluster connect --profile '
                          + profile + '` again to check and fetch them.')
    conf.pop('tunnel_pid', None)
    if revoke:
        result['revoked'] = remote(conf, 'revoke', conf['connection_dir'], conf['remote_user'])['revoked']
        profile_path(profile).unlink(missing_ok=True)
        state_path(profile).unlink(missing_ok=True)
    else:
        atomic_json(state_path(profile), conf)
    return result


def status(profile='lab'):
    from .client import profile_path
    conf = load_state(profile)
    if not conf:
        return {'profile': profile, 'connection': None}
    result = {'profile': profile, **{key: conf.get(key) for key in SETTINGS}, 'remote_port': conf.get('remote_port'),
              'tunnel_open': tunnel_alive(conf), 'profile_configured': profile_path(profile).exists()}
    if result['tunnel_open'] and result['profile_configured']:
        try:
            result['gateway'] = {key: value for key, value in info(profile).items() if key in ('engine_build', 'version', 'protocol')}
        except Exception as exc:
            result['gateway_error'] = one_line(str(exc), 200)
    return result
