"""Gateway-host side of `blender-render cluster connect`.

The client sends this file over SSH stdin to the administrator-installed
controller Python (`python -I - MODE ...`). It uses only the standard library
and that controller's own `blender_render.cluster`, so nothing is installed on
the cluster. Every gateway it starts is token-authenticated and loopback-only:
a login node's 127.0.0.1 is shared by all users on that node.
The local connector also reuses this module's listener checks and file locks.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

SERVE_WAIT = 30
LOCK_WAIT = 10


def emit(value):
    sys.stdout.write(json.dumps(value) + '\n')
    sys.stdout.flush()


def private_dir(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    stat = path.lstat()
    if path.is_symlink() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError('Connection directory must be a private directory owned by this account: ' + str(path))
    return path


@contextmanager
def file_lock(path):
    """Serialize lifecycle changes without replacing or unlinking the lock inode."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Connection lock must be a private regular file owned by this account')
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Another connection operation is still running; retry after it finishes')
                time.sleep(.1)
        yield
    finally:
        os.close(fd)


def write_private_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.write-', dir=Path(path).parent)
    with os.fdopen(fd, 'w') as handle:
        json.dump(value, handle, indent=2)
    os.replace(name, path)


def derived_config(base, connection):
    """The administrator's site config with this connection's own spool and tokens.

    Only spool, tokens_file and authentication change; Slurm, image and limits
    stay exactly as the site configured and validated them.
    """
    from blender_render.cluster.config import ServerConfig
    target = connection / 'server.json'
    document = json.loads(Path(base).read_text())
    document.update(spool=str(connection / 'jobs'), tokens_file=str(connection / 'users.json'),
                    authentication='token')
    if not target.exists() or json.loads(target.read_text()) != document:
        record = connection / 'gateway.json'
        state = json.loads(record.read_text()) if record.exists() else None
        if ours(state):
            raise ValueError('The site configuration changed while the gateway is running; '
                             'disconnect without --keep-gateway, then connect again (no revocation needed)')
        # Validate before replacing the last usable configuration. Jobs and credentials
        # survive this refresh; submitted jobs already carry their own execution settings.
        fd, name = tempfile.mkstemp(prefix='.server-', dir=connection)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(document, handle)
            ServerConfig.load(name)
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
    private_dir(connection / 'jobs')
    tokens = connection / 'users.json'
    if not tokens.exists():
        write_private_json(tokens, {'users': {}})  # serve requires an existing private database.
    ServerConfig.load(target)
    return target


def listening(port):
    """(port in use by anyone, PIDs of this account's listeners) for 127.0.0.1:port.

    Other users' PIDs are not visible, so 'in use' and 'ours' are separate answers.
    """
    port = str(port)
    if shutil.which('ss'):
        out = subprocess.run(['ss', '-Hltnp', 'sport', '=', ':' + port], capture_output=True, text=True, timeout=5).stdout
        lines = [line for line in out.splitlines() if re.search(r'(127\.0\.0\.1|\*|0\.0\.0\.0|\[::\]):' + port + r'\b', line)]
        return bool(lines), {int(pid) for line in lines for pid in re.findall(r'pid=(\d+)', line)}
    if shutil.which('lsof'):
        out = subprocess.run(['lsof', '-nP', '-iTCP:' + port, '-sTCP:LISTEN', '-Fpu'],
                             capture_output=True, text=True, timeout=5).stdout
        pids, current = set(), None
        for line in out.splitlines():
            if line.startswith('p'):
                current = int(line[1:])
            elif line.startswith('u') and int(line[1:]) == os.getuid() and current is not None:
                pids.add(current)
        return bool(re.search(r'^p', out, re.MULTILINE)), pids
    raise RuntimeError('Neither ss nor lsof is available to verify the gateway listener')


def listeners(port):
    return listening(port)[1]


def alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def ours(state):
    """The recorded gateway is still running and is the process holding its port."""
    return bool(state) and alive(state['pid']) and state['pid'] in listeners(state['port'])


def ensure(base, connection, *ports):
    connection = private_dir(connection)
    config = derived_config(base, connection)
    record = connection / 'gateway.json'
    state = json.loads(record.read_text()) if record.exists() else None
    if ours(state):
        return emit({'config': str(config), 'pid': state['pid'], 'port': state['port'], 'started': False})
    executable = os.path.join(os.path.dirname(sys.executable), 'blender-render')
    for port in map(int, ports):
        if listening(port)[0]:
            continue
        log = open(connection / 'gateway.log', 'ab')
        process = subprocess.Popen([executable, 'cluster', 'serve', '--config', str(config),
                                    '--bind', '127.0.0.1', '--port', str(port)],
                                   cwd=connection, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        log.close()
        deadline = time.monotonic() + SERVE_WAIT
        while time.monotonic() < deadline and process.poll() is None:
            if process.pid in listeners(port):
                write_private_json(record, {'pid': process.pid, 'port': port, 'started_at': time.time()})
                return emit({'config': str(config), 'pid': process.pid, 'port': port, 'started': True})
            time.sleep(.5)
        if process.poll() is None:
            process.terminate()
    raise RuntimeError('The gateway did not start; see gateway.log in the connection directory')


def issue(connection, user, url):
    """Write a fresh credential for USER to stdout and keep no copy.

    Reissuing keeps the same owner name, so jobs submitted earlier stay visible.
    """
    from blender_render.cluster.auth import add_user, revoke_user, users
    from blender_render.cluster.config import ServerConfig
    config = ServerConfig.load(private_dir(connection) / 'server.json')
    if config.tokens_file.exists() and user in users(config.tokens_file):
        revoke_user(config.tokens_file, user)
    with tempfile.TemporaryDirectory(prefix='.credential-', dir=connection) as temporary:
        output = Path(temporary) / 'credential.json'
        add_user(config.tokens_file, user, url, output)
        sys.stdout.buffer.write(output.read_bytes())
        sys.stdout.flush()


def stop(connection):
    connection = private_dir(connection)
    record = connection / 'gateway.json'
    state = json.loads(record.read_text()) if record.exists() else None
    stopped = False
    if ours(state):
        os.killpg(state['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 20
        while alive(state['pid']) and time.monotonic() < deadline:
            time.sleep(.25)
        stopped = not alive(state['pid'])
        if not stopped:
            raise RuntimeError('Gateway did not stop after SIGTERM')
    record.unlink(missing_ok=True)
    emit({'stopped': stopped})


def revoke(connection, user):
    from blender_render.cluster.auth import revoke_user, users
    from blender_render.cluster.config import ServerConfig
    config = ServerConfig.load(private_dir(connection) / 'server.json')
    revoked = config.tokens_file.exists() and user in users(config.tokens_file)
    if revoked:
        revoke_user(config.tokens_file, user)
    emit({'revoked': bool(revoked)})


def main(argv):
    modes = {'ensure': ensure, 'issue': issue, 'stop': stop, 'revoke': revoke}
    try:
        mode = argv[0]
        connection = private_dir(argv[2] if mode == 'ensure' else argv[1])
        with file_lock(connection / '.connection.lock'):
            modes[mode](*argv[1:])
    except Exception as exc:  # Reported to the client as one line; no traceback paths.
        sys.stderr.write(type(exc).__name__ + ': ' + str(exc) + '\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
