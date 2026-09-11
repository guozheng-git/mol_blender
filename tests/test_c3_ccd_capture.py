"""CCD capture and real daemon boundaries with injected Blender/Biotite only."""
import ast
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

CCD = b'locked CCD fixture'
SHA = hashlib.sha256(CCD).hexdigest()


@pytest.fixture
def runtime(tmp_path):
    from blender_render import worker_ccd
    source = tmp_path/'components.bcif'
    source.write_bytes(CCD)
    ccd = SimpleNamespace(_CCD_FILE=source)
    ccd.set_ccd_path = lambda path:setattr(ccd, '_CCD_FILE', Path(path))
    try:
        yield worker_ccd, source, ccd
    finally:
        worker_ccd.close()


@pytest.mark.parametrize('bad', ['symlink', 'directory', 'missing', 'corrupt'])
def test_bad_source_prevents_capture_and_readiness(runtime, tmp_path, bad):
    from blender_render.engine import RuntimeUnavailable
    worker, source, ccd = runtime
    if bad == 'symlink':
        target = tmp_path/'other.bcif'
        target.write_bytes(CCD)
        source.unlink()
        source.symlink_to(target)
    elif bad == 'directory':
        source.unlink()
        source.mkdir()
    elif bad == 'missing': source.unlink()
    else: source.write_bytes(b'corrupt')
    with pytest.raises(RuntimeUnavailable): worker.initialize(parent=tmp_path, ccd_module=ccd, expected=SHA)
    assert worker.observation()['verified'] is False
    assert not list(tmp_path.glob('worker-ccd-*'))


@pytest.mark.parametrize('behavior', ['raise', 'ignore'])
def test_activation_must_be_verified(runtime, tmp_path, behavior):
    from blender_render.engine import RuntimeUnavailable
    worker, source, ccd = runtime
    def setter(path):
        if behavior == 'raise': raise OSError('cannot activate')
    ccd.set_ccd_path = setter
    with pytest.raises(RuntimeUnavailable): worker.initialize(parent=tmp_path, ccd_module=ccd, expected=SHA)
    assert worker.observation()['verified'] is False
    assert not list(tmp_path.glob('worker-ccd-*'))
    assert source.read_bytes() == CCD


@pytest.mark.parametrize('swap', ['after_startup', 'after_final_preflight'])
def test_daemon_renderer_uses_only_captured_ccd(runtime, tmp_path, monkeypatch, swap):
    from blender_render import engine, blender_worker_daemon as daemon
    worker, source, ccd = runtime
    capture = worker.initialize(parent=tmp_path, ccd_module=ccd, expected=SHA)
    captured = Path(worker.observation()['path'])
    assert captured != source and captured.stat().st_mode & 0o777 == 0o400
    assert captured.parent.stat().st_mode & 0o777 == 0o700
    assert ccd._CCD_FILE == captured and captured.read_bytes() == CCD
    monkeypatch.setitem(engine.ACTION_MODULES, 'render', ('worker_ccd',))
    monkeypatch.setattr(engine, '_runtime_versions', lambda:{'versions':{}, 'verified':True, 'mismatches':[]})
    # Run the real renderer's guard without loading Blender modules.
    definition = next(node for node in ast.parse(engine.process_seal().inputs['blender_custom_render.py']).body
                      if isinstance(node, ast.FunctionDef) and node.name == '_ensure_biotite_ccd_database')
    namespace = {'Path':Path}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), 'renderer_ccd_guard', 'exec'), namespace)
    def replace_source():
        replacement = tmp_path/'replacement'
        replacement.write_bytes(b'changed mutable source')
        os.replace(replacement, source)
    if swap == 'after_startup': replace_source()
    observed = []
    def render(args):
        # This executes after the daemon's final preflight. The actual renderer
        # entry guard must use the private capture even if source changes here.
        if swap == 'after_final_preflight': replace_source()
        namespace['_ensure_biotite_ccd_database'](before_execution=True)
        observed.append(ccd._CCD_FILE.read_bytes())  # Biotite's effective input.
        Path(args.run_manifest).write_text('{}')
    renderer = SimpleNamespace(_render=render)
    result = daemon._render_job(renderer, {'expected_engine':engine.engine_identity(),
        'structure_path':'fixture.cif', 'view_json':'view.json', 'style_json':'style.json',
        'output_png':str(tmp_path/'render.png'), 'run_manifest':str(tmp_path/'manifest.json')})
    assert result['status'] == 'completed' and observed == [CCD]
    assert source.read_bytes() != CCD and worker.observation()['verified']
    capture.close()
    assert not captured.exists() and worker.observation()['verified'] is False


