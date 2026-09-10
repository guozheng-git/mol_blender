"""Version-1 retrieval remains independent of the currently executable styles."""
import copy
import json

import pytest

from test_gateway_recovery import pair
from test_jobs_api import payload


@pytest.mark.parametrize('changed', ['catalog', 'style_schema', 'capabilities', 'bundle_schema'])
def test_upgraded_controller_recovers_without_current_consumers(pair, monkeypatch, changed):
    from blender_render import core, execution, presets, gateway, service as services
    from blender_render.client import ConnectionIntention
    service, leaf, identifier, events, failures = pair
    failures['download'] = True
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['child_completed']
    child = service.store.get('alice', identifier)['child_operation_id']
    initial_events, initial_renders = list(events), list(leaf.backend.calls)
    def forbidden(*a, **k): pytest.fail('Recovery invoked a current renderer consumer or compute')
    if changed == 'catalog': monkeypatch.setattr(presets, 'PRESET_STYLE_FILENAMES', {})
    elif changed == 'style_schema': monkeypatch.setattr(core, 'validate_style_document', forbidden)
    elif changed == 'capabilities': monkeypatch.setattr(presets, 'required_capabilities', forbidden)
    else: monkeypatch.setattr(services, 'validate_input', forbidden)
    old = execution.engine_identity()
    monkeypatch.setattr(execution, 'engine_identity', lambda:{**old, 'engine_build':'upgraded-controller'})
    monkeypatch.setattr(leaf.backend, 'render', forbidden)
    service.converter = forbidden
    connect = gateway.configured_client
    def attach(*a, **k):
        assert k['intention'] == ConnectionIntention.ATTACH_EXISTING
        assert k['controller_owner'] == 'alice'
        return connect(*a, **k)
    monkeypatch.setattr(gateway, 'configured_client', attach)
    restarted = services.RenderService(service.store, service.backend)
    restarted._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert service.store.get('alice', identifier)['child_operation_id'] == child
    assert (service.store.artifacts/identifier/'render.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert leaf.backend.calls == initial_renders
    subsequent = events[len(initial_events):]
    assert not any(path == '/internal/v1/resolved' or '/render-files/' in path or path.endswith('/cancel') for _, path in subsequent)


@pytest.mark.parametrize('field', ['version', 'bundle_sha256', 'spec_hash', 'engine_build'])
def test_invalid_stored_evidence_retains_operation(pair, field):
    from blender_render.service import RenderService
    service, leaf, identifier, events, failures = pair
    failures['download'] = True
    service._execute(service.store.claim())
    row = dict(service.store.db.execute('SELECT * FROM jobs').fetchone())
    snapshot = json.loads(row['execution'])
    if field == 'spec_hash': snapshot['request']['render_spec']['sha256'] = '0'*64
    elif field == 'engine_build': snapshot['expected_engine']['engine_build'] = '0'*64
    else: snapshot[field] = 99 if field == 'version' else '0'*64
    service.store.db.execute('UPDATE jobs SET execution=?', (json.dumps(snapshot),))
    previous = list(events)
    restarted = RenderService(service.store, service.backend)
    restarted._execute(service.store.claim())
    state = service.store.get('alice', identifier)
    assert state['status'] == 'transfer_pending' and state['condition'] == 'recovery_required'
    after = dict(service.store.db.execute('SELECT * FROM jobs').fetchone())
    for key in ('id', 'reserved', 'charged', 'input_id', 'remote_id', 'remote_completed_at'):
        assert after[key] == row[key]
    assert events == previous and len(leaf.backend.calls) == 1


@pytest.mark.parametrize('conflict', ['child_id', 'inventory', 'protocol'])
def test_conflicting_child_identity_or_inventory_never_acknowledged(pair, monkeypatch, conflict):
    from blender_render import gateway
    service, leaf, identifier, events, failures = pair
    failures['download'] = True
    service._execute(service.store.claim())
    receipt = service.store.root/'child-results'/identifier/'receipt.json'
    original_receipt = receipt.read_bytes()
    transport = gateway.configured_client('fake')
    original = transport.request
    def changed(method, path, *a, **k):
        response = original(method, path, *a, **k)
        if '/result?inventory' in path:
            response = copy.deepcopy(response)
            if conflict == 'child_id': response['job_id'] = 'wrong-child'
            elif conflict == 'protocol': response['delivery_policy'] = 'unsupported'
            else: response['expected_artifacts'][0]['sha256'] = '0'*64
        return response
    transport.request = changed
    monkeypatch.setattr(gateway, 'configured_client', lambda *a, **k:transport)
    previous = len(events)
    service.resume_transfer('alice', identifier)
    service._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'transfer_pending'
    assert receipt.read_bytes() == original_receipt
    assert not any('/artifacts/' in path for _, path in events[previous:])
    assert len(leaf.backend.calls) == 1
