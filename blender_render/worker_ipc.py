"""Short private IPC storage, independent of durable state and inherited TMPDIR.

An explicit BLENDER_RENDER_IPC_BASE is mandatory when supplied. Otherwise use
safe XDG_RUNTIME_DIR or /tmp. Owners must confirm child exit before closing.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

MAX_SOCKET_BYTES = 100
PREFIX = 'br-'
SOCKET_NAME = 'w.sock'
BASE_ENV = 'BLENDER_RENDER_IPC_BASE'


class IPCUnavailable(RuntimeError):
    def __init__(self, *args, observed=None):
        super().__init__(*args)
        from .compatibility import make
        self.diagnostic = make('worker_ipc_unavailable', phase='startup', component='worker',
                               before_execution=True, observed=observed)


def _private_base(value):
    path = Path(value)
    info = path.lstat()
    if (not value or not path.is_absolute() or any(c in str(path) for c in ':,')
            or path.resolve(strict=True) != path or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
            or not os.access(path, os.W_OK | os.X_OK)):
        raise ValueError('Invalid private IPC base')
    return path


def _shared_parent(value):
    path = Path(value)
    info = path.lstat()
    if (not path.is_absolute() or path.resolve(strict=True) != path
            or any(c in str(path) for c in ':,') or not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
            or not os.access(path, os.W_OK | os.X_OK)):
        raise ValueError('Invalid node-local IPC parent')
    return path


def _tmp():
    # macOS /tmp may be a system symlink; only this fixed fallback is resolved.
    return _shared_parent(Path('/tmp').resolve(strict=True))


def _bases():
    if BASE_ENV in os.environ:
        try:
            yield _private_base(os.environ[BASE_ENV])
        except (OSError, ValueError) as exc:
            raise IPCUnavailable('Explicit worker IPC base is unavailable', observed={'base_available':False}) from exc
        return
    try:
        if os.environ.get('XDG_RUNTIME_DIR'):
            yield _private_base(os.environ['XDG_RUNTIME_DIR'])
    except (OSError, ValueError):
        pass
    try:
        yield _tmp()
    except (OSError, ValueError):
        pass


def _fits(base):
    return len(os.fsencode(base / (PREFIX + 'x'*8) / SOCKET_NAME)) <= MAX_SOCKET_BYTES


def _write_worker_evidence(base, record):
    # Same controller owns the explicit base and its single persistent worker.
    # Atomic replacement never follows a pre-existing evidence symlink.
    fd, name = tempfile.mkstemp(prefix='.ipc-', dir=base)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream)
        os.replace(name, base/'worker-ipc.json')
    finally:
        Path(name).unlink(missing_ok=True)


class WorkerIPC:
    def __init__(self, *, purpose='worker'):
        if purpose not in {'worker', 'probe'}:
            raise ValueError('Unsupported IPC owner purpose')
        self.directory = self.socket_path = None
        self._evidence_base = None
        self._record = None
        explicit = BASE_ENV in os.environ
        for base in _bases():
            if not _fits(base):
                if explicit:
                    raise IPCUnavailable('Explicit worker IPC base is too long')
                continue
            try:
                directory = Path(tempfile.mkdtemp(prefix='bp-' if purpose == 'probe' else PREFIX, dir=base))
            except OSError as exc:
                if explicit:
                    raise IPCUnavailable('Explicit worker IPC base cannot create storage', observed={'base_available':False}) from exc
                continue
            try:
                directory.chmod(0o700)
                socket_path = directory / SOCKET_NAME
                if len(os.fsencode(socket_path)) > MAX_SOCKET_BYTES:
                    raise IPCUnavailable('Worker IPC socket pathname is too long')
                self._record = {'socket_path_bytes':len(os.fsencode(socket_path)),
                                'durable_root_independent':True, 'directory_mode':'0700',
                                'cleanup_complete':False}
                if explicit and purpose == 'worker':
                    _write_worker_evidence(base, self._record)
                    self._evidence_base = base
            except BaseException as exc:
                shutil.rmtree(directory)
                if isinstance(exc, OSError):
                    raise IPCUnavailable('Worker IPC storage initialization failed') from exc
                raise
            self.directory, self.socket_path = directory, socket_path
            return
        raise IPCUnavailable('No safe writable short directory for worker IPC')

    def close(self):
        if self.directory is not None:
            shutil.rmtree(self.directory)
            self.directory = self.socket_path = None
            if self._evidence_base is not None:
                _write_worker_evidence(self._evidence_base, {**self._record, 'cleanup_complete':True})


class OuterIPCBase:
    """Allocation-owned base for the outer service container and inner workers."""
    def __init__(self):
        self.directory = None
        candidates = [('slurm_tmpdir', os.environ.get('SLURM_TMPDIR'))]
        candidates.append(('tmp', Path('/tmp').resolve()))
        for kind, value in candidates:
            if not value:
                continue
            try:
                parent = _shared_parent(value)
                if not _fits(parent/('bi-'+'x'*8)):
                    continue
                directory = Path(tempfile.mkdtemp(prefix='bi-', dir=parent))
            except (OSError, ValueError):
                continue
            try:
                directory.chmod(0o700)
                _private_base(str(directory))
                if not _fits(directory):
                    raise IPCUnavailable('Outer IPC base is too long')
            except BaseException as exc:
                shutil.rmtree(directory)
                if isinstance(exc, OSError):
                    raise IPCUnavailable('Outer IPC base initialization failed') from exc
                raise
            self.directory, self.kind = directory, kind
            return
        raise IPCUnavailable('No safe node-local outer IPC base', observed={'base_available':False})

    def close(self):
        if self.directory is not None:
            shutil.rmtree(self.directory)
            self.directory = None


def preflight():
    """Probe creation only; never claim this probe was a rendering worker."""
    ipc = WorkerIPC(purpose='probe')
    try:
        return {'transport':'AF_UNIX', 'base':str(ipc.directory.parent),
                'directory_mode':'0700', 'socket_path_bytes':len(os.fsencode(ipc.socket_path)),
                'durable_root_independent':True, 'lifetime':'worker_process',
                'outer_apptainer_bind':True, 'explicit_base':BASE_ENV in os.environ}
    finally:
        ipc.close()
