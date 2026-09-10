"""Worker IPC contracts: real private files, injected process/socket boundaries."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

import pytest

from blender_render import engine, render_worker_manager as manager
from test_jobs_api import payload


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    root = tmp_path / ('durable-' + 'x' * 160) / 'worker'
    w = manager.PersistentBlenderWorker(root_dir=root, blender_exe='fake',
        custom_script=engine.process_seal().root/'blender_custom_render.py',
        use_apptainer=False, apptainer_image='')
    monkeypatch.setattr(w, '_resolve_blender_executable', lambda:'fake')
    monkeypatch.setattr(w, '_wait_until_ready_locked', lambda **kw:None)
    monkeypatch.setattr(w, '_send_request_locked', lambda *a, **kw:{})
    monkeypatch.setattr(manager.os, 'killpg', lambda *a:None)
    w.launches = []

    class Process:
        pid = 987654
        exited = False
        forced = False
        killed = False
        waits = 0

        def poll(self):
            return 0 if self.exited else None

        def wait(self, timeout):
            assert Path(self.socket).parent.exists(), 'IPC removed before exit'
            self.waits += 1
            if self.forced and self.waits <= 2:
                raise subprocess.TimeoutExpired('fake', timeout)
            self.exited = True
            return 0

        def terminate(self):
            assert Path(self.socket).parent.exists()

        def kill(self):
            assert Path(self.socket).parent.exists()
            self.killed = True

    def launch(argv, **kwargs):
        p = Process()
        p.socket = argv[argv.index('--socket')+1]
        # A fake AF_UNIX bind retains the OS pathname constraint under the guard.
        assert len(os.fsencode(p.socket)) <= 100
        Path(p.socket).write_bytes(b'fake socket')
        w.launches.append((argv, kwargs, p))
        return p

    monkeypatch.setattr(manager.subprocess, 'Popen', launch)
    yield w
    w._stop_locked()


def test_long_durable_root_short_private_ipc_and_persistent_logs(worker):
    assert worker.status()['socket_path'] is None
    worker.ensure_started()
    socket = worker.socket_path
    assert len(os.fsencode(socket)) <= 100
    assert not socket.is_relative_to(worker.root_dir)
    assert stat.S_IMODE(socket.parent.stat().st_mode) == 0o700
    assert worker.stdout_log.parent == worker.stderr_log.parent == worker.root_dir
    assert all(p.exists() for p in (worker.stdout_log, worker.stderr_log))
    argv, kwargs, _ = worker.launches[0]
    assert Path(argv[argv.index('--socket')+1]) == socket
    assert Path(kwargs['stdout'].name).parent == worker.root_dir
    worker.stop()
    assert not socket.parent.exists()
    assert worker.status()['socket_path'] is None
    assert worker.stdout_log.exists() and worker.stderr_log.exists()


@pytest.mark.parametrize('phase', ['capture', 'command', 'environment', 'launch', 'ready', 'interrupted'])
def test_startup_failure_cleans_all_ephemeral_state(worker, monkeypatch, phase):
    paths = []
    def fail(*a, **kw):
        if worker.socket_path is not None:
            paths.append(worker.socket_path.parent)
        if phase == 'interrupted':
            raise KeyboardInterrupt()
        raise OSError('injected startup failure')
    if phase == 'capture':
        monkeypatch.setattr(engine.process_seal(), 'capture', fail)
    else:
        target, name = {
            'command': (worker, '_build_command'),
            'environment': (worker, '_subprocess_env'),
            'launch': (manager.subprocess, 'Popen'),
            'ready': (worker, '_wait_until_ready_locked'),
            'interrupted': (worker, '_wait_until_ready_locked'),
        }[phase]
        monkeypatch.setattr(target, name, fail)
    with pytest.raises((OSError, manager.WorkerNotReady, KeyboardInterrupt)):
        worker.ensure_started()
    assert worker.socket_path is None
    assert not any(path.exists() for path in paths)
    assert not list(worker.root_dir.glob('engine-*'))
    assert worker._process is None


def test_forced_stop_retains_ipc_until_child_exit(worker):
    worker.ensure_started()
    path = worker.socket_path
    process = worker._process
    process.forced = True
    worker.stop()
    assert process.killed and process.exited and process.waits == 3
    assert not path.parent.exists()
    assert worker.socket_path is None


def test_restart_never_adopts_stale_paths(worker):
    worker.root_dir.mkdir(parents=True)
    old = worker.root_dir/'worker.sock'
    old.write_text('unrelated stale socket')
    worker.ensure_started()
    first = worker.socket_path
    worker.stop()
    # Even a re-created old worker directory must never be adopted or removed.
    first.parent.mkdir(mode=0o700)
    first.write_text('stale')
    try:
        worker.ensure_started()
        assert worker.socket_path.parent != first.parent
        assert first.read_text() == 'stale'
        assert old.read_text() == 'unrelated stale socket'
    finally:
        first.unlink()
        first.parent.rmdir()


def test_abandoned_replacement_cleans_new_ipc(worker, monkeypatch):
    worker.ensure_started()
    first = worker.socket_path
    monkeypatch.setattr(worker, '_send_request_locked', lambda *a, **kw:{'status':'not-ready'})
    monkeypatch.setattr(worker, '_build_command', lambda:(_ for _ in ()).throw(OSError('replacement abandoned')))
    with pytest.raises((OSError, manager.WorkerNotReady)):
        worker.ensure_started()
    assert worker.socket_path is None and not first.parent.exists()
    assert not list(worker.root_dir.glob('engine-*'))


def test_outer_apptainer_explicitly_binds_ipc(worker, monkeypatch):
    worker.use_apptainer = True
    worker.apptainer_image = '/prepared/image.sif'
    monkeypatch.setattr(manager._bridge_runner, '_resolve_apptainer_executable', lambda:'apptainer')
    worker.ensure_started()
    argv = worker.launches[0][0]
    binds = [argv[i+1] for i, arg in enumerate(argv[:-1]) if arg == '--bind']
    assert str(worker.socket_path.parent)+':'+str(worker.socket_path.parent)+':rw' in binds
    assert worker.stdout_log.parent == worker.root_dir


def test_xdg_preference_and_unsafe_fallback(worker, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='c3-xdg-', dir='/tmp') as directory:
        base = Path(directory)
        monkeypatch.setenv('XDG_RUNTIME_DIR', directory)
        worker.ensure_started()
        assert worker.socket_path.parent.parent == base
        worker.stop()
        base.chmod(0o777)
        worker.ensure_started()
        assert worker.socket_path.parent.parent == Path('/tmp').resolve()


@pytest.mark.parametrize('kind', ['relative', 'symlink', 'long', 'missing', 'file'])
def test_invalid_xdg_is_not_used(worker, tmp_path, monkeypatch, kind):
    base = tmp_path/kind
    if kind == 'symlink':
        base.symlink_to(tmp_path, target_is_directory=True)
    elif kind == 'long':
        base = tmp_path/('x'*150)
        base.mkdir(mode=0o700)
    elif kind == 'file':
        base.write_text('not a directory')
    monkeypatch.setenv('XDG_RUNTIME_DIR', 'relative' if kind == 'relative' else str(base))
    worker.ensure_started()
    assert worker.socket_path.parent.parent == Path('/tmp').resolve()


@pytest.mark.parametrize('separator', [':', ','])
def test_unsafe_apptainer_xdg_separator_falls_back(worker, monkeypatch, separator):
    with tempfile.TemporaryDirectory(prefix='c3'+separator, dir='/tmp') as directory:
        monkeypatch.setenv('XDG_RUNTIME_DIR', directory)
        monkeypatch.setattr(manager._bridge_runner, '_resolve_apptainer_executable', lambda:'apptainer')
        worker.use_apptainer = True
        worker.apptainer_image = '/prepared/image.sif'
        try:
            worker.ensure_started()
            assert worker.socket_path.parent.parent == Path('/tmp').resolve()
            argv = worker.launches[0][0]
            assert str(worker.socket_path.parent)+':'+str(worker.socket_path.parent)+':rw' in argv
        finally:
            worker.stop()


def test_no_safe_ipc_fails_before_launch_with_typed_readiness(worker, monkeypatch):
    from blender_render import worker_ipc
    def denied(*a, **kw):
        raise PermissionError('no writable IPC storage')
    monkeypatch.setattr(worker_ipc.tempfile, 'mkdtemp', denied)
    with pytest.raises(manager.WorkerNotReady, match='IPC'):
        worker.ensure_started()
    assert not worker.launches and worker._process is None
    assert worker.socket_path is None
    assert not list(worker.root_dir.glob('engine-*'))


def test_dead_process_status_does_not_report_old_active_socket(worker):
    worker.ensure_started()
    worker._process.exited = True
    assert worker.status()['socket_path'] is None
    worker.stop()


def test_ipc_preflight_reports_effective_strategy_without_launch(monkeypatch):
    from blender_render.worker_ipc import preflight
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    result = preflight()
    assert result['transport'] == 'AF_UNIX'
    assert result['durable_root_independent'] is True
    assert result['directory_mode'] == '0700'
    assert result['socket_path_bytes'] <= 100
    assert result['base'] == str(Path('/tmp').resolve())
    assert result['outer_apptainer_bind'] is True


def test_ipc_failure_preserves_admitted_job_and_reservation(worker, tmp_path, monkeypatch, payload):
    from blender_render import worker_ipc
    from test_c3_operation_blocks import admitted
    store, service, source, job = admitted(tmp_path/'store', payload)
    def deny(*a, **kw):
        raise PermissionError('IPC storage denied')
    monkeypatch.setattr(worker_ipc.tempfile, 'mkdtemp', deny)
    monkeypatch.setattr(service.backend, 'render', lambda *a:worker.ensure_started())
    row = store.claim()
    before = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    try:
        service._execute(row)
        after = dict(store.db.execute('SELECT * FROM jobs').fetchone())
        assert after['blocked_condition'] == 'compatibility_blocked'
        assert after['state'] == 'queued'
        for key in ('id', 'input_id', 'execution', 'reserved', 'charged', 'remote_id'):
            assert after[key] == before[key]
        assert not worker.launches
    finally:
        store.close()
