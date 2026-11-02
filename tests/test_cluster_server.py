import hashlib
import time
from dataclasses import replace

import pytest
from starlette.testclient import TestClient

from blender_render.cluster.common import atomic_json
from blender_render.cluster.server import create_app
from test_cluster_jobs import Scheduler, config, request


@pytest.fixture
def service(config):
    tokens = {'alice': 'a' * 43, 'bob': 'b' * 43}
    atomic_json(config.tokens_file, {'users': {owner: hashlib.sha256(token.encode()).hexdigest()
                                             for owner, token in tokens.items()}})
    app = create_app(config, scheduler=Scheduler())
    return app, {name: {'Authorization': 'Bearer ' + token} for name, token in tokens.items()}


def test_http_ownership_download_cancel_and_delete(service):
    app, headers = service
    with TestClient(app) as client:
        assert client.get('/v1/info').status_code == 401
        assert client.get('/v1/info', headers=headers['alice']).json()['protocol'] == 1
        result = client.post('/v1/jobs', headers=headers['alice'], json=request())
        assert result.status_code == 202
        identifier = result.json()['job_id']
        url = '/v1/jobs/' + identifier
        assert client.get(url, headers=headers['bob']).status_code == 404
        assert client.post(url + '/cancel', headers=headers['bob']).status_code == 404
        assert client.delete(url, headers=headers['bob']).status_code == 404
        assert client.get('/v1/jobs', headers=headers['bob']).json() == []
        assert client.get(url + '/artifacts/render.png', headers=headers['alice']).status_code == 404
        directory = app.state.store.directory(identifier)
        (directory / 'out').mkdir()
        (directory / 'out/render.png').write_bytes(b'verified-image')
        metadata = {'path': 'out/render.png', 'bytes': 14, 'sha256': hashlib.sha256(b'verified-image').hexdigest()}
        atomic_json(directory / 'result.json', {'status': 'completed', 'finished_at': time.time(),
                                                'artifacts': {'render.png': metadata}})
        status = client.get(url, headers=headers['alice']).json()
        assert status['status'] == 'completed'
        assert 'path' not in status['artifacts']['render.png']
        assert 'owner' not in status and 'payload' not in status
        download = client.get(url + '/artifacts/render.png', headers=headers['alice'])
        assert download.content == b'verified-image'
        assert download.headers['x-content-sha256'] == metadata['sha256']
        assert download.headers['cache-control'] == 'no-store'
        assert client.get(url + '/artifacts/render.png', headers=headers['bob']).status_code == 404
        assert client.get(url + '/artifacts/job.json', headers=headers['alice']).status_code == 404
        assert client.delete(url, headers=headers['alice']).json()['status'] == 'expired'
        assert client.get(url + '/artifacts/render.png', headers=headers['alice']).status_code == 404
        assert client.post('/v1/jobs', headers=headers['alice'], json=request()).json()['status'] == 'expired'


def test_requests_are_bounded_and_data_only(service):
    app, headers = service
    with TestClient(app) as client:
        header = {**headers['alice'], 'Content-Type': 'application/json'}
        assert client.post('/v1/jobs', headers=header, content=b'not json').status_code == 400
        assert client.post('/v1/jobs', headers=header, content=b'{"payload":NaN}').status_code == 400
        assert client.post('/v1/jobs', headers={**header, 'Content-Encoding': 'gzip'}, content=b'zip').status_code == 415
        assert client.post('/v1/jobs', headers={**header, 'Content-Length': str(21 * 1024**2)}, content=b'{}').status_code == 413
        doc = request()
        doc['owner'] = 'bob'
        assert client.post('/v1/jobs', headers=headers['alice'], json=doc).status_code == 400
        doc = request()
        doc['payload']['pse'] = 'executable session'
        assert client.post('/v1/jobs', headers=headers['alice'], json=doc).status_code == 400
        assert app.state.store.scheduler.submissions == []


