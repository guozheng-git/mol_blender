"""C2: fake-only delivery, restart, and partial recovery; no compute startup."""
import base64
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blender_render.api import create_app
from blender_render.client import Client, ClientError
from blender_render.jobs import JobError, JobStore, Policy
from blender_render.service import RenderService
from test_cli_operation_routing import no_live_boundaries
from test_jobs_api import Compute, payload, TOKEN, SECOND_TOKEN


class SceneCompute(Compute):
    def render(self, request, *args):
        result = super().render(request, *args)
        if request['save_blend']:
            result['blend_base64'] = base64.b64encode(b'BLENDER fake scene').decode()
        return result


class LocalClient(Client):
    def __init__(self, api, token=TOKEN, url='http://127.0.0.1:1234'):
        super().__init__(url, token)
        self.api, self.calls = api, []
        self.fail_scene = False
        self.lose_ack = False
        self.corrupt = False

    def request(self, method, path, body=None, *, binary=False, **kwargs):
        self.calls.append((method, path))
        if self.fail_scene and method == 'GET' and '/artifacts/render.blend' in path:
            raise ClientError('Simulated interrupted scene transfer')
        result = self.api.request(method, path, json=body, headers={'Authorization': 'Bearer ' + self.token})
        if result.status_code >= 400:
            raise ClientError(result.json().get('error', 'Failed'), result.status_code)
        if self.lose_ack and path.endswith('/ack'):
            raise ClientError('Simulated lost ack response')
        return (b'corrupt' if self.corrupt else result.content) if binary else result.json()


@pytest.fixture
def rendered(tmp_path, payload):
    store = JobStore(tmp_path / 'state')
    compute = SceneCompute()
    service = RenderService(store, compute)
    source = service.import_payload('owner', payload)['input_id']
    job = service.submit('owner', source, {'save_blend': True}, 'intent')
    service._execute(store.claim())
    api = TestClient(create_app(service, token=TOKEN, tokens={TOKEN: 'owner', SECOND_TOKEN: 'other'}))
    yield store, service, api, job['job_id']
    api.close()
    store.close()


def test_partial_download_restart_recovers_same_operation(rendered, tmp_path):
    store, service, api, identifier = rendered
    client = LocalClient(api)
    client.fail_scene = True
    output = tmp_path / 'out.png'
    first = client.download(identifier, output)
    assert not first['complete'] and 'render.blend' in first['errors']
    assert Path(first['outputs']['render.png']).read_bytes().startswith(b'\x89PNG')
    assert not (store.artifact_root(identifier) / 'render.png').exists()
    second = LocalClient(api).download(identifier, output)
    assert second['complete'] and set(second['outputs']) == {'render.png', 'render.blend', 'manifest.json'}
    assert store.get('owner', identifier)['status'] == 'delivered'
    assert service.backend.calls == [identifier]
    recovered = LocalClient(api).download(identifier, tmp_path / 'new' / 'copy.png')
    assert recovered['complete'] and Path(recovered['outputs']['render.blend']).is_file()
    assert service.backend.calls == [identifier]


def test_bad_destination_fails_before_network_or_consumption(rendered, tmp_path):
    store, _, api, identifier = rendered
    parent = tmp_path / 'not-a-directory'
    parent.write_text('user work')
    client = LocalClient(api)
    with pytest.raises(OSError):
        client.download(identifier, parent / 'out.png')
    assert not client.calls
    assert store.get('owner', identifier)['status'] == 'done'


def test_corruption_does_not_ack_or_replace_user_output(rendered, tmp_path):
    store, _, api, identifier = rendered
    output = tmp_path / 'out.png'
    output.write_bytes(b'user original')
    client = LocalClient(api)
    client.corrupt = True
    result = client.download(identifier, output)
    assert not result['complete'] and not result['outputs']
    assert output.read_bytes() == b'user original'
    assert not any(path.endswith('/ack') for _, path in client.calls)
    assert store.get('owner', identifier)['status'] == 'done'
    assert LocalClient(api).download(identifier, output)['complete']


