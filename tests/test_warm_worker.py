"""Real supervisor/IPC/lifecycle checks; a tiny child replaces only Blender here.

Actual GPU rendering is covered separately by scripts/validate_warm_local.py.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time

import pytest

from blender_render import cli, developer, local, local_bridge, pymol_bridge_plugin, warm_worker
from blender_render.errors import WorkerError
from blender_render.warm_protocol import MAX_MESSAGE, receive, seconds, send


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'config'))
    blender = tmp_path / 'blender'
    blender.write_text('#!' + sys.executable + '\n' + '''import json, os, socket, sys, time
from pathlib import Path
cfg = json.loads(Path(os.environ['BLENDER_RENDER_DEV_CONFIG']).read_text())
sys.path.insert(0, str(Path(cfg['root']) / 'source'))
from blender_render.warm_protocol import send, receive
stream = socket.socket(fileno=int(os.environ['BLENDER_RENDER_WARM_FD']))
send(stream, {'ready': True, 'source_sha256': cfg['identity']['sha256']})
count = 0
while True:
    message = receive(stream)
    if message['action'] == 'stop': break
    args = message['arguments']
    paths = dict(zip(args[::2], args[1::2]))
    behavior = Path(paths['--structure']).read_text()
    count += 1
    if behavior == 'hang': time.sleep(30)
    if behavior == 'crash': os._exit(4)
    if behavior == 'error':
        send(stream, {'error': 'fixture render failure'})
        break
    send(stream, {'pid': os.getpid(), 'request_index': count, 'reused_process': count > 1})
''')
    blender.chmod(0o700)
    ccd = tmp_path / 'components.bcif'
    ccd.write_bytes(b'fixture')
    deps = tmp_path / 'deps'
    deps.mkdir()
    settings = {'blender': blender, 'ccd': ccd, 'runtime_paths': [deps], 'runtime_wheels': None}
    yield settings
    warm_worker.stop(force=True)


def render(session, tmp_path, behavior='render', timeout=5):
    structure = tmp_path / (behavior + '.cif')
    structure.write_text(behavior)
    return warm_worker.RemoteBlender(session).submit_render(
        structure_path=structure, view_json=tmp_path / 'view.json', style_json=tmp_path / 'style.json',
        output_png=tmp_path / 'out.png', run_manifest=tmp_path / 'manifest.json', timeout=timeout)


def until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    pytest.fail('Lifecycle deadline expired')


def test_explicit_worker_reuses_process_and_removes_snapshot(runtime, tmp_path):
    session = warm_worker.start(settings=runtime)
    assert warm_worker.start(settings=runtime)['id'] == session['id']
    first = render(session, tmp_path)
    second = render(session, tmp_path)
    assert first['pid'] == second['pid'] == session['blender_pid']
    assert not first['reused_process'] and second['reused_process']
    assert warm_worker.status()['completed'] == 2
    assert (Path(session['root']) / 'worker.sock').stat().st_mode & 0o777 == 0o600
    warm_worker.stop()
    assert not Path(session['root']).exists()
    assert warm_worker.status() == {'status': 'stopped'}
    assert not warm_worker._alive(session['blender_pid'])


def test_concurrent_start_does_not_duplicate_worker(runtime):
    with concurrent.futures.ThreadPoolExecutor(2) as executor:
        a, b = [executor.submit(warm_worker.start, settings=runtime) for _ in range(2)]
        assert a.result()['id'] == b.result()['id']


def test_changed_runtime_or_compute_allocation_requires_restart(runtime, monkeypatch):
    session = warm_worker.start(settings=runtime)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', 'different-allocation')
    with pytest.raises(RuntimeError, match='source/runtime changed'):
        warm_worker.start(settings=runtime)
    assert warm_worker.status()['id'] == session['id']
    assert warm_worker.status()['completed'] == 0


def test_status_polling_does_not_prevent_idle_shutdown(runtime):
    session = warm_worker.start(settings=runtime, idle_timeout=.5)
    until(lambda: warm_worker.status()['status'] == 'stopped')
    assert not Path(session['root']).exists()
    assert not warm_worker._alive(session['blender_pid'])


@pytest.mark.parametrize('behavior', ['hang', 'crash', 'error'])
def test_failure_retires_child_without_replay(runtime, tmp_path, behavior):
    session = warm_worker.start(settings=runtime)
    with pytest.raises(WorkerError, match='no automatic replay'):
        render(session, tmp_path, behavior, timeout=.2)
    until(lambda: not Path(session['root']).exists())
    assert not warm_worker._alive(session['blender_pid'])
    replacement = warm_worker.start(settings=runtime)
    assert replacement['id'] != session['id']
    assert replacement['completed'] == 0


def test_busy_worker_rejects_second_job_and_force_stop_interrupts(runtime, tmp_path):
    session = warm_worker.start(settings=runtime, idle_timeout=.3)
    with concurrent.futures.ThreadPoolExecutor(1) as executor:
        active = executor.submit(render, session, tmp_path, 'hang')
        until(lambda: warm_worker.status()['status'] == 'busy')
        time.sleep(.4)
        assert warm_worker.status()['status'] == 'busy'  # Idle expiry excludes active work.
        with pytest.raises(WorkerError, match='busy'):
            render(session, tmp_path)
        with pytest.raises(RuntimeError, match='rendering'):
            warm_worker.stop()
        warm_worker.stop(force=True)
        with pytest.raises(WorkerError):
            active.result(timeout=3)
    assert not Path(session['root']).exists()


def test_private_worker_registry_required(runtime, tmp_path):
    registry = tmp_path / 'config' / 'warm-worker'
    registry.mkdir(parents=True, mode=0o755)
    registry.chmod(0o755)
    with pytest.raises(ValueError, match='0700'):
        warm_worker.start(settings=runtime)
    registry.chmod(0o700)


def test_protocol_bounds_json_and_refuses_nonfinite_values():
    left, right = socket.socketpair()
    try:
        send(left, {'value': 'roundtrip'})
        assert receive(right) == {'value': 'roundtrip'}
        left.sendall(struct.pack('!I', MAX_MESSAGE + 1))
        with pytest.raises(ValueError, match='length'):
            receive(right)
        with pytest.raises(ValueError):
            send(left, {'value': float('nan')})
        for value in (float('nan'), float('inf'), -1):
            with pytest.raises(ValueError):
                seconds(value)
    finally:
        left.close()
        right.close()


def test_warm_cli_is_opt_in_and_does_not_become_style_option(monkeypatch):
    calls = []
    monkeypatch.setattr(local, 'render_file', lambda *a, **kw: calls.append(kw))
    cli.execute(cli.parser().parse_args(['render', 'test.cif']))
    cli.execute(cli.parser().parse_args(['render', 'test.cif', '--warm']))
    assert not calls[0]['warm'] and calls[1]['warm']
    assert 'warm' not in calls[1]['options']


def test_pymol_refuses_combined_slurm_and_warm_before_export(monkeypatch):
    monkeypatch.setattr(pymol_bridge_plugin, 'build_render_payload', lambda *a, **k: pytest.fail('exported'))
    with pytest.raises(ValueError, match='local worker'):
        pymol_bridge_plugin.bridge_render(cluster='lab', warm=1)


def test_embedded_bridge_passes_warm_only_when_requested(runtime, monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(local_bridge.subprocess, 'run', lambda args, **kw:
                        calls.append(args) or SimpleNamespace(returncode=0, stdout='{}'))
    local_bridge.render_payload({}, output='first.png', options={}, warm=True)
    local_bridge.render_payload({}, output='second.png', options={})
    assert '--warm' in calls[0] and '--warm' not in calls[1]


def test_snapshot_mismatch_never_contacts_worker(runtime, tmp_path):
    bundle = tmp_path / 'bundle.json'
    bundle.write_text('{}')
    with pytest.raises(RuntimeError, match='source changed'):
        developer.preview(checkout=Path(developer.__file__).parent.parent, bundle=bundle, options={},
                          catalog_dir=tmp_path, output=tmp_path / 'out',
                          warm_worker={'source_sha256': 'stale'}, **runtime)