def test_credential_revocation_is_immediate(service, config):
    app, headers = service
    with TestClient(app) as client:
        assert client.get('/v1/info', headers=headers['alice']).status_code == 200
        atomic_json(config.tokens_file, {'users': {}})
        assert client.get('/v1/info', headers=headers['alice']).status_code == 401


def test_private_spool_and_pinned_image_are_required(config):
    atomic_json(config.tokens_file, {'users': {}})
    config.image.write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        create_app(config)
    config.image.write_bytes(b'image')
    config.spool.mkdir(mode=0o755)
    config.spool.chmod(0o755)
    with pytest.raises(ValueError, match='0700'):
        create_app(config)


def test_cleanup_retains_idempotency_without_research_data(service):
    app, headers = service
    store = app.state.store
    job = store.submit('alice', request())['job_id']
    record = store.owned('alice', job)
    record.update(status='failed', finished_at=time.time() - 90000)
    store.save(record)
    store.sweep()
    assert store.status('alice', job)['status'] == 'expired'
    assert [p.name for p in store.directory(job).iterdir()] == ['job.json']


def test_artifact_paths_cannot_escape_the_job(service, tmp_path):
    app, headers = service
    store = app.state.store
    job = store.submit('alice', request())['job_id']
    private = tmp_path / 'private'
    private.write_bytes(b'secret')
    root = store.directory(job)
    (root / 'out').mkdir()
    (root / 'out/render.png').symlink_to(private)
    atomic_json(root / 'result.json', {'status': 'completed', 'finished_at': time.time(), 'artifacts': {
        'render.png': {'path': 'out/render.png', 'bytes': 6, 'sha256': '0' * 64}}})
    with TestClient(app) as client:
        assert client.get(f'/v1/jobs/{job}/artifacts/render.png', headers=headers['alice']).status_code == 404


def test_open_mode_has_one_shared_queue_and_preserves_limits(config):
    config = replace(config, authentication='none', tokens_file=None,
                     limits=replace(config.limits, max_active_per_user=1))
    app = create_app(config, scheduler=Scheduler())
    with TestClient(app) as client:
        assert client.get('/v1/info').json()['authentication'] == 'none'
        document = request()
        result = client.post('/v1/jobs', json=document)
        assert result.status_code == 202
        identifier = result.json()['job_id']
        # Headers do not partition the queue or let callers choose another owner.
        headers = {'Authorization': 'Bearer ignored'}
        assert client.post('/v1/jobs', json=document, headers=headers).json()['job_id'] == identifier
        assert len(app.state.store.scheduler.submissions) == 1
        assert client.get('/v1/jobs', headers=headers).json()[0]['job_id'] == identifier
        assert client.get('/v1/requests/' + document['request_id']).json()['job_id'] == identifier
        assert client.post('/v1/jobs', json=request('2' * 32)).status_code == 429
        invalid = request('3' * 32)
        invalid['payload']['pse'] = 'executable input'
        assert client.post('/v1/jobs', json=invalid).status_code == 400
        url = '/v1/jobs/' + identifier
        assert client.post(url + '/cancel').json()['status'] == 'cancelling'
        app.state.store.scheduler.state_value = 'CANCELLED'
        assert client.delete(url, headers=headers).json()['status'] == 'expired'
        # Switching modes must not expose jobs from an earlier token user.
        private = app.state.store.submit('alice', request('4' * 32))
        assert client.get('/v1/jobs/' + private['job_id']).status_code == 404


def test_open_authentication_requires_an_explicit_valid_setting(config):
    with pytest.raises(ValueError, match='tokens_file'):
        replace(config, tokens_file=None)
    for mode in ('anonymous', '', False, None):
        with pytest.raises(ValueError, match='authentication'):
            replace(config, authentication=mode)
    # A missing credential database in default mode must fail closed at startup.
    with pytest.raises(ValueError, match='Credential'):
        create_app(config)
