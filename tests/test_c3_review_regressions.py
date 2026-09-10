"""Independent review regressions, with process and transport boundaries faked."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_jobs_api import payload
from test_gateway_recovery import pair
from test_c3_operation_blocks import admitted
from test_c3_integrity import package


@pytest.mark.parametrize('entrypoint', ['local', 'gateway'])
def test_entrypoint_preserves_operator_drain(tmp_path, monkeypatch, entrypoint):
    from blender_render import cli, gateway, service
    from blender_render.jobs import JobStore
    store = JobStore(tmp_path/'service')
    store.drain()
    store.close()
    token = tmp_path/'token'
    token.write_text('t'*40)
    creds = tmp_path/'credentials'
    creds.write_text(json.dumps({k:'test' for k in ('GITHUB_CLIENT_ID','GITHUB_CLIENT_SECRET',
        'BLENDER_RENDER_ADMIN_TOKEN','BLENDER_RENDER_DOWNLOAD_KEY','BLENDER_RENDER_ENCRYPTION_KEY','BLENDER_RENDER_JWT_KEY')}))
    seen = []
    class Stopped(Exception): pass
    def create(store, *a, **k):
        seen.append(store.db.execute("SELECT value FROM settings WHERE key='admission'").fetchone()[0])
        store.close()
        raise Stopped()
    monkeypatch.setattr(service, 'RenderService', create)
    monkeypatch.setattr(service, 'HostBackend', lambda *a, **k:object())
    args = SimpleNamespace(root=tmp_path/'service', host='127.0.0.1', token_file=token,
        job_timeout=60, worker_profile='', blender='', image='', pymol_python='',
        public_url='https://test.invalid', allowed_user=['123'], credentials=creds, profile='fake')
    with pytest.raises(Stopped):
        (cli.serve if entrypoint == 'local' else gateway.serve_gateway)(args)
    assert seen == ['draining']


@pytest.mark.parametrize('error', ['unknown', 'restart', 'compatibility'])
def test_cancellation_race_settles_blocked_backend(tmp_path, payload, error):
    from blender_render.engine import ExecutionUnknown, RestartRequired, EngineMismatch
    store, service, source, job = admitted(tmp_path, payload)
    def render(*args):
        store.cancel('a', job['job_id'])
        exc = {'unknown':ExecutionUnknown, 'restart':RestartRequired, 'compatibility':EngineMismatch}[error]('blocked')
        if error == 'restart': exc.before_execution = True
        raise exc
    service.backend.render = render
    service._execute(store.claim())
    row = store.db.execute('SELECT * FROM jobs').fetchone()
    assert row['state'] == 'cancelled' and store.claim() is None
    assert row['charged'] == (row['reserved'] if error == 'unknown' else 0)
    store.close()


@pytest.mark.parametrize('completed', [False, True])
def test_legacy_child_without_resume_evidence_stays_preserved(tmp_path, payload, completed):
    from blender_render.service import RenderService
    store, service, source, job = admitted(tmp_path, payload)
    store.db.execute("UPDATE jobs SET state='transfer_pending',remote_id='old-child',remote_phase='child_transfer_pending',remote_completed_at=?,execution=NULL,resume_phase=NULL", (123 if completed else None,))
    RenderService(store, service.backend)
    row = store.db.execute('SELECT * FROM jobs').fetchone()
    assert row['blocked_condition'] == 'recovery_required'
    assert row['remote_id'] == 'old-child' and row['reserved'] > 0 and row['charged'] == 0
    assert store.input('a', source) and store.claim() is None
    store.close()


def test_original_gateway_invocation_downgrades_to_attachment(pair, monkeypatch):
    from blender_render import gateway, client
    service, leaf, identifier, events, failures = pair
    transport = gateway.configured_client('fake')
    intentions = []
    def connect(profile, *, intention, **kwargs):
        intentions.append(intention)
        if len(intentions) > 1:
            assert intention == client.ConnectionIntention.ATTACH_EXISTING
        return transport
    monkeypatch.setattr(client, '_connect_client', connect)
    monkeypatch.setattr(gateway, 'configured_client', client.configured_client)
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert intentions[0] == client.ConnectionIntention.START_OR_ALLOCATE
    assert len(leaf.backend.calls) == 1


def test_latch_between_artifacts_preserves_verified_receipt(pair, monkeypatch):
    from blender_render import gateway
    from blender_render.engine import RestartRequired
    from blender_render.service import RenderService
    service, leaf, identifier, events, failures = pair
    transport = gateway.configured_client('fake')
    original = transport.request
    latched = False
    def request(method, path, *a, **k):
        nonlocal latched
        result = original(method, path, *a, **k)
        if path.endswith('/render.png?delivery=ack'): latched = True
        return result
    transport.request = request
    monkeypatch.setattr(gateway, 'configured_client', lambda *a, **k:transport)
    protect = gateway.protect
    def checked(action):
        if latched: raise RestartRequired('restart_required')
        return protect(action)
    monkeypatch.setattr(gateway, 'protect', checked)
    service._execute(service.store.claim())
    state = service.store.get('alice', identifier)
    assert state['status'] == 'transfer_pending' and state['condition'] == 'restart_required'
    assert not any('/ack' in path or 'render.blend?delivery' in path for _,path in events)
    receipt = json.loads((service.store.root/'child-results'/identifier/'receipt.json').read_text())
    assert receipt['outputs']['render.png']
    latched = False
    restarted = RenderService(service.store, service.backend)
    restarted._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert len(leaf.backend.calls) == 1
    assert sum(path.endswith('/render.png?delivery=ack') for _,path in events) == 1


def test_captured_package_resource_ignores_post_check_symlink(tmp_path, monkeypatch):
    from blender_render import engine
    root = package(tmp_path)
    resource = root/'preset.json'
    resource.write_bytes(b'sealed')
    seal = engine.EngineSeal(root)
    monkeypatch.setattr(engine, 'process_seal', lambda:seal)
    external = tmp_path/'external.json'
    external.write_bytes(b'changed')
    resource.unlink()
    resource.symlink_to(external)
    assert engine.resource_bytes(resource) == b'sealed'
    with pytest.raises(engine.RestartRequired): seal.check()


def test_subprocess_capture_uses_preloaded_bytes_and_manifest(tmp_path):
    from blender_render.engine import EngineSeal, RestartRequired
    root = package(tmp_path)
    (root/'nested').mkdir()
    (root/'nested'/'scene.py').write_bytes(b'# sealed')
    seal = EngineSeal(root)
    captured = seal.capture(tmp_path/'action'/'blender_render')
    (root/'nested'/'scene.py').write_bytes(b'raise RuntimeError("changed")')
    assert (captured/'nested'/'scene.py').read_bytes() == b'# sealed'
    assert EngineSeal(captured).identity == seal.identity
    with pytest.raises(RestartRequired): seal.check()


def test_local_export_uses_captured_script(tmp_path, monkeypatch):
    from blender_render import runtime, engine
    source = tmp_path/'fixture.pdb'
    source.write_text('test')
    def run(argv, **kwargs):
        script = Path(argv[1])
        assert script.parent != engine.process_seal().root
        assert engine.EngineSeal(script.parent).identity == engine.engine_identity()
        Path(argv[3]).write_text('{}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    assert runtime.export_file(source) == {}


def test_worker_command_and_environment_use_owned_capture(tmp_path, monkeypatch):
    from blender_render import engine, render_worker_manager as manager
    worker = manager.PersistentBlenderWorker(root_dir=tmp_path/'worker', blender_exe='fake',
        custom_script=engine.process_seal().root/'blender_custom_render.py', use_apptainer=False, apptainer_image='')
    monkeypatch.setattr(worker, '_resolve_blender_executable', lambda:'fake')
    monkeypatch.setattr(worker, '_wait_until_ready_locked', lambda **k:None)
    seen = []
    def launch(argv, **kwargs):
        daemon = Path(argv[argv.index('--python')+1])
        renderer = Path(argv[argv.index('--custom-script')+1])
        assert daemon.parent == renderer.parent != engine.process_seal().root
        assert engine.EngineSeal(daemon.parent).identity == engine.engine_identity()
        assert kwargs['env']['PYTHONPATH'].split(':')[0] == str(daemon.parent.parent)
        seen.append(daemon)
        return SimpleNamespace(pid=123456, poll=lambda:0)
    monkeypatch.setattr(manager.subprocess, 'Popen', launch)
    worker._start_locked()
    worker._stop_locked()
    assert seen and not seen[0].exists()


def test_remote_bootstrap_and_worker_launcher_capture_package(tmp_path, monkeypatch, capsys):
    import runpy
    import io
    import shlex
    from blender_render import engine, hpc_remote
    from test_hpc_negotiation import profile
    root = package(tmp_path)
    seal = engine.EngineSeal(root)
    observed = []
    def execute(path, **kwargs):
        captured = Path(path).parent
        assert captured != root
        assert engine.EngineSeal(captured).identity == seal.identity
        observed.append(captured)
    monkeypatch.setattr(runpy, 'run_path', execute)
    exec(engine.remote_bootstrap(root), {'__name__':'__main__'})
    assert observed and not observed[0].exists()
    doc = profile(tmp_path)
    monkeypatch.setattr(hpc_remote.sys, 'argv', ['helper', 'start'])
    monkeypatch.setattr(hpc_remote.sys, 'stdin', io.StringIO(json.dumps({'profile':doc})))
    def run(argv, **kwargs):
        script = Path(argv[-1])
        args = shlex.split(script.read_text().split('exec ',1)[1])
        captured = Path(args[1]).parent
        assert captured != engine.process_seal().root
        assert engine.EngineSeal(captured).identity == engine.engine_identity()
        return SimpleNamespace(returncode=0, stdout='123')
    monkeypatch.setattr(hpc_remote.subprocess, 'run', run)
    hpc_remote.main()
    assert json.loads(capsys.readouterr().out)['job_id'] == '123'


def test_preload_never_executes_changed_module(tmp_path, monkeypatch):
    import sys
    import blender_render
    from blender_render import engine
    root = package(tmp_path)
    module = root/'_seal_contract.py'
    marker = tmp_path/'executed'
    module.write_text('VALUE = 1')
    seal = engine.EngineSeal(root)
    monkeypatch.setattr(engine, 'process_seal', lambda:seal)
    monkeypatch.setattr(blender_render, '__path__', [str(root)])
    monkeypatch.setitem(engine.ACTION_MODULES, 'test', ('_seal_contract',))
    original = engine.importlib.import_module
    def changed(name):
        module.write_text('from pathlib import Path; Path('+repr(str(marker))+').touch()')
        return original(name)
    monkeypatch.setattr(engine.importlib, 'import_module', changed)
    try:
        with pytest.raises(engine.RestartRequired): engine.protect('test')
        assert not marker.exists()
    finally:
        sys.modules.pop('blender_render._seal_contract', None)


def test_original_child_timing_set_survives_attempt_window(tmp_path):
    from blender_render.jobs import JobStore
    from blender_render.telemetry import record
    store = JobStore(tmp_path)
    store.db.execute("INSERT INTO jobs(id,owner,input_id,idempotency_key,request_hash,options,state,submitted,reserved,remote_id) VALUES ('parent','a','i','k','h','{}','transfer_pending',0,10,'child')")
    original = [record('rendering', 'worker', 1)]
    store.import_child_timings('parent', 'child', original)
    store.add_timings('parent', [record('transfer', 'gateway', 1, attempt=n) for n in range(1,257)])
    marker = json.loads(store.db.execute('SELECT child_timing_import FROM jobs').fetchone()[0])
    assert marker['records'] == original
    store.close()


def test_remote_start_rechecks_exact_identity_before_allocation(tmp_path, monkeypatch):
    import io
    from blender_render import engine, hpc_remote
    from test_hpc_negotiation import profile
    expected = {**engine.engine_identity(), 'engine_build':'0'*64}
    monkeypatch.setattr(hpc_remote.sys, 'argv', ['helper', 'start'])
    monkeypatch.setattr(hpc_remote.sys, 'stdin', io.StringIO(json.dumps({'profile':profile(tmp_path), 'expected_engine':expected})))
    calls = []
    def run(*a, **k):
        calls.append(a)
        return SimpleNamespace(returncode=0, stdout='123')
    monkeypatch.setattr(hpc_remote.subprocess, 'run', run)
    with pytest.raises(engine.EngineMismatch): hpc_remote.main()
    assert calls == []


@pytest.mark.parametrize('condition', ['restart_required', 'compatibility_blocked', 'execution_unknown'])
def test_http_worker_preserves_nonterminal_conditions(payload, condition):
    from fastapi.testclient import TestClient
    from blender_render.engine import RestartRequired, EngineMismatch, ExecutionUnknown
    from blender_render.worker_http import create_worker
    from test_execution_envelope import worker_request
    errors = {'restart_required':RestartRequired, 'compatibility_blocked':EngineMismatch, 'execution_unknown':ExecutionUnknown}
    class Backend:
        def render(self, *a):
            error = errors[condition]('worker condition')
            if condition == 'restart_required': error.before_execution = True
            raise error
        def stop(self): pass
    with TestClient(create_worker('x'*40, backend_factory=lambda *a:Backend())) as client:
        response = client.post('/render/'+'a'*32, json=worker_request(payload, 'a'*32), headers={'Authorization':'Bearer '+'x'*40})
    assert response.status_code == 503
    assert response.json()['condition'] == condition


@pytest.mark.parametrize('condition', ['restart_required', 'compatibility_blocked', 'execution_unknown'])
def test_http_worker_condition_retains_admitted_operation(tmp_path, payload, monkeypatch, condition):
    import httpx
    from blender_render import engine
    from blender_render.worker_http import HTTPBackend
    store, service, source, job = admitted(tmp_path, payload)
    class Client:
        def __init__(self, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **k):
            return httpx.Response(200, json={'ready':True, 'controller_declaration':engine.engine_identity()}, request=httpx.Request('GET',url))
        def post(self, url, **k):
            return httpx.Response(503, json={'condition':condition, 'before_execution':condition!='execution_unknown'}, request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx, 'Client', Client)
    service.backend = HTTPBackend('https://worker.invalid', 'x'*40)
    service._execute(store.claim())
    state = store.get('a', job['job_id'])
    assert state['status'] == 'queued' and state['condition'] == condition
    assert store.input('a', source)
    assert store.db.execute('SELECT charged FROM jobs').fetchone()[0] == 0
    store.close()


def test_modal_partial_image_can_load_runtime_setup(tmp_path, monkeypatch):
    """Execute just the copied image bootstrap, stopping at the installer seam."""
    import ast
    import importlib.util
    import importlib
    import sys
    from blender_render import engine
    root = engine.process_seal().root
    tree = ast.parse((root/'modal_app.py').read_text())
    files = next(ast.literal_eval(node.iter) for node in ast.walk(tree)
                 if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and node.target.id == 'filename')
    partial = tmp_path/'partial'
    partial.mkdir()
    for name in files:
        (partial/name).write_bytes((root/name).read_bytes())
    name = '_c3_image_bootstrap'
    spec = importlib.util.spec_from_file_location(name, partial/'__init__.py', submodule_search_locations=[str(partial)])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    class InstallerBoundary(Exception): pass
    def installer(*a, **k): raise InstallerBoundary()
    try:
        spec.loader.exec_module(module)
        setup = importlib.import_module(name+'.runtime_install')
        monkeypatch.setattr(setup.subprocess, 'run', installer)
        with pytest.raises(InstallerBoundary): setup.install(blender='/fake/blender', root=tmp_path/'runtime')
    finally:
        for key in list(sys.modules):
            if key.startswith(name+'.'): sys.modules.pop(key)


def test_modal_helper_is_preloaded_without_provider_imports(monkeypatch):
    import sys
    from blender_render import engine
    monkeypatch.delitem(sys.modules, 'blender_render.modal_boundaries', raising=False)
    previous = set(sys.modules)
    engine.protect('conversion')
    assert 'blender_render.modal_boundaries' in sys.modules
    assert not {'bpy', 'modal', 'molecularnodes'} & (set(sys.modules)-previous)


def test_sandbox_creation_checks_integrity(monkeypatch):
    from blender_render import engine, modal_boundaries
    def latched(*a): raise engine.RestartRequired('restart_required')
    def create(): pytest.fail('Latched conversion reached sandbox creation')
    monkeypatch.setattr(engine, 'protect', latched)
    with pytest.raises(engine.RestartRequired):
        modal_boundaries.convert_file(b'public fixture', '.pdb', create_sandbox=create)


def test_latch_during_child_poll_blocks_downstream_recovery(pair, monkeypatch):
    from blender_render import gateway
    from blender_render.engine import RestartRequired
    from blender_render.service import RenderService
    service, leaf, identifier, events, failures = pair
    transport = gateway.configured_client('fake')
    original = transport.status
    latched = False
    def status(child):
        nonlocal latched
        result = original(child)
        latched = True
        return {**result, 'status':'transfer_pending'}
    transport.status = status
    monkeypatch.setattr(gateway, 'configured_client', lambda *a, **k:transport)
    protected = gateway.protect
    def check(action):
        if latched: raise RestartRequired('restart_required')
        return protected(action)
    monkeypatch.setattr(gateway, 'protect', check)
    service._execute(service.store.claim())
    state = service.store.get('alice', identifier)
    assert state['condition'] == 'restart_required' and state['child_operation_id']
    assert not any(path.endswith('/recover-transfer') for _,path in events)
    latched = False
    transport.status = original
    restarted = RenderService(service.store, service.backend)
    restarted._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert len(leaf.backend.calls) == 1


def test_package_alias_uses_captured_resource_and_cannot_retarget(tmp_path, monkeypatch):
    from blender_render import engine
    root = package(tmp_path)
    resource = root/'preset.json'
    resource.write_bytes(b'sealed')
    alias = tmp_path/'checkout-alias'
    alias.symlink_to(root, target_is_directory=True)
    seal = engine.EngineSeal(alias)
    monkeypatch.setattr(engine, 'process_seal', lambda:seal)
    resource.write_bytes(b'changed')
    assert engine.resource_bytes(alias/'preset.json') == b'sealed'
    resource.write_bytes(b'sealed')
    other = tmp_path/'unreviewed'
    other.mkdir()
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)
    with pytest.raises(engine.RestartRequired): seal.check()
