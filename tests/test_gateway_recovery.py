"""Real parent/child stores and ASGI transport; fake leaf compute only."""
import json
import base64

import pytest
from fastapi.testclient import TestClient
from blender_render.api import create_app
from blender_render.client import ClientError
from blender_render.execution import scoped_owner
from blender_render.gateway import GatewayBackend
from blender_render.jobs import JobStore
from blender_render.service import RenderService
from test_jobs_api import Compute, payload, TOKEN


@pytest.fixture
def pair(tmp_path, payload, monkeypatch):
    child = JobStore(tmp_path/'child')
    class Leaf(Compute):
        def render(self, request, *args):
            return {**super().render(request, *args), 'blend_base64':base64.b64encode(b'fake scene').decode()}
    leaf = RenderService(child, Leaf())
    http = TestClient(create_app(leaf, token=TOKEN, controller_tokens={'s'*40:'controller'}))
    events, failures = [], {}
    class Transport:
        url, token = 'http://127.0.0.1:1234', 's'*40
        def request(self, method, path, body=None, **kwargs):
            events.append((method, path))
            if path.endswith('/render.blend?delivery=ack') and failures.pop('download', False):
                raise ClientError('lost transfer')
            response = http.request(method, path, json=body, headers={'Authorization':'Bearer '+self.token, 'X-Render-Owner':'alice'})
            if response.status_code >= 400:
                raise ClientError(response.json()['error'], response.status_code, response.json().get('code'))
            if '/result?inventory' in path and failures.get('identity'):
                return {**response.json(), 'bundle_sha256':'0'*64}
            if path == '/internal/v1/resolved':
                if failures.pop('cancel_before_completion', False):
                    service.store.cancel('alice', service.store.recent('alice')['jobs'][0]['job_id'])
                    return response.json()
                job = child.claim()
                if job:
                    leaf._execute(job)
                if failures.pop('submission', False):
                    raise ClientError('lost submission response')
            if path.endswith('/render.png/ack') and failures.pop('ack', False):
                raise ClientError('lost ack response')
            return response.content if kwargs.get('binary') else response.json()
        def status(self, identifier): return self.request('GET', '/v1/jobs/'+identifier)
        def cancel(self, identifier): return self.request('POST', '/v1/jobs/'+identifier+'/cancel')
    monkeypatch.setattr('blender_render.gateway.configured_client', lambda *a, **k: Transport())
    parent = JobStore(tmp_path/'parent')
    backend = GatewayBackend(parent, 'fake')
    service = RenderService(parent, backend)
    source = service.import_payload('alice', payload)['input_id']
    job = service.submit('alice', source, {'save_blend':True}, 'intent')
    yield service, leaf, job['job_id'], events, failures
    http.close()
    service.stop()
    parent.close()
    child.close()


