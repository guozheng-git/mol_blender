"""Opt-in, per-user local Blender supervisor. No remote execution or installers."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blender_render.config import config_root
from blender_render.developer import (digest_file, isolated_environment, snapshot_source,
                                      source_digest, source_files, stage_runtime_wheels)
from blender_render.warm_protocol import receive, request, seconds, send

START_TIMEOUT = 180


def _private(path):
    if path.is_symlink():
        raise ValueError('Warm worker directories must not be symlinks')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Warm worker directory must be owned by you and mode 0700: ' + str(path))
    return path


def _registry():
    path = config_root().absolute() / 'warm-worker'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return _private(path)


def _atomic(path, document):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, 0o600)
        json.dump(document, stream, allow_nan=False)
    temporary.replace(path)


@contextmanager
def _lock(registry):
    descriptor = os.open(registry / 'start.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def _session(registry):
    path = registry / 'session.json'
    if not path.exists():
        return None
    session = json.loads(path.read_text())
    root = _private(Path(session['root']))
    if not root.name.startswith('br-warm-') or session['socket'] != str(root / 'worker.sock'):
        raise ValueError('Invalid warm worker session path')
    cfg = json.loads((root / 'worker.json').read_text())
    if cfg['worker_id'] != session['id']:
        raise ValueError('Warm worker session identity changed')
    return session


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _existing(registry):
    try:
        session = _session(registry)
    except FileNotFoundError:
        if not (registry / 'session.json').exists():
            return None  # Normal idle shutdown raced this observation.
        raise
    if not session:
        return None
    try:
        return request(session, 'status')
    except (ConnectionError, FileNotFoundError):
        if _alive(session['pid']):
            return {**session, 'status': session.get('status', 'unreachable')}
        if _alive(session['blender_pid']):
            raise RuntimeError('Warm supervisor exited; waiting for its Blender process to stop. Retry shortly.')
        # An unclean exit may leave only this verified, private source snapshot.
        shutil.rmtree(session['root'])
        (registry / 'session.json').unlink(missing_ok=True)
        return None


def status():
    return _existing(_registry()) or {'status': 'stopped'}


def _fingerprint(settings):
    for key in ('blender', 'ccd'):
        if not settings[key].is_file():
            raise ValueError('Required local runtime file is missing: ' + str(settings[key]))
    if not settings['runtime_paths'] or not all(p.is_dir() for p in settings['runtime_paths']):
        raise ValueError('Provide existing runtime dependency directories')
    files = source_files(Path(__file__).resolve().parent)
    wheels = settings.get('runtime_wheels')
    inputs = {'source': source_digest(files), 'blender': str(settings['blender']),
              'blender_sha256': digest_file(settings['blender']),
              'ccd_sha256': digest_file(settings['ccd']),
              'runtime_paths': list(map(str, settings['runtime_paths'])),
              'wheels': {p.name: digest_file(p) for p in sorted(wheels.glob('*.whl'))} if wheels else {},
              'resources': {k: os.environ.get(k) for k in
                            ('CUDA_VISIBLE_DEVICES', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'APPTAINER_CONTAINER')}}
    return source_digest(inputs), inputs


def start(*, idle_timeout=900, settings=None):
    """Explicitly start, or attach to an identical worker. Never replace a live worker."""
    from blender_render.local import runtime_settings
    idle_timeout = seconds(idle_timeout)
    settings = settings or runtime_settings()
    key, inputs = _fingerprint(settings)
    registry = _registry()
    with _lock(registry):
        current = _existing(registry)
        if current:
            if current['status'] not in ('ready', 'busy'):
                raise RuntimeError('Warm worker is stopping or unreachable; retry after shutdown or inspect worker.log')
            if current['key'] != key:
                raise RuntimeError('Warm worker source/runtime changed. Run blender-render worker stop, then retry.')
            return current
        # Keep Unix socket names short even when config or Darwin TMPDIR paths are long.
        base = tempfile.gettempdir()
        if len(os.fsencode(base)) > 50:
            base = '/tmp'
        root = Path(tempfile.mkdtemp(prefix='br-warm-', dir=base)).resolve()
        process = None
        try:
            identity = snapshot_source(Path(__file__).resolve().parent.parent, root / 'source')
            if identity['sha256'] != inputs['source']:
                raise RuntimeError('Source changed during warm worker startup; retry once edits are saved')
            paths = list(map(str, settings['runtime_paths']))
            if settings.get('runtime_wheels'):
                captured = stage_runtime_wheels(settings['runtime_wheels'], root / 'runtime')
                if captured != inputs['wheels']:
                    raise RuntimeError('Runtime wheels changed during startup')
                paths.insert(0, str(root / 'runtime'))
            shutil.copyfile(settings['ccd'], root / 'components.bcif')
            if digest_file(root / 'components.bcif') != inputs['ccd_sha256']:
                raise RuntimeError('CCD changed during startup')
            cfg = {'root': str(root), 'registry': str(registry), 'worker_id': uuid.uuid4().hex,
                   'identity': identity, 'key': key, 'idle_timeout': idle_timeout,
                   'blender': str(settings['blender']), 'runtime_paths': paths,
                   'ccd': str(root / 'components.bcif'), 'ccd_sha256': inputs['ccd_sha256']}
            _atomic(root / 'worker.json', cfg)
            with (registry / 'worker.log').open('w') as log:
                os.chmod(registry / 'worker.log', 0o600)
                process = subprocess.Popen([sys.executable, '-B', '-I',
                    str(root / 'source/blender_render/warm_worker.py'), str(root / 'worker.json')],
                    cwd=root, env=isolated_environment(root), stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + START_TIMEOUT + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Warm Blender startup failed; see ' + str(registry / 'worker.log'))
                current = _existing(registry)
                if current:
                    return current
                time.sleep(.1)
            raise TimeoutError('Warm Blender startup timed out; see ' + str(registry / 'worker.log'))
        except BaseException:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            shutil.rmtree(root, ignore_errors=True)
            (registry / 'session.json').unlink(missing_ok=True)
            raise


def stop(*, force=False):
    registry = _registry()
    with _lock(registry):
        current = _existing(registry)
        if not current:
            return {'status': 'stopped'}
        if current['status'] == 'unreachable':
            raise RuntimeError('Warm worker supervisor is unreachable; inspect worker.log')
        if current['status'] != 'stopping':
            request(current, 'stop', force=force)
        deadline = time.monotonic() + 15
        while Path(current['root']).exists() and time.monotonic() < deadline:
            time.sleep(.05)
        if Path(current['root']).exists():
            raise RuntimeError('Warm worker is still stopping; inspect worker.log')
        return {'status': 'stopped', 'id': current['id']}


class RemoteBlender:
    def __init__(self, session):
        self.session = session

    def submit_render(self, *, structure_path, view_json, style_json, output_png, run_manifest, timeout):
        from blender_render.errors import WorkerError
        try:
            return request(self.session, 'render', timeout=timeout + 10, key=self.session['key'],
                           render_timeout=timeout, paths={
                               'structure': str(structure_path), 'view-json': str(view_json),
                               'style-json': str(style_json), 'output-png': str(output_png),
                               'run-manifest': str(run_manifest)})
        except (OSError, ValueError, RuntimeError) as exc:
            raise WorkerError('Warm render failed; no automatic replay: ' + str(exc)) from exc


class Supervisor:
    def __init__(self, cfg, process, stream):
        self.cfg, self.process, self.stream = cfg, process, stream
        self.busy = threading.Lock()
        self.stopping = threading.Event()
        self.last_activity = time.monotonic()
        self.completed = 0
        self.session = {'id': cfg['worker_id'], 'pid': os.getpid(), 'blender_pid': process.pid,
                        'root': cfg['root'], 'socket': str(Path(cfg['root']) / 'worker.sock'),
                        'key': cfg['key'], 'source_sha256': cfg['identity']['sha256'],
                        'idle_timeout': cfg['idle_timeout']}

    def handle(self, message):
        if message.get('id') != self.session['id']:
            raise ValueError('Wrong warm worker session')
        action = message.get('action')
        if action == 'status':
            return {**self.session, 'status': 'stopping' if self.stopping.is_set() else
                    ('busy' if self.busy.locked() else 'ready'), 'completed': self.completed}
        if action == 'stop':
            if self.busy.locked() and not message.get('force', False):
                raise RuntimeError('Worker is rendering; wait or explicitly use worker stop --force')
            self.stopping.set()
            return {'status': 'stopping'}
        if action != 'render' or self.stopping.is_set():
            raise ValueError('Worker is stopping or the action is unsupported')
        if message.get('key') != self.session['key']:
            raise ValueError('Warm worker source/runtime mismatch; restart the worker')
        timeout = seconds(message.get('render_timeout'), minimum=.001)
        paths = message.get('paths')
        expected = {'structure', 'view-json', 'style-json', 'output-png', 'run-manifest'}
        if not isinstance(paths, dict) or set(paths) != expected or any(
                not isinstance(p, str) or not Path(p).is_absolute() for p in paths.values()):
            raise ValueError('Expected absolute local render paths')
        if not self.busy.acquire(blocking=False):
            raise RuntimeError('Warm worker is busy; this request was not submitted')
        try:
            if self.stopping.is_set():
                raise RuntimeError('Warm worker is stopping; request was not submitted')
            self.stream.settimeout(timeout)
            arguments = [value for key, path in paths.items() for value in ('--' + key, path)]
            send(self.stream, {'action': 'render', 'arguments': arguments})
            result = receive(self.stream)
            if 'error' in result:
                raise RuntimeError(result['error'])
            self.completed += 1
            return result
        except BaseException:
            # An uncertain/failed Blender process is retired, never replayed.
            self.stopping.set()
            raise
        finally:
            self.last_activity = time.monotonic()
            self.busy.release()


def serve(config_path):
    os.umask(0o077)
    cfg = json.loads(Path(config_path).read_text())
    root, registry = Path(cfg['root']), Path(cfg['registry'])
    process, server, control, child = None, None, None, None
    try:
        control, child = socket.socketpair()
        command = [cfg['blender'], '--background', '--factory-startup', '--disable-autoexec',
                   '--python-use-system-env', '--python-exit-code', '1', '--python',
                   str(root / 'source/blender_render/warm_blender.py')]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root / 'source'), *cfg['runtime_paths']]),
                   BLENDER_RENDER_DEV_CONFIG=str(config_path), BLENDER_RENDER_WARM_FD=str(child.fileno()),
                   PRESS_BIOTITE_CCD_PATH=cfg['ccd'])
        process = subprocess.Popen(command, cwd=root / 'source', env=env, pass_fds=(child.fileno(),),
                                   stdin=subprocess.DEVNULL)
        child.close()
        control.settimeout(START_TIMEOUT)
        ready = receive(control)
        if not ready.get('ready') or ready.get('source_sha256') != cfg['identity']['sha256']:
            raise RuntimeError('Warm Blender failed its readiness check: ' + str(ready))
        supervisor = Supervisor(cfg, process, control)
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                try:
                    result = supervisor.handle(receive(self.request))
                except Exception as exc:
                    result = {'error': str(exc)}
                try:
                    send(self.request, result)
                except OSError:
                    pass  # Never submit again after a lost client connection.
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
        server = Server(supervisor.session['socket'], Handler)
        os.chmod(supervisor.session['socket'], 0o600)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _atomic(registry / 'session.json', supervisor.session)
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: supervisor.stopping.set())
        while not supervisor.stopping.wait(.2):
            if process.poll() is not None:
                break
            if cfg['idle_timeout'] and time.monotonic() - supervisor.last_activity >= cfg['idle_timeout']:
                # Synchronize expiry with admission; an active render cannot expire.
                if supervisor.busy.acquire(blocking=False):
                    try:
                        if time.monotonic() - supervisor.last_activity >= cfg['idle_timeout']:
                            supervisor.stopping.set()
                    finally:
                        supervisor.busy.release()
        _atomic(registry / 'session.json', {**supervisor.session, 'status': 'stopping'})
        if not supervisor.busy.locked() and process.poll() is None:
            try:
                send(control, {'action': 'stop'})
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        if server:
            server.shutdown()
            server.server_close()
        if control:
            control.close()
        if child:
            child.close()
        path = registry / 'session.json'
        if path.exists() and json.loads(path.read_text()).get('id') == cfg['worker_id']:
            path.unlink()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    serve(sys.argv[1])