def test_capture_corruption_or_path_change_blocks_preflight(runtime, tmp_path, monkeypatch):
    from blender_render import engine, blender_worker_daemon as daemon
    worker, source, ccd = runtime
    worker.initialize(parent=tmp_path, ccd_module=ccd, expected=SHA)
    captured = ccd._CCD_FILE
    monkeypatch.setattr(engine, '_runtime_versions', lambda:{'versions':{}, 'verified':True, 'mismatches':[]})
    renderer = SimpleNamespace(_render=lambda *a:pytest.fail('Invalid captured CCD rendered'))
    ccd._CCD_FILE = source
    with pytest.raises(engine.RuntimeUnavailable): daemon._render_job(renderer, {'expected_engine':engine.engine_identity()})
    ccd._CCD_FILE = captured
    captured.chmod(0o600)
    captured.write_bytes(b'changed private copy')
    captured.chmod(0o400)
    with pytest.raises(engine.RuntimeUnavailable): daemon._render_job(renderer, {'expected_engine':engine.engine_identity()})
    assert not getattr(renderer, '_bridge_dispatch_count', 0)


@pytest.mark.parametrize('activation_ok', [True, False])
def test_actual_daemon_captures_before_readiness_and_cleans_up(runtime, tmp_path, monkeypatch, activation_ok):
    import sys
    from types import ModuleType
    from blender_render import engine, blender_worker_daemon as daemon
    worker, source, ccd = runtime
    monkeypatch.setitem(engine.ACTION_MODULES, 'render', ('worker_ccd',))
    monkeypatch.setattr(engine, '_runtime_versions', lambda:{'versions':{}, 'verified':True, 'mismatches':[]})
    # Only third-party import boundaries are injected; daemon startup, capture,
    # activation, health and teardown execute in process.
    for name in ('molecularnodes', 'molecularnodes.nodes', 'molecularnodes.nodes.styles',
                 'biotite', 'biotite.structure', 'biotite.structure.io', 'biotite.structure.io.pdbx'):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    renderer = SimpleNamespace(_configure_molecularnodes_import_path=lambda:None,
                               _ensure_molecularnodes_ready=lambda *a:None)
    monkeypatch.setattr(daemon, '_load_renderer_module', lambda *a:renderer)
    initialize = worker.initialize
    captured = []
    if not activation_ok: ccd.set_ccd_path = lambda path:None
    def init(**kwargs):
        assert kwargs['parent'] == tmp_path/'capture-root'
        result = initialize(**kwargs, ccd_module=ccd, expected=SHA)
        captured.append(ccd._CCD_FILE)
        return result
    monkeypatch.setattr(worker, 'initialize', init)
    requests = iter([{'action':'ping'}, {'action':'shutdown'}])
    replies = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class Socket:
        def bind(self, path):
            assert worker.observation()['verified'] is activation_ok
            Path(path).touch()
        def listen(self, *a): pass
        def settimeout(self, *a): pass
        def accept(self): return Connection(), None
        def close(self): pass
    monkeypatch.setattr(daemon, 'socket', SimpleNamespace(AF_UNIX=1, SOCK_STREAM=1, socket=lambda *a:Socket(), timeout=TimeoutError))
    monkeypatch.setattr(daemon, '_read_json_request', lambda *a:next(requests))
    monkeypatch.setattr(daemon, '_write_json_response', lambda conn, value:replies.append(value))
    (tmp_path/'capture-root').mkdir()
    args = SimpleNamespace(socket=tmp_path/'worker.sock', custom_script=engine.process_seal().root/'blender_custom_render.py',
                           ccd_root=tmp_path/'capture-root', idle_timeout=0)
    assert daemon._run_server(args) == 0
    if activation_ok:
        assert replies[0]['status'] == 'ready' and replies[0]['runtime']['ccd']['verified']
        assert len(captured) == 1 and not captured[0].exists()
    else:
        assert replies[0]['condition'] == 'compatibility_blocked' and replies[0]['before_execution']
        assert replies[0]['compatibility_diagnostic']['reason'] == 'ccd_activation_failed'
    assert worker.observation()['verified'] is False
    assert not list((tmp_path/'capture-root').iterdir())
