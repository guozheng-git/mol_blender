import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from blender_render.api import create_app
from blender_render.service import RenderService
from blender_render.jobs import JobStore, Policy
from blender_render import hpc, cli, engine
from test_jobs_api import Compute, TOKEN, payload


def test_public_health_aggregate_authenticated_identity(tmp_path):
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    http = TestClient(create_app(service, token=TOKEN, controller_tokens={'s'*40:'controller'}))
    for route in ('/health','/healthz'):
        result = http.get(route).json()
        assert 'controller_declaration' not in result and 'worker_observation' not in result
    assert http.get('/v1/capabilities').status_code == 401
    assert http.get('/v1/capabilities', headers={'Authorization':'Bearer '+'s'*40, 'X-Render-Owner':'a'}).json()['controller_declaration']['engine_build']
    store.close()


@pytest.mark.parametrize('transport', [None, 'bogus', '', ['slurm']])
def test_transport_must_be_explicit(transport):
    profile = json.loads(Path(hpc.__file__).with_name('profile_slurm.json').read_text())
    profile['transport'] = transport
    with pytest.raises(ValueError, match='transport'): hpc.validate_profile(profile)


def test_doctor_reads_sanitized_connection_cache(tmp_path, monkeypatch):
    profile = json.loads(Path(hpc.__file__).with_name('profile_slurm.json').read_text())
    monkeypatch.setattr(cli, 'load_config', lambda:{'profiles':{'test':profile}})
    monkeypatch.setattr(hpc, 'config_root', lambda:tmp_path)
    root = tmp_path/'hpc'/'test'
    root.mkdir(parents=True)
    (root/'connection.json').write_text(json.dumps({'token':'secret', 'url':'https://private',
        'observed_at':123, 'cached_observation':{'controller_declaration':engine.engine_identity(),
        'worker_observation':{'runtime':{'ccd':{'path':'/private/ccd','verified':True,'sha256':'a'*64}}}}}))
    result = cli.execute(cli.parser().parse_args(['doctor','--profile','test']))
    assert result['cached_observation']['observed_at'] == 123
    assert result['cached_observation']['controller_declaration']['engine_build']
    assert 'secret' not in json.dumps(result) and '/private' not in json.dumps(result)


def test_worker_limits_are_independent(payload):
    from blender_render.worker_http import create_worker
    from test_execution_envelope import worker_request
    payload = dict(payload)
    atom = next(line for line in payload['cif_text'].splitlines() if line.startswith('ATOM '))
    payload['cif_text'] += atom.replace('ATOM 1 ', 'ATOM 2 ').replace(' C CA ', ' C CB ')+'\n'
    request = worker_request(payload, 'a'*32)
    for limits in ({'max_atoms':1}, {'max_bytes':1}):
        http = TestClient(create_worker('x'*40, backend_factory=lambda *a:pytest.fail('oversize reached backend'), **limits))
        response = http.post('/render/'+'a'*32, json=request, headers={'Authorization':'Bearer '+'x'*40})
        assert response.status_code >= 400


def test_cleanliness_scope_checks_ignored_inputs_without_fixture_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, '_git_paths', lambda *args:['blender_render/nested/extension.so',
        'blender_render/__pycache__/x.pyc','1qys.cif','setup.py'])
    result = engine.reviewed_checkout(tmp_path)
    assert result == {'clean':False, 'paths':['blender_render/nested/extension.so','setup.py']}
