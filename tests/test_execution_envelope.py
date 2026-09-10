"""C3 snapshot boundaries: no runtime, process, or provider execution."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from blender_render.api import create_app
from blender_render.jobs import JobError, JobStore
from blender_render.service import RenderService, prepare_options
from test_jobs_api import payload, Compute, TOKEN


def worker_request(payload, identifier, timeout=600):
    from blender_render.execution import freeze_execution, dispatch_request
    from blender_render.service import validate_input
    data = validate_input(payload, max_atoms=1000, max_bytes=100000)
    snapshot = freeze_execution(data, prepare_options({}, data, public=True, timeout=timeout))
    return dispatch_request(snapshot, 'controller', 'owner', identifier)


def test_normalized_admission_binds_both_snapshots_without_changing_identity(tmp_path, payload):
    from blender_render.execution import bundle_digest
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    job = service.submit('a', source, {}, 'one')
    assert len(job['render_spec_sha256']) == 64
    captured = json.loads(store.db.execute('SELECT execution FROM jobs').fetchone()[0])
    assert job['bundle_sha256'] == bundle_digest(captured['bundle'])
    assert 'run_id' not in captured['bundle']
    with pytest.raises(JobError, match='immutable'):
        store.bind_execution(job['job_id'], {**captured, 'bundle_sha256': '0'*64})
    assert service.submit('a', source, {}, 'two')['job_id'] == job['job_id']
    service._execute(store.claim())
    assert store.get('a', job['job_id'])['render_spec_sha256'] == job['render_spec_sha256']
    assert store.result('a', job['job_id'])['bundle_sha256'] == job['bundle_sha256']
    store.close()


@pytest.mark.parametrize('suffix', ['.pse', '.cif', '.pdb'])
def test_deferred_admission_and_selection(tmp_path, payload, suffix):
    store = JobStore(tmp_path)
    calls = []
    def converter(data, ext, *, selection):
        assert store.db.execute("SELECT state FROM jobs").fetchone()[0] == 'running'
        calls.append((ext, selection))
        return payload
    service = RenderService(store, Compute(), converter=converter)
    job = service.render_file('a', b'public', suffix, 'public'+suffix, converter, {}, selection='chain A')
    assert job['stage'] == 'conversion_pending' and job['render_spec_sha256'] is None
    service._execute(store.claim())
    assert calls == [(suffix, 'chain A')]
    assert store.get('a', job['job_id'])['render_spec_sha256']
    store.close()


def test_internal_route_authentication_tampering_and_no_reresolution(tmp_path, payload, monkeypatch):
    from blender_render.execution import freeze_execution
    from blender_render.service import validate_input
    data = validate_input(payload, max_atoms=100, max_bytes=100000)
    snapshot = freeze_execution(data, prepare_options({}, data, public=False, timeout=600))
    envelope = {**snapshot, 'controller_id': 'lab', 'owner_id': 'alice', 'operation_id': 'intent'}
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    app = create_app(service, token=TOKEN, controller_tokens={'s'*40: 'lab'})
    client = TestClient(app)
    route = '/internal/v1/resolved'
    assert client.post(route, json=envelope, headers={'Authorization':'Bearer '+TOKEN}).status_code == 401
    headers = {'Authorization':'Bearer '+'s'*40}
    for key, value in [('owner_id', ''), ('controller_id', 'other'), ('bundle_sha256', '0'*64)]:
        assert client.post(route, json={**envelope, key:value}, headers=headers).status_code >= 400
    changed = copy.deepcopy(envelope)
    changed['request']['overrides'] = {}
    assert client.post(route, json=changed, headers=headers).status_code == 400
    monkeypatch.setattr('blender_render.spec.resolve_options', lambda *a, **k: pytest.fail('resolved twice'))
    first = client.post(route, json=envelope, headers=headers)
    assert first.status_code == 200, first.text
    assert client.post(route, json=envelope, headers=headers).json()['job_id'] == first.json()['job_id']
    assert store.recent('alice')['jobs'] == []
    store.close()


def test_exact_build_and_capability_negotiation():
    from blender_render.engine import engine_identity, require_compatible
    identity = engine_identity()
    require_compatible(identity, identity, [])
    for field, value in [('engine_build', '0'*64), ('protocol_version', 999), ('capabilities', [])]:
        observed = {**identity, field:value}
        with pytest.raises(ValueError):
            require_compatible(identity, observed, ['camera.depth_of_field'])


@pytest.mark.parametrize('replacement_matches', [False, True])
def test_stale_worker_restarted_before_dispatch(tmp_path, monkeypatch, replacement_matches):
    from blender_render.engine import engine_identity
    from blender_render.render_worker_manager import PersistentBlenderWorker, WorkerError
    worker = PersistentBlenderWorker(root_dir=tmp_path, blender_exe='unused', custom_script=tmp_path/'unused',
                                     use_apptainer=False, apptainer_image='')
    identity = engine_identity()
    stale = {**identity, 'engine_build': 'stale'}
    responses = iter([stale, identity if replacement_matches else stale])
    monkeypatch.setattr(worker, '_send_request_locked', lambda *a, **k: {'engine': next(responses), 'runtime': {'verified': True}})
    calls = []
    monkeypatch.setattr(worker, '_stop_locked', lambda: calls.append('stop'))
    monkeypatch.setattr(worker, '_start_locked', lambda: calls.append('start'))
    if replacement_matches:
        worker._negotiate_locked(identity, [])
    else:
        with pytest.raises(WorkerError, match='engine_build'):
            worker._negotiate_locked(identity, [])
    assert calls == ['stop', 'start']


def test_snapshot_policy_and_capabilities_do_not_mutate_input(payload):
    from blender_render.execution import freeze_execution, validate_snapshot
    from blender_render.service import validate_input
    data = validate_input(payload, max_atoms=100, max_bytes=100000)
    snapshot = freeze_execution(data, prepare_options({'width': 5000}, data, public=False, timeout=600))
    original = copy.deepcopy(snapshot)
    with pytest.raises(ValueError, match='policy'):
        validate_snapshot(snapshot, public=True)
    assert snapshot == original
    snapshot['request']['render_spec']['required_capabilities'] = []
    with pytest.raises(ValueError, match='capabilities'):
        validate_snapshot(snapshot)


def test_modal_selection_is_one_quoted_argument_and_always_terminates(payload):
    import shlex
    from types import SimpleNamespace
    from blender_render.modal_boundaries import convert_file
    commands, stopped = [], []
    selection = '(chain A) or (chain D); $(not-a-command)'
    sandbox = SimpleNamespace(filesystem=SimpleNamespace(write_bytes=lambda *a: None,
        read_bytes=lambda *a: json.dumps(payload).encode()), terminate=lambda: stopped.append(True))
    def execute(*args):
        commands.append(args)
        return SimpleNamespace(wait=lambda: None, returncode=0)
    sandbox.exec = execute
    assert convert_file(b'public', '.pse', selection=selection, create_sandbox=lambda:sandbox) == payload
    assert shlex.split(commands[0][2])[5] == selection
    assert stopped == [True]


def test_http_backend_mismatch_cannot_dispatch_paid_render(payload, monkeypatch):
    import httpx
    from types import SimpleNamespace
    from blender_render.worker_http import HTTPBackend
    from blender_render.engine import engine_identity
    request = worker_request(payload, 'a'*32)
    calls = []
    class Transport:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path, **kwargs):
            calls.append('health')
            return SimpleNamespace(status_code=200, json=lambda:{'controller_declaration':{**engine_identity(), 'engine_build':'stale'}})
        def post(self, *args, **kwargs): pytest.fail('Incompatible worker rendered')
    monkeypatch.setattr(httpx, 'Client', Transport)
    with pytest.raises(ValueError, match='engine_build'):
        HTTPBackend('https://fake', 'x'*40).render(request, 'a'*32, lambda:False)
    assert calls == ['health']


def test_worker_invalid_snapshot_never_creates_backend(payload):
    from blender_render.worker_http import create_worker
    request = worker_request(payload, 'a'*32)
    request['payload']['view']['pymol_view'][9] = 400
    http = TestClient(create_worker('x'*40, backend_factory=lambda *a:pytest.fail('Invalid input started backend')))
    response = http.post('/render/'+'a'*32, json=request, headers={'Authorization':'Bearer '+'x'*40})
    assert response.status_code >= 400


def test_prepared_runtime_is_required_before_worker_dispatch(tmp_path, monkeypatch):
    from blender_render.engine import engine_identity
    from blender_render.render_worker_manager import PersistentBlenderWorker, WorkerError
    worker = PersistentBlenderWorker(root_dir=tmp_path, blender_exe='unused', custom_script=tmp_path/'unused',
                                     use_apptainer=False, apptainer_image='')
    monkeypatch.setattr(worker, '_send_request_locked', lambda *a, **k: {'engine':engine_identity(), 'runtime':{'verified':False}})
    calls = []
    monkeypatch.setattr(worker, '_stop_locked', lambda: calls.append('stop'))
    monkeypatch.setattr(worker, '_start_locked', lambda: calls.append('start'))
    with pytest.raises(WorkerError, match='setup'):
        worker._negotiate_locked(engine_identity(), [])
    assert calls == ['stop', 'start']


def test_snapshot_storage_counts_toward_receiving_limit(tmp_path, payload):
    from blender_render.jobs import Policy
    store = JobStore(tmp_path, Policy(public=True, max_storage_bytes=5000))
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    with pytest.raises(JobError, match='capacity|storage'):
        service.submit('a', source, {}, 'once')
    assert not service.backend.calls and store.recent('a')['jobs'] == []
    store.close()


def test_private_gateway_rejects_shared_endpoint_before_network(monkeypatch):
    from blender_render.client import _connect_client, ClientError
    monkeypatch.setattr('blender_render.config.load_config', lambda:{'profiles':{'shared':{
        'kind':'https', 'url':'https://shared.invalid', 'auth':'oauth'}}})
    with pytest.raises(ClientError, match='private endpoint'):
        _connect_client('shared', controller_owner='alice')


@pytest.mark.parametrize('suffix', ['.pse', '.cif', '.pdb'])
def test_raw_file_adapter_preserves_selection_and_stable_object_name(tmp_path, payload, monkeypatch, suffix):
    import sys
    from types import SimpleNamespace
    from blender_render import export
    loads, calls = [], []
    cmd = SimpleNamespace(load=lambda *a: loads.append(a), hide=lambda *a:None,
        show=lambda *a:None, orient=lambda:None, zoom=lambda *a, **k:None,
        get_version=lambda:('test', 1))
    class PyMOL:
        def __enter__(self): return SimpleNamespace(cmd=cmd)
        def __exit__(self, *a): pass
    monkeypatch.setitem(sys.modules, 'pymol2', SimpleNamespace(PyMOL=PyMOL))
    def build(command, selection, state, **kwargs):
        assert command is cmd and kwargs == {'include_color_hints': True}
        calls.append(selection)
        return copy.deepcopy(payload)
    monkeypatch.setattr(export, 'build_render_payload', build)
    path = tmp_path/('arbitrary-scratch-name'+suffix)
    path.write_bytes(b'public')
    exported = export.from_file(path, '(chain A) or (chain D)')
    assert calls == ['(chain A) or (chain D)']
    assert loads == [(str(path),)] if suffix == '.pse' else loads == [(str(path), 'structure')]
    assert exported['view'] == payload['view'] and exported['dss'] == payload['dss']


@pytest.mark.parametrize('suffix', [None, '.pse', '.cif', '.pdb'])
def test_molecular_state_survives_bound_dispatch(tmp_path, payload, suffix):
    molecular = copy.deepcopy(payload)
    molecular.update(selection='chain A', state=2,
        provenance={'enabled_objects':['protein'], 'object_transforms':{'protein':[1,0,0,5,0,1,0,2,0,0,1,3,0,0,0,1]}},
        color_hints={'atoms':{'A:1:ALA:CA':'#FF0000'}},
        ss_hints={'by_chain_resi':{'A:1':'H'}})
    class Capture(Compute):
        def render(self, request, *args):
            for key in ('selection','state','provenance','color_hints','ss_hints','view','cif_text','dss'):
                assert request['payload'][key] == molecular[key]
            assert 'run_id' not in request['payload']
            return super().render(request, *args)
    store = JobStore(tmp_path)
    converter = lambda *a, **k: copy.deepcopy(molecular)
    service = RenderService(store, Capture(), converter=converter)
    if suffix:
        job = service.render_file('a', b'public', suffix, 'public'+suffix, converter, {}, selection='chain A')
    else:
        job = service.submit('a', service.import_payload('a', molecular)['input_id'], {}, 'one')
    service._execute(store.claim())
    assert store.get('a', job['job_id'])['status'] == 'done'
    assert len(service.backend.calls) == 1
    store.close()


@pytest.mark.parametrize('suffix', ['.pse', '.cif', '.pdb'])
def test_empty_deferred_selection_never_dispatches(tmp_path, payload, suffix):
    empty = {**payload, 'cif_text':payload['cif_text'].split('ATOM 1')[0]}
    converter = lambda *a, **k: empty
    store = JobStore(tmp_path)
    service = RenderService(store, Compute(), converter=converter)
    job = service.render_file('a', b'public', suffix, 'public'+suffix, converter, {}, selection='none')
    service._execute(store.claim())
    assert store.get('a', job['job_id'])['status'] == 'failed'
    assert not service.backend.calls
    store.close()