def test_ack_response_loss_is_idempotent_and_owner_scoped(rendered, tmp_path):
    store, service, api, identifier = rendered
    client = LocalClient(api)
    client.lose_ack = True
    assert client.download(identifier, tmp_path / 'out.png')['complete']
    again = LocalClient(api)
    assert again.download(identifier, tmp_path / 'out.png')['complete']
    assert not any('/artifacts/' in path and method == 'GET' for method, path in again.calls)
    with pytest.raises(ClientError):
        LocalClient(api, SECOND_TOKEN).download(identifier, tmp_path / 'out.png')
    receipt = next((Path(os.environ['BLENDER_RENDER_HOME']) / 'result-receipts').glob('*.json'))
    assert TOKEN not in receipt.read_text() and 'cif_text' not in receipt.read_text()
    assert service.backend.calls == [identifier]


def test_service_scope_cannot_reuse_other_service_receipt(rendered, tmp_path):
    _, _, api, identifier = rendered
    assert LocalClient(api).download(identifier, tmp_path / 'out.png')['complete']
    result = LocalClient(api, url='http://127.0.0.1:9999').download(identifier, tmp_path / 'other.png')
    assert not result['complete'] and 'render.png' in result['missing_required']


def test_concurrent_recovery_serializes_receipts_without_new_compute(rendered, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    _, service, api, identifier = rendered
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(LocalClient(api).download, identifier, tmp_path / name) for name in ('one.png', 'two.png')]
        results = [future.result() for future in futures]
    assert all(result['complete'] for result in results)
    assert (tmp_path / 'one.png').read_bytes() == (tmp_path / 'two.png').read_bytes()
    assert service.backend.calls == [identifier]


def test_cli_partial_result_preserves_outputs_and_reports_nonzero(rendered, tmp_path, monkeypatch, capsys):
    from blender_render import cli, client as client_module
    _, _, api, identifier = rendered
    connection = LocalClient(api)
    connection.fail_scene = True
    monkeypatch.setattr(client_module, 'configured_client', lambda *a, **kw: connection)
    assert cli.main(['result', identifier, '-o', str(tmp_path / 'out.png')]) == 1
    result = json.loads(capsys.readouterr().out)
    assert 'render.png' in result['outputs'] and not result['complete']


def test_deleted_local_copy_is_not_mistaken_for_verified_output(rendered, tmp_path):
    _, service, api, identifier = rendered
    output = tmp_path / 'out.png'
    assert LocalClient(api).download(identifier, output)['complete']
    output.unlink()
    result = LocalClient(api).download(identifier, output)
    assert not result['complete'] and 'render.png' not in result['outputs']
    assert service.backend.calls == [identifier]


def test_preview_and_ack_opt_in_do_not_consume_legacy_get_still_does(rendered):
    store, _, api, identifier = rendered
    client = LocalClient(api)
    result = client.request('GET', f'/v1/jobs/{identifier}/result')
    png = next(item for item in result['artifacts'] if item['name'] == 'render.png')
    signed = png['download_url'].split('/download/')[1]
    assert api.get('/preview/' + signed).status_code == 200
    client.request('GET', f'/v1/jobs/{identifier}/artifacts/render.png?delivery=ack', binary=True)
    assert (store.artifact_root(identifier) / 'render.png').exists()
    with pytest.raises(ClientError):
        client.request('POST', f'/v1/jobs/{identifier}/artifacts/render.png/ack', {'sha256': 'wrong'})
    client.request('GET', f'/v1/jobs/{identifier}/artifacts/render.png', binary=True)
    assert not (store.artifact_root(identifier) / 'render.png').exists()


