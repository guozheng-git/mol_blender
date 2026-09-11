"""Short, private worker IPC storage; independent of durable service paths.

Use a private XDG runtime directory when suitable, otherwise the node's /tmp.
Never use TMPDIR (it can point at a long shared registry). There is deliberately
no GC finalizer: the process owner must confirm child exit before closing IPC.
"""
import os
from pathlib import Path
import shutil
import stat
import tempfile


MAX_SOCKET_BYTES = 100  # Below both Linux and macOS AF_UNIX pathname limits.
PREFIX = 'br-'
SOCKET_NAME = 'w.sock'


class IPCUnavailable(RuntimeError):
    def __init__(self, *args):
        super().__init__(*args)
        from .compatibility import make
        self.diagnostic = make('worker_ipc_unavailable', phase='startup', component='worker', before_execution=True)


def _bases():
    xdg = os.environ.get('XDG_RUNTIME_DIR')
    # Apptainer uses ':' and ',' as bind delimiters, including in argv form.
    # Keep the IPC choice portable to an outer container without escaping rules.
    if xdg and not any(char in xdg for char in ':,'):
        try:
            path = Path(xdg)
            info = path.lstat()
            if (path.is_absolute() and path.resolve(strict=True) == path
                    and stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                    and stat.S_IMODE(info.st_mode) == 0o700
                    and os.access(path, os.W_OK | os.X_OK)):
                yield path
        except (OSError, ValueError):
            pass
    try:
        path = Path('/tmp').resolve(strict=True)
        info = path.stat()
        if (stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()}
                and (not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX)
                and os.access(path, os.W_OK | os.X_OK)):
            yield path
    except OSError:
        pass


class WorkerIPC:
    def __init__(self):
        self.directory = None
        self.socket_path = None
        for base in _bases():
            # tempfile's generated suffix is eight characters. Check again on
            # the actual path; an overlong XDG base must fall back to /tmp.
            if len(os.fsencode(base / (PREFIX + 'x'*8) / SOCKET_NAME)) > MAX_SOCKET_BYTES:
                continue
            try:
                directory = Path(tempfile.mkdtemp(prefix=PREFIX, dir=base))
            except OSError:
                continue
            try:
                directory.chmod(0o700)
                socket_path = directory / SOCKET_NAME
                if len(os.fsencode(socket_path)) > MAX_SOCKET_BYTES:
                    raise IPCUnavailable('Worker IPC socket pathname is too long')
            except BaseException:
                shutil.rmtree(directory)
                raise
            self.directory, self.socket_path = directory, socket_path
            return
        raise IPCUnavailable('No safe writable short directory for worker IPC; check XDG_RUNTIME_DIR or /tmp')

    def close(self):
        if self.directory is not None:
            shutil.rmtree(self.directory)
            self.directory = self.socket_path = None


def preflight():
    """Check creation/permissions/path length, without binding or launching."""
    ipc = WorkerIPC()
    try:
        assert stat.S_IMODE(ipc.directory.stat().st_mode) == 0o700
        assert len(os.fsencode(ipc.socket_path)) <= MAX_SOCKET_BYTES
        return {'transport': 'AF_UNIX', 'base': str(ipc.directory.parent),
                'directory_mode': '0700', 'socket_path_bytes': len(os.fsencode(ipc.socket_path)),
                'durable_root_independent': True, 'lifetime': 'worker_process',
                'outer_apptainer_bind': True}
    finally:
        ipc.close()
