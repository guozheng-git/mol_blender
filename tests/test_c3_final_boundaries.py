"""Final independent review: admission, worker IPC and build/control seams."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_jobs_api import payload, Compute
from test_c3_operation_blocks import admitted
from test_c3_integrity import package


def test_latch_during_validation_prevents_durable_admission(tmp_path, payload, monkeypatch):
    from blender_render import service as services
    from blender_render.engine import RestartRequired
    from blender_render.jobs import JobStore
    store = JobStore(tmp_path)
    service = services.RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    original = services.prepare_options
    def validation(*a, **k):
        result = original(*a, **k)
        store.process_blocked = lambda:True
        return result
    monkeypatch.setattr(services, 'prepare_options', validation)
    with pytest.raises(RestartRequired): service.submit('a', source, {}, 'blocked')
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    store.close()


def test_latched_conversion_cannot_reserve(tmp_path, payload):
    from blender_render.engine import RestartRequired
    store, service, source, job = admitted(tmp_path, payload)
    store.claim()
    store.process_blocked = lambda:True
    before = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    with pytest.raises(RestartRequired): store.reserve_conversion('a', job_id=job['job_id'])
    assert store.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0] == 0
    assert dict(store.db.execute('SELECT * FROM jobs').fetchone()) == before
    store.close()


def test_import_endpoint_preserves_integrity_error(tmp_path):
    from blender_render.engine import RestartRequired
    from blender_render.jobs import JobStore
    from blender_render.service import RenderService
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    def convert(*a): raise RestartRequired('restart_required')
    with pytest.raises(RestartRequired):
        service.import_file('a', b'public fixture', '.pdb', 'public.pdb', convert)
    store.close()


@pytest.mark.parametrize('phase', ['daemon', 'renderer_entry'])
def test_warm_ccd_loss_is_retained_through_worker_ipc(tmp_path, payload, monkeypatch, phase, request):
    from blender_render import engine, blender_worker_daemon as daemon
    from blender_render.render_worker_manager import PersistentBlenderWorker
    store, service, source, job = admitted(tmp_path/'store', payload)
    worker = PersistentBlenderWorker(root_dir=tmp_path/'worker', blender_exe='unused',
        custom_script=engine.process_seal().root/'blender_custom_render.py', use_apptainer=False, apptainer_image='')
    monkeypatch.setattr(worker, '_ensure_started_locked', lambda:None)
    monkeypatch.setattr(engine, 'runtime_observation', lambda:{'verified':False, 'ccd':{'verified':False}})
    renderer = SimpleNamespace(_render=lambda *a:pytest.fail('CCD failure invoked renderer'))
    if phase == 'renderer_entry':
        import argparse
        import ast
        import hashlib
        # Execute the actual entry function and CCD guard without importing bpy.
        # The failing guard must exit before any scene/runtime body is reached.
        script = engine.process_seal().inputs['blender_custom_render.py'].decode()
        definitions = [node for node in ast.parse(script).body if isinstance(node, ast.FunctionDef)
                       and node.name in {'_render', '_ensure_biotite_ccd_database'}]
        namespace = {'argparse':argparse, 'Path':Path}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), 'sealed_renderer_entry', 'exec'), namespace)
        renderer._render = namespace['_render']
        # The Blender-only preload boundary is replaced; integrity checks and
        # the real renderer entry/CCD guard remain active in this process.
        monkeypatch.setitem(engine.ACTION_MODULES, 'render', ())
        ccd = tmp_path/'components.bcif'
        ccd.write_bytes(b'ccd')
        from blender_render import worker_ccd
        ccd_module = SimpleNamespace(_CCD_FILE=ccd)
        ccd_module.set_ccd_path = lambda value:setattr(ccd_module, '_CCD_FILE', Path(value))
        capture = worker_ccd.initialize(parent=tmp_path, ccd_module=ccd_module, expected=hashlib.sha256(b'ccd').hexdigest())
        request.addfinalizer(capture.close)
        monkeypatch.setattr(engine, 'runtime_observation', lambda:{'verified':True})
        protect = engine.protect
        checks = []
        def corrupt_after_preflight(action):
            protect(action)
            if action == 'render':
                checks.append(action)
                if len(checks) == 2:
                    ccd_module._CCD_FILE.chmod(0o600)
                    ccd_module._CCD_FILE.write_bytes(b'corrupt after daemon preflight')
                    ccd_module._CCD_FILE.chmod(0o400)
        monkeypatch.setattr(engine, 'protect', corrupt_after_preflight)
    def ipc(request, **kwargs):
        if request['action'] == 'ping':
            return {'status':'ready', 'engine':engine.engine_identity(), 'runtime':{'verified':True}}
        try:
            return daemon._render_job(renderer, request)
        except Exception as exc:
            return daemon._error_response(exc)
    monkeypatch.setattr(worker, '_send_request_locked', ipc)
    class Backend:
        def render(self, request, identifier, cancelled):
            return worker.submit_render(structure_path=tmp_path/'structure.cif', view_json=tmp_path/'view.json',
                style_json=tmp_path/'style.json', output_png=tmp_path/'render.png', run_manifest=tmp_path/'manifest.json', timeout=10)
    service.backend = Backend()
    service._execute(store.claim())
    state = store.get('a', job['job_id'])
    assert state['status'] == 'queued' and state['condition'] == 'compatibility_blocked'
    assert getattr(renderer, '_bridge_dispatch_count', 0) == (phase == 'renderer_entry')
    assert store.input('a', source)
    row = store.db.execute('SELECT * FROM jobs').fetchone()
    assert row['execution'] and row['reserved'] > 0 and row['charged'] == 0
    store.close()


def test_cold_runtime_failure_reports_typed_readiness(tmp_path, monkeypatch):
    from blender_render import blender_worker_daemon as daemon, engine
    args = SimpleNamespace(socket=tmp_path/'worker.sock', custom_script=engine.process_seal().root/'blender_custom_render.py', idle_timeout=0)
    def missing(*a): raise ImportError('CCD/runtime unavailable')
    monkeypatch.setattr(daemon, '_load_renderer_module', missing)
    requests = iter([{'action':'ping'}, {'action':'shutdown'}])
    replies = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class Socket:
        def bind(self, path): Path(path).touch()
        def listen(self, *a): pass
        def settimeout(self, *a): pass
        def accept(self): return Connection(), None
        def close(self): pass
    monkeypatch.setattr(daemon, 'socket', SimpleNamespace(AF_UNIX=1, SOCK_STREAM=1, socket=lambda *a:Socket(), timeout=TimeoutError))
    monkeypatch.setattr(daemon, '_read_json_request', lambda *a:next(requests))
    monkeypatch.setattr(daemon, '_write_json_response', lambda conn, value:replies.append(value))
    assert daemon._run_server(args) == 0
    assert replies[0]['condition'] == 'compatibility_blocked' and replies[0]['before_execution']


def test_administration_uses_captured_helper_despite_remote_damage(tmp_path, monkeypatch):
    import io
    import sys
    import runpy
    from blender_render import engine
    root = package(tmp_path)
    (root/'engine-manifest.json').write_text(json.dumps(engine.generate_manifest(root)))
    (root/'engine.py').write_text('# changed installed code')
    monkeypatch.setattr(sys, 'argv', ['helper','status'])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'profile':{'registry':str(tmp_path/'absent')}})))
    def unexpected(*a, **k): pytest.fail('Passive action loaded remote package')
    monkeypatch.setattr(runpy, 'run_path', unexpected)
    # Uses captured stdlib administrative code. The absent registry performs no
    # socket, scheduler or provider action; any accidental attempt is audited.
    exec(engine.remote_bootstrap(root, check=False), {'__name__':'__main__'})


def test_modal_image_snapshot_uses_shared_inventory(tmp_path):
    from blender_render import engine, modal_boundaries
    root = package(tmp_path)
    (root/'render_outputs').mkdir()
    (root/'render_outputs'/'private.json').write_bytes(b'private cache')
    (root/'external.sif').write_bytes(b'container')
    (root/'nested').mkdir()
    (root/'nested'/'shader.json').write_bytes(b'{}')
    with modal_boundaries.package_snapshot(root) as captured:
        assert engine.package_inventory(captured) == engine.package_inventory(root)
        assert not (captured/'render_outputs').exists() and not (captured/'external.sif').exists()
        assert (captured/'nested'/'shader.json').is_file()
    (root/'escape.py').symlink_to(tmp_path/'outside')
    with pytest.raises((ValueError, engine.RestartRequired)):
        with modal_boundaries.package_snapshot(root): pass


@pytest.mark.parametrize('mutation', ['retarget_alias', 'changed_current_package'])
def test_modal_snapshot_cannot_reseal_after_process_mutation(tmp_path, monkeypatch, mutation):
    from blender_render import engine, modal_boundaries
    current_dir, other_dir = tmp_path/'current', tmp_path/'other'
    current_dir.mkdir()
    other_dir.mkdir()
    original, other = package(current_dir), package(other_dir)
    alias = tmp_path/'package_alias'
    alias.symlink_to(original, target_is_directory=True)
    seal = engine.EngineSeal(alias)
    monkeypatch.setattr(engine, 'process_seal', lambda:seal)
    if mutation == 'retarget_alias':
        alias.unlink()
        alias.symlink_to(other, target_is_directory=True)
        requested = alias
    else:
        (original/'engine.py').write_bytes(b'# changed current package')
        requested = other
    with pytest.raises(engine.RestartRequired):
        with modal_boundaries.package_snapshot(requested):
            pytest.fail('Mutated process captured a new package seal')
    assert seal.latched