@pytest.mark.parametrize('failure', ['download', 'ack', 'submission', 'preparation'])
def test_recovery_uses_same_completed_child(pair, failure, monkeypatch):
    service, leaf, identifier, events, failures = pair
    original = service._save_artifacts
    if failure == 'preparation':
        monkeypatch.setattr(service, '_save_artifacts', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    else:
        failures[failure] = True
    service._execute(service.store.claim())
    state = service.store.get('alice', identifier)
    assert state['status'] == 'transfer_pending'
    assert state['stage'] in {'child_observation_pending', 'child_transfer_pending', 'artifact_preparation_failed'}
    assert len(leaf.backend.calls) == 1
    assert not any(path.endswith('/cancel') for _, path in events)
    monkeypatch.setattr(service, '_save_artifacts', original)
    service.resume_transfer('alice', identifier)
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert len(leaf.backend.calls) == 1
    assert sum(path == '/internal/v1/resolved' for _, path in events) == 1
    if failure in {'download', 'ack', 'preparation'}:
        assert sum(path.endswith('/render.png?delivery=ack') for _, path in events) == 1
    assert not any(path.endswith('/cancel') for _, path in events)


def test_timing_records_are_sanitized():
    from blender_render.telemetry import sanitize
    records = [{'phase':'rendering','hop':'worker','status':'executed','duration_ms':12, 'attempt':1,
                'path':'/private/molecule','token':'secret','url':'https://signed'}]
    result = sanitize(records)
    assert result == [{k:v for k,v in records[0].items() if k not in {'path','token','url'}}]
    assert sanitize([{**records[0], 'phase':'private-molecule'}]) == []
    assert sanitize([{**records[0], 'duration_ms':float('nan')}]) == []
    assert sanitize([{**records[0], 'phase':[]}]) == []


def test_restart_recovers_only_missing_transfer_and_keeps_reservation(pair):
    service, leaf, identifier, events, failures = pair
    failures['download'] = True
    service._execute(service.store.claim())
    row = dict(service.store.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
    root = service.store.root
    service.stop()
    service.store.close()
    replacement = JobStore(root)
    restored = RenderService(replacement, GatewayBackend(replacement, 'fake'))
    try:
        restored._execute(replacement.claim())
        assert replacement.get('alice', identifier)['status'] == 'done'
        assert len(leaf.backend.calls) == 1
        assert sum(path.endswith('/render.png?delivery=ack') for _, path in events) == 1
        after = dict(replacement.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone())
        assert after['reserved'] == row['reserved']
        assert after['remote_completed_at'] == row['remote_completed_at']
        assert not list((root/'child-results').iterdir())
        assert after['timings'] and b'data_test' not in (root/'jobs.sqlite3').read_bytes()
    finally:
        restored.stop()
        # Fixture owns the last live store; never overlap controllers.
        service.store = replacement


def test_cancellation_after_child_completion_does_not_cancel_child(pair):
    service, leaf, identifier, events, failures = pair
    failures['download'] = True
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['child_completed']
    result = service.store.cancel('alice', identifier)
    assert result['status'] == 'cancelled'
    assert not any(path.endswith('/cancel') for _, path in events)
    service.resume_transfer('alice', identifier)
    assert service.store.claim() is None
    assert len(leaf.backend.calls) == 1


def test_volume_sync_before_ack_and_expiry_cleanup(pair, monkeypatch):
    service, leaf, identifier, events, failures = pair
    from blender_render import delivery
    original = delivery._atomic
    written = []
    def atomic(path, data):
        original(path, data)
        written.append(path.name)
    monkeypatch.setattr(delivery, '_atomic', atomic)
    syncs = []
    service.store.sync = lambda: syncs.append(tuple(written))
    failures['download'] = True
    service._execute(service.store.claim())
    assert any('render.png' in files and 'receipt.json' in files for files in syncs)
    service.store.clock = lambda: 1e12
    service.store.cleanup()
    assert service.store.get('alice', identifier)['status'] == 'expired'
    assert not list((service.store.root/'child-results').iterdir())
    assert len(leaf.backend.calls) == 1


def test_mismatched_child_identity_cannot_be_acknowledged(pair):
    service, leaf, identifier, events, failures = pair
    failures['identity'] = True
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'transfer_pending'
    assert not any(path.endswith('/ack') for _, path in events)
    assert len(leaf.backend.calls) == 1


def test_missing_child_diagnostic_does_not_block_required_outputs(pair, monkeypatch):
    service, leaf, identifier, events, failures = pair
    original = leaf._execute
    def complete_without_manifest(job):
        original(job)
        (leaf.store.artifacts/job['id']/'manifest.json').unlink()
    monkeypatch.setattr(leaf, '_execute', complete_without_manifest)
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert len(leaf.backend.calls) == 1


def test_cancellation_before_child_completion_reaches_child(pair):
    service, leaf, identifier, events, failures = pair
    failures['cancel_before_completion'] = True
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'cancelled'
    assert not leaf.backend.calls
    assert sum(path.endswith('/cancel') for _, path in events) == 1


@pytest.mark.parametrize('terminal', ['cancel', 'expire'])
def test_terminal_pending_transfer_never_refunds_dispatched_reservation(tmp_path, payload, terminal):
    from blender_render.jobs import Policy
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    job = service.submit('alice', service.import_payload('alice', payload)['input_id'], {}, 'once')
    store.claim()
    store.remote_checkpoint(job['job_id'], 'child_transfer_pending', completed=True)
    store.transfer_pending(job['job_id'], 'child_transfer_pending')
    before = store.quota('alice')['budget_remaining_usd']
    if terminal == 'cancel':
        store.cancel('alice', job['job_id'])
    else:
        store.clock = lambda:1e12
        store.cleanup()
    assert store.quota('alice')['budget_remaining_usd'] == before
    store.close()