def test_expiry_not_extended_and_missing_local_copy_never_rerenders(rendered, tmp_path):
    store, service, api, identifier = rendered
    client = LocalClient(api)
    client.fail_scene = True
    partial = client.download(identifier, tmp_path / 'out.png')
    expiry = store.get('owner', identifier)['expires_at']
    store.clock = lambda: expiry + 1
    result = LocalClient(api).download(identifier, tmp_path / 'out.png')
    assert not result['complete'] and result['outputs']['render.png'] == partial['outputs']['render.png']
    assert service.backend.calls == [identifier]


def test_drained_handoff_preserves_unexpired_results_and_deadline(tmp_path, payload):
    now = [100.0]
    store = JobStore(tmp_path / 'state', data_root=tmp_path / 'old', clock=lambda: now[0])
    service = RenderService(store, Compute())
    source = service.import_payload('owner', payload)['input_id']
    job = service.submit('owner', source, {}, 'intent')
    service._execute(store.claim())
    identifier = job['job_id']
    expiry = store.get('owner', identifier)['expires_at']
    with pytest.raises(JobError, match='Drain'):
        store.preserve_results(tmp_path / 'new')
    store.drain()
    report = store.preserve_results(tmp_path / 'new')
    assert report['job_ids'] == [identifier] and not report['expiry_extended']
    assert store.preserve_results(tmp_path / 'new') == report
    store.close()
    shutil.rmtree(tmp_path / 'old')
    replacement = JobStore(tmp_path / 'state', data_root=tmp_path / 'new', clock=lambda: now[0])
    assert replacement.get('owner', identifier)['status'] == 'done'
    assert replacement.get('owner', identifier)['expires_at'] == expiry
    assert {a['name'] for a in replacement.result('owner', identifier)['artifacts']} == {'render.png', 'manifest.json'}
    assert replacement.input_path(source).exists() is False
    now[0] = expiry + 1
    replacement.cleanup()
    assert replacement.get('owner', identifier)['status'] == 'expired'
    replacement.close()


def test_unplanned_replacement_reports_missing_not_done(tmp_path, payload):
    store = JobStore(tmp_path / 'state', data_root=tmp_path / 'old')
    service = RenderService(store, Compute())
    source = service.import_payload('owner', payload)['input_id']
    job = service.submit('owner', source, {}, 'intent')
    service._execute(store.claim())
    store.close()
    shutil.rmtree(tmp_path / 'old')
    replacement = JobStore(tmp_path / 'state', data_root=tmp_path / 'missing')
    assert replacement.get('owner', job['job_id'])['status'] == 'expired'
    with pytest.raises(JobError):
        replacement.result('owner', job['job_id'])
    replacement.close()


@pytest.mark.parametrize('terminal', ['partial', 'all', 'expiry', 'delete'])
def test_all_managed_copies_obey_tombstones(rendered, tmp_path, terminal):
    store, service, _, identifier = rendered
    expected = store.result('owner', identifier, inventory=True)['expected_artifacts']
    original = {a['name']: (store.artifact_root(identifier) / a['name']).read_bytes() for a in expected}
    store.drain()
    store.preserve_results(tmp_path / 'handoff')
    store.preserve_results(tmp_path / 'restored')
    roots = [store.artifacts, tmp_path / 'handoff' / 'artifacts', tmp_path / 'restored' / 'artifacts']
    expiry = store.get('owner', identifier)['expires_at']
    if terminal in {'partial', 'all'}:
        for item in expected if terminal == 'all' else expected[:1]:
            store.artifact_delivered('owner', identifier, item['name'], sha256=item['sha256'])
            assert all(not (root / identifier / item['name']).exists() for root in roots)
    elif terminal == 'delete':
        store.delete_result('owner', identifier)
    else:
        store.clock = lambda: expiry + 1
        store.cleanup()
    # A stale restored physical copy cannot re-authorize a tombstone.
    stale = roots[1] / identifier
    stale.mkdir(parents=True, exist_ok=True)
    (stale / 'render.png').write_bytes(original['render.png'])
    orphan = roots[1] / ('a' * 32)
    orphan.mkdir()
    (orphan / 'render.png').write_bytes(b'orphan')
    staging = roots[1] / '.handoff-abandoned'
    staging.mkdir()
    (staging / 'render.png').write_bytes(b'staging')
    store.close()
    replacement = JobStore(store.root, data_root=tmp_path / 'handoff', clock=store.clock)
    report = replacement.result('owner', identifier, inventory=True)
    assert report['expected_artifacts'][0]['availability'] != 'available'
    assert all(not (root / identifier / 'render.png').exists() for root in roots)
    assert not orphan.exists() and not staging.exists()
    assert {a['name'] for a in report['artifacts']} == ({'render.blend', 'manifest.json'} if terminal == 'partial' else set())
    # Immutable metadata and original expiry survive all state transitions.
    assert [{k: v for k, v in a.items() if k != 'availability'} for a in report['expected_artifacts']] == [{k: v for k, v in a.items() if k != 'availability'} for a in expected]
    replacement.clock = lambda: expiry + 1
    replacement.cleanup()
    replacement.cleanup()
    assert all(not (root / identifier).exists() for root in roots)
    assert service.backend.calls == [identifier]
    replacement.close()


