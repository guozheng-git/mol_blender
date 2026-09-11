"""Compatibility evidence contracts; every execution boundary is injected."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from test_jobs_api import payload, TOKEN
from test_c3_operation_blocks import admitted

REASONS = ('engine_build_mismatch', 'engine_protocol_mismatch', 'engine_schema_mismatch',
           'capability_missing', 'runtime_version_mismatch', 'runtime_dependency_missing',
           'ccd_missing', 'ccd_checksum_mismatch', 'ccd_activation_failed',
           'worker_ipc_unavailable', 'worker_startup_failed', 'worker_readiness_failed',
           'unknown_compatibility')


@pytest.mark.parametrize('reason', REASONS)
def test_exception_to_owner_status(reason, tmp_path, payload, monkeypatch):
    from blender_render import compatibility as diag, engine, blender_worker_daemon as daemon
    from blender_render.render_worker_manager import PersistentBlenderWorker, WorkerNotReady
    from blender_render.api import create_app
    diagnostic = diag.make(reason, phase='readiness', component='worker', before_execution=True)
    original = engine.RuntimeUnavailable('secret /private/molecule token=https://secret', diagnostic=diagnostic)
    response = daemon._error_response(original)
    assert response['compatibility_diagnostic'] == diagnostic
    assert 'traceback' not in response and 'secret' not in json.dumps(response)
    worker = PersistentBlenderWorker(root_dir=tmp_path/'worker', blender_exe='fake',
        custom_script=engine.process_seal().root/'blender_custom_render.py', use_apptainer=False, apptainer_image='')
    worker.socket_path = tmp_path/'fake-socket'; worker.socket_path.touch()
    worker._process = SimpleNamespace(poll=lambda:None)
    monkeypatch.setattr(worker, '_send_request_locked', lambda *a, **kw:response)
    with pytest.raises(WorkerNotReady) as caught:
        worker._wait_until_ready_locked(timeout=1)
    assert caught.value.diagnostic['reason'] == reason
    from blender_render.jobs import JobStore, Policy
    from blender_render.service import RenderService
    from test_jobs_api import Compute
    store = JobStore(tmp_path/'store', Policy(public=True))
    service = RenderService(store, Compute())
    source = service.import_payload('github:a', payload)
    job = service.submit('github:a', source['input_id'], {}, 'one')
    row = store.claim()
    before = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    def fail(*a): raise caught.value
    monkeypatch.setattr(service.backend, 'render', fail)
    service._execute(row)
    after = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    for key in ('id', 'input_id', 'execution', 'reserved', 'charged', 'remote_id', 'operation_key'):
        assert after[key] == before[key]
    class Auth:
        async def verify_token(self, token):
            return SimpleNamespace(claims={'sub':'a' if token == TOKEN else 'b'})
    app = create_app(service, token=TOKEN, auth_provider=Auth())
    with TestClient(app) as client:
        state = client.get('/v1/jobs/'+job['job_id'], headers={'Authorization':'Bearer '+TOKEN}).json()
        assert state['condition'] == 'compatibility_blocked', state
        assert state['compatibility_diagnostic']['reason'] == reason
        assert state['error'] and 'secret' not in json.dumps(state)
        assert state['next_action'] == 'restore_exact_engine'
        assert client.get('/v1/jobs/'+job['job_id'], headers={'Authorization':'Bearer other'}).status_code == 404
        assert client.get('/v1/jobs/'+job['job_id']).status_code == 401
        for route in ('/health', '/healthz'):
            assert 'compatibility_diagnostic' not in client.get(route).json()
    store.close()


def test_storage_migration_atomicity_and_clear(tmp_path, payload):
    import sqlite3
    from blender_render import compatibility as diag
    from blender_render.jobs import JobStore
    store, service, _, job = admitted(tmp_path, payload)
    diagnostic = diag.make('ccd_missing', phase='startup', component='ccd', before_execution=True)
    before = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    store.db.execute("CREATE TRIGGER reject_diagnostic BEFORE UPDATE OF compatibility_diagnostic ON jobs BEGIN SELECT RAISE(ABORT,'injected storage error'); END")
    with pytest.raises(sqlite3.DatabaseError): store.block(job['job_id'], 'compatibility_blocked', 'dispatch', diagnostic=diagnostic)
    assert dict(store.db.execute('SELECT * FROM jobs').fetchone()) == before
    store.db.execute('DROP TRIGGER reject_diagnostic')
    store.block(job['job_id'], 'compatibility_blocked', 'dispatch', diagnostic=diagnostic)
    assert store.get('a', job['job_id'])['compatibility_diagnostic'] == diagnostic
    store.process_blocked = lambda:True
    assert store.get('a', job['job_id'])['compatibility_diagnostic'] is None
    store.process_blocked = lambda:False
    store.recover_interrupted()
    assert store.get('a', job['job_id'])['compatibility_diagnostic'] is None
    assert store.db.execute('SELECT compatibility_diagnostic FROM jobs').fetchone()[0] is None
    store.block(job['job_id'], 'compatibility_blocked', 'dispatch')
    store.db.execute('ALTER TABLE jobs DROP COLUMN compatibility_diagnostic')
    tables = [r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    saved = {t:[tuple(r) for r in store.db.execute('SELECT * FROM '+t)] for t in tables}
    store.close()
    store = JobStore(tmp_path)
    for t in tables:
        rows = [tuple(r) for r in store.db.execute('SELECT * FROM '+t)]
        assert ([r[:-1] for r in rows] if t=='jobs' else rows) == saved[t]
    assert store.get('a', job['job_id'])['compatibility_diagnostic'] is None
    store.close()


@pytest.mark.parametrize('failure,reason', [('missing','ccd_missing'), ('checksum','ccd_checksum_mismatch'), ('activation','ccd_activation_failed')])
def test_actual_ccd_sources(tmp_path, failure, reason):
    import hashlib
    from blender_render import worker_ccd, engine
    path = tmp_path/'private-name'; path.write_bytes(b'ccd')
    ccd = SimpleNamespace(_CCD_FILE=path)
    ccd.set_ccd_path = lambda value:setattr(ccd, '_CCD_FILE', value)
    if failure=='missing': path.unlink()
    if failure=='checksum': path.write_bytes(b'bad')
    if failure=='activation': ccd.set_ccd_path = lambda value:None
    with pytest.raises(engine.RuntimeUnavailable) as caught:
        worker_ccd.initialize(parent=tmp_path, ccd_module=ccd, expected=hashlib.sha256(b'ccd').hexdigest())
    assert caught.value.diagnostic['reason']==reason
    assert str(tmp_path) not in json.dumps(caught.value.diagnostic)
    assert not list(tmp_path.glob('worker-ccd-*'))


def test_runtime_and_capability_sources():
    from blender_render import compatibility as diag, engine
    for missing in (True, False):
        observed = {'versions':{'numpy':None if missing else '9.0'},'verified':False}
        assert diag.runtime_diagnostic(observed, {'numpy':'1.26.4'})['reason'] == ('runtime_dependency_missing' if missing else 'runtime_version_mismatch')
    expected = engine.engine_identity(); observed = engine.engine_identity(); observed['capabilities']=[]
    with pytest.raises(engine.EngineMismatch) as caught: engine.require_compatible(expected, observed, ['representation.cartoon'])
    assert caught.value.diagnostic['reason']=='capability_missing'


def test_worker_startup_exit_timeout_and_bad_peer_keep_existing_classes(tmp_path, monkeypatch):
    from blender_render import engine, render_worker_manager as manager
    worker = manager.PersistentBlenderWorker(root_dir=tmp_path, blender_exe='fake',
        custom_script=engine.process_seal().root/'blender_custom_render.py', use_apptainer=False, apptainer_image='')
    worker.socket_path = tmp_path/'fake-socket'
    worker.stdout_log.write_text('private tokens'); worker.stderr_log.write_text('private traceback')
    worker._process = SimpleNamespace(poll=lambda:7)
    with pytest.raises(engine.ExecutionUnknown) as caught: worker._wait_until_ready_locked(timeout=1)
    assert caught.value.diagnostic['reason']=='worker_startup_failed'
    assert caught.value.diagnostic['observed']=={'exit_code':7}
    assert caught.value.diagnostic['log_refs']==[{'name':'worker.stdout.log','exists':True},{'name':'worker.stderr.log','exists':True}]
    worker._process = SimpleNamespace(poll=lambda:None)
    clock = iter([0,0,2])
    monkeypatch.setattr(manager.time, 'time', lambda:next(clock))
    with pytest.raises(manager.WorkerError) as caught: worker._wait_until_ready_locked(timeout=1)
    assert not isinstance(caught.value, manager.WorkerNotReady)
    assert caught.value.diagnostic['reason']=='worker_readiness_failed'
    monkeypatch.undo()
    worker.socket_path.touch()
    monkeypatch.setattr(worker, '_send_request_locked', lambda *a,**kw:{'condition':'compatibility_blocked',
        'compatibility_diagnostic':{'reason':'/private/secret','before_execution':True}})
    with pytest.raises(manager.WorkerNotReady) as caught: worker._wait_until_ready_locked(timeout=1)
    assert caught.value.diagnostic['reason']=='unknown_compatibility'


def test_owned_sanitized_values_cannot_change_after_validation():
    from blender_render import compatibility as diag, engine
    assert diag.CAPABILITIES == set(engine.CAPABILITIES)
    value = diag.make('engine_schema_mismatch', expected={'schema_versions':['1.0']})
    saved = diag.sanitize(value)
    value['expected']['schema_versions'].append('/private/secret')
    assert saved['expected']['schema_versions']==['1.0']
    assert diag.sanitize({**saved,'version':2})['reason']=='unknown_compatibility'
    assert diag.sanitize({**saved,'before_execution':'true'})['reason']=='unknown_compatibility'


def test_noncompatibility_error_preserves_existing_fallback_signal():
    from blender_render.blender_worker_daemon import _error_response
    # Existing explicit CIF/PDB fallback inspects this non-compatibility error.
    response = _error_response(RuntimeError('CIF parse failed'))
    assert response['condition'] is None and response['error'] == 'CIF parse failed'
    assert 'traceback' not in response


def test_standalone_verifier_and_constructor_never_import_on_failure(tmp_path, monkeypatch):
    import builtins
    import runpy
    from pathlib import Path
    from blender_render import engine
    standalone = runpy.run_path(str(Path(engine.__file__)))
    from test_c3_integrity import package
    root = package(tmp_path)
    manifest = standalone['generate_manifest'](root)
    (root/'engine-manifest.json').write_text(json.dumps(manifest))
    (root/'engine.py').write_text('# changed')
    original = builtins.__import__
    def imports(name, *a, **kw):
        if 'compatibility' in name:pytest.fail('Exception construction imported unverified code')
        return original(name, *a, **kw)
    monkeypatch.setattr(builtins, '__import__', imports)
    for name in ('EngineMismatch','ExecutionUnknown','RestartRequired'):
        exc = standalone[name]('private failure')
        assert exc.diagnostic['reason']=='unknown_compatibility'
    import sys
    monkeypatch.delitem(sys.modules, 'blender_render.compatibility')
    assert standalone['EngineMismatch']('private failure').diagnostic['reason']=='unknown_compatibility'
    with pytest.raises(standalone['RestartRequired']): standalone['EngineSeal'](root)


def test_private_stderr_failure_cannot_change_ipc_refusal(monkeypatch):
    import io
    from blender_render import engine, compatibility as diag, blender_worker_daemon as daemon
    class Broken(io.StringIO):
        def write(self, *a):raise OSError('private log full')
    monkeypatch.setattr(daemon, 'sys', SimpleNamespace(stderr=Broken()))
    exc = engine.RuntimeUnavailable('private data', diagnostic=diag.make('ccd_missing'))
    result = daemon._error_response(exc)
    assert result['condition']=='compatibility_blocked'
    assert result['compatibility_diagnostic']['reason']=='ccd_missing'


def test_sanitizer_is_bounded_and_fail_closed():
    from blender_render import compatibility as diag
    safe = diag.make('ccd_missing', phase='startup', component='ccd', before_execution=True)
    assert diag.sanitize({**safe, 'traceback':'secret', 'token':'secret'}) == safe
    for value in ('/private/structure.pse', 'https://token', 'Traceback secret', 'my molecule', 'x'*5000):
        for key in ('reason','phase','component'):
            result = diag.sanitize({**safe, key:value})
            assert result['reason'] == 'unknown_compatibility' and value not in json.dumps(result)
        assert diag.sanitize({**safe, 'observed':{'engine_build':value}})['reason'] == 'unknown_compatibility'
    result = diag.sanitize({**safe, 'observed':{'path':'/secret','token':'secret'}})
    assert result['observed'] == {}
    assert diag.sanitize({**safe, 'log_refs':[{'name':'/secret/worker.stdout.log','exists':True}]})['reason'] == 'unknown_compatibility'


@pytest.mark.parametrize('field,reason', [('engine_build','engine_build_mismatch'),
    ('protocol_version','engine_protocol_mismatch'),('schema_versions','engine_schema_mismatch')])
def test_engine_sources(field, reason):
    from blender_render import engine
    expected = engine.engine_identity(); observed = engine.engine_identity()
    observed[field] = 'a'*64 if field=='engine_build' else 999 if field=='protocol_version' else ['9.0']
    with pytest.raises(engine.EngineMismatch) as caught: engine.require_compatible(expected, observed, [])
    assert caught.value.diagnostic['reason'] == reason


def test_ambiguity_is_not_reclassified(tmp_path, payload, monkeypatch):
    from blender_render import engine, compatibility as diag
    store, service, _, job = admitted(tmp_path, payload)
    def unknown(*a):
        raise engine.ExecutionUnknown('secret', diagnostic=diag.make('worker_readiness_failed'))
    monkeypatch.setattr(service.backend, 'render', unknown)
    service._execute(store.claim())
    state = store.get('a', job['job_id'])
    assert state['condition'] == 'execution_unknown' and state['next_action'] == 'inspect_execution'
    assert store.claim() is None
    store.close()