def test_legacy_and_transient_copies_deleted_without_following_links(tmp_path, payload):
    store = JobStore(tmp_path / 'state', data_root=tmp_path / 'transient')
    service = RenderService(store, Compute())
    source = service.import_payload('owner', payload)['input_id']
    identifier = service.submit('owner', source, {}, 'intent')['job_id']
    service._execute(store.claim())
    legacy = store.root / 'artifacts' / identifier
    shutil.copytree(store.artifacts / identifier, legacy)
    outside = tmp_path / 'user-files'
    outside.mkdir()
    (outside / 'keep').write_text('user')
    (legacy / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(JobError):
        store.delete_result('other', identifier)
    store.delete_result('owner', identifier)
    assert not legacy.exists() and not (store.artifacts / identifier).exists()
    assert (outside / 'keep').read_text() == 'user'
    store.close()


def test_fresh_receipt_cannot_omit_consumed_png(rendered, tmp_path):
    store, service, api, identifier = rendered
    client_a = LocalClient(api)
    client_a.request('GET', f'/v1/jobs/{identifier}/artifacts/render.png', binary=True)
    client_b = LocalClient(api)
    result = client_b.download(identifier, tmp_path / 'fresh.png')
    assert not result['complete'] and result['missing_required'] == ['render.png']
    assert result['error_code'] == 'artifact_unavailable'
    assert result['next_action'] == 'recover_verified_local_copy_or_stop'
    assert all(method == 'GET' or path.endswith('/ack') for method, path in client_b.calls)
    assert service.backend.calls == [identifier]
    assert store.result('owner', identifier, inventory=True)['expected_artifacts'][0]['availability'] == 'delivered'


def test_receipt_detects_changed_local_png(rendered, tmp_path):
    _, service, api, identifier = rendered
    output = tmp_path / 'out.png'
    assert LocalClient(api).download(identifier, output)['complete']
    output.write_bytes(b'modified')
    result = LocalClient(api).download(identifier, output)
    assert not result['complete'] and result['missing_required'] == ['render.png']
    assert next(a for a in result['artifacts'] if a['name'] == 'render.png')['checksum_failure']
    assert service.backend.calls == [identifier]


@pytest.mark.parametrize('scene', [False, True])
def test_required_outputs_not_diagnostic_manifest(tmp_path, payload, scene):
    store = JobStore(tmp_path / 'state')
    service = RenderService(store, SceneCompute())
    source = service.import_payload('owner', payload)['input_id']
    identifier = service.submit('owner', source, {'save_blend': scene}, 'intent')['job_id']
    service._execute(store.claim())
    (store.artifact_root(identifier) / 'manifest.json').unlink()
    store.cleanup()
    assert store.get('owner', identifier)['result_available'] is True
    assert store.get('owner', identifier)['status'] == 'done'
    assert store.result('owner', identifier)['artifacts']
    api = TestClient(create_app(service, token=TOKEN, tokens={TOKEN: 'owner'}))
    result = LocalClient(api).download(identifier, tmp_path / 'out.png')
    assert result['complete'] and result['missing_required'] == []
    assert set(result['outputs']) == ({'render.png', 'render.blend'} if scene else {'render.png'})
    assert 'manifest.json' in result['errors']
    assert service.backend.calls == [identifier]
    api.close()
    store.close()


@pytest.mark.parametrize('metadata', [True, False])
def test_legacy_inventory_migration_conservative(rendered, tmp_path, metadata):
    store, service, _, identifier = rendered
    before = store.db.execute('SELECT charged,reserved FROM jobs WHERE id=?', (identifier,)).fetchone()
    if not metadata:
        (store.artifact_root(identifier) / 'manifest.json').unlink()
    with store.transaction() as db:
        db.execute('UPDATE jobs SET expected_artifacts=NULL,artifact_states=NULL WHERE id=?', (identifier,))
    store.close()
    replacement = JobStore(store.root)
    api = TestClient(create_app(RenderService(replacement, service.backend), token=TOKEN, tokens={TOKEN: 'owner'}))
    result = LocalClient(api).download(identifier, tmp_path / 'legacy.png')
    assert result['complete'] is metadata
    assert next(a for a in result['artifacts'] if a['name'] == 'render.png')['required']
    assert tuple(replacement.db.execute('SELECT charged,reserved FROM jobs WHERE id=?', (identifier,)).fetchone()) == tuple(before)
    assert service.backend.calls == [identifier]
    api.close()
    replacement.close()


def test_lost_artifact_cannot_be_restored(rendered):
    store, _, _, identifier = rendered
    png = store.artifact_root(identifier) / 'render.png'
    raw = png.read_bytes()
    png.unlink()
    store.cleanup()
    png.write_bytes(raw)
    assert store.result('owner', identifier, inventory=True)['expected_artifacts'][0]['availability'] == 'lost'
    store.cleanup()
    assert not png.exists()


def test_ack_tombstone_commits_before_interrupted_file_deletion(rendered, tmp_path, monkeypatch):
    from blender_render import artifacts
    store, _, _, identifier = rendered
    store.drain()
    store.preserve_results(tmp_path / 'handoff')
    png = store.result('owner', identifier, inventory=True)['expected_artifacts'][0]
    original = artifacts.delete_artifact_copies
    def fail(*args):
        raise OSError('Simulated interrupted cleanup')
    monkeypatch.setattr(artifacts, 'delete_artifact_copies', fail)
    with pytest.raises(OSError):
        store.artifact_delivered('owner', identifier, 'render.png', sha256=png['sha256'])
    assert json.loads(store.db.execute('SELECT artifact_states FROM jobs WHERE id=?', (identifier,)).fetchone()[0])['render.png'] == 'delivered'
    monkeypatch.setattr(artifacts, 'delete_artifact_copies', original)
    store.close()
    replacement = JobStore(store.root, data_root=tmp_path / 'handoff')
    assert replacement.result('owner', identifier, inventory=True)['expected_artifacts'][0]['availability'] == 'delivered'
    assert not (store.artifacts / identifier / 'render.png').exists()
    assert not (tmp_path / 'handoff' / 'artifacts' / identifier / 'render.png').exists()
    replacement.close()


@pytest.mark.parametrize('name', ['render.png', 'render.blend'])
def test_consumed_required_artifact_is_not_successful_content_reuse(rendered, payload, name):
    store, service, _, identifier = rendered
    store.artifact_delivered('owner', identifier, name)
    source = service.import_payload('owner', payload)['input_id']
    retry = service.submit('owner', source, {'save_blend': True}, 'new-transport-key')
    assert retry['job_id'] == identifier and retry['reused']
    assert retry['status'] == 'expired' and retry['result_available'] is False
    with pytest.raises(JobError) as error:
        store.result('owner', identifier)
    assert error.value.code == 'artifact_unavailable'
    remaining = store.result('owner', identifier, inventory=True)['artifacts']
    assert remaining and all(a['name'] != name for a in remaining)
    assert service.backend.calls == [identifier]


def test_pre_c2_partial_delivery_migration_does_not_restore_stale_png(rendered):
    store, service, _, identifier = rendered
    record = json.loads(store.db.execute('SELECT result FROM jobs WHERE id=?', (identifier,)).fetchone()[0])
    record['artifacts'] = [a for a in record['artifacts'] if a['name'] != 'render.png']
    record['bytes'] = sum(a['size'] for a in record['artifacts'])
    before = tuple(store.db.execute('SELECT charged,reserved,result_expires FROM jobs WHERE id=?', (identifier,)).fetchone())
    with store.transaction() as db:
        db.execute('UPDATE jobs SET result=?,expected_artifacts=NULL,artifact_states=NULL WHERE id=?', (json.dumps(record), identifier))
        assert not db.execute('SELECT 1 FROM delivery_receipts WHERE job_id=?', (identifier,)).fetchone()
    # The original physical PNG deliberately remains as a stale restored copy.
    store.close()
    replacement = JobStore(store.root)
    result = replacement.result('owner', identifier, inventory=True)
    assert result['expected_artifacts'][0]['availability'] == 'delivered'
    assert not (replacement.artifact_root(identifier) / 'render.png').exists()
    assert {a['name'] for a in result['artifacts']} == {'render.blend', 'manifest.json'}
    assert tuple(replacement.db.execute('SELECT charged,reserved,result_expires FROM jobs WHERE id=?', (identifier,)).fetchone()) == before
    assert service.backend.calls == [identifier]
    replacement.close()


def test_trusted_alias_roots_and_subsequent_substitution(tmp_path, payload):
    alias = tmp_path / 'alias'
    real = tmp_path / 'real'
    real.mkdir()
    alias.symlink_to(real, target_is_directory=True)
    store = JobStore(alias / 'state', data_root=alias / 'data')
    assert store.root == real / 'state' and store.artifacts == real / 'data' / 'artifacts'
    service = RenderService(store, Compute())
    source = service.import_payload('owner', payload)['input_id']
    identifier = service.submit('owner', source, {}, 'one')['job_id']
    service._execute(store.claim())
    store.drain()
    store.preserve_results(alias / 'handoff')
    store.preserve_results(real / 'handoff')
    assert store.db.execute('SELECT COUNT(*) FROM artifact_roots').fetchone()[0] == 3
    # A canonical root substituted after registration must never be followed.
    handoff = real / 'handoff' / 'artifacts'
    shutil.rmtree(handoff)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('user data')
    handoff.symlink_to(outside, target_is_directory=True)
    store.delete_result('owner', identifier)
    store.cleanup()
    assert (outside / 'keep').read_text() == 'user data'
    store.close()


def test_result_metadata_response_loss_retries_without_submission(rendered, tmp_path):
    _, service, api, identifier = rendered
    class LostResultClient(LocalClient):
        def request(self, method, path, *args, **kwargs):
            result = super().request(method, path, *args, **kwargs)
            if '/result?' in path:
                raise ClientError('Lost result metadata response')
            return result
    first = LostResultClient(api)
    with pytest.raises(ClientError, match='Lost result'):
        first.download(identifier, tmp_path / 'out.png')
    retry = LocalClient(api)
    assert retry.download(identifier, tmp_path / 'out.png')['complete']
    assert all(method == 'GET' or path.endswith('/ack') for method, path in first.calls + retry.calls)
    assert service.backend.calls == [identifier]
