"""C1 acceptance: isolated stores and fake compute only; never a live profile."""
import asyncio
import base64
import copy
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from blender_render.api import create_app
from blender_render.client import Client, configured_client
from blender_render.jobs import JobError, JobStore, Policy
from blender_render.service import RenderService, prepare_options
from blender_render.worker_http import create_worker
from test_jobs_api import payload, Compute, await_status, TOKEN
from test_execution_envelope import worker_request


def test_operation_identity_survives_changed_retry_key_and_hidden_preview(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    first = service.submit('a', source, {}, 'transport1', operation_key='intent1')
    retry = service.submit('a', source, {}, 'transport2', operation_key='intent1')
    assert retry['job_id'] == first['job_id']
    assert service.submit('a', source, {}, 'lost-key')['job_id'] == first['job_id']
    with pytest.raises(JobError, match='hidden preview'):
        service.submit('a', source, {'rough': True}, 'preview-key', operation_key='intent1')
    second = service.submit('a', source, {}, 'transport3', operation_key='intent2', rerender=True)
    assert second['job_id'] != first['job_id']
    assert service.submit('a', source, {}, 'transport4', operation_key='intent2', rerender=True)['job_id'] == second['job_id']
    assert len(store.recent('a')['jobs']) == 2 and not service.backend.calls
    assert store.recent('another-owner')['jobs'] == []
    store.close()


def test_operation_key_recovers_after_lost_child_response_and_reupload(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    first = service.submit('a', source, {}, 'gateway-outer', operation_key='gateway-outer', rerender=True)
    service._execute(store.claim())
    # Child committed and completed but the parent never received its response.
    new_source = service.import_payload('a', payload)['input_id']
    assert source != new_source
    recovered = service.submit('a', new_source, {}, 'changed-transport', operation_key='gateway-outer', rerender=True)
    assert recovered['job_id'] == first['job_id']
    assert len(service.backend.calls) == 1 and store.claim() is None
    changed = service.import_payload('a', {**payload, 'state': 2})['input_id']
    with pytest.raises(JobError, match='different request'):
        service.submit('a', changed, {}, 'other', operation_key='gateway-outer', rerender=True)
    store.close()


@pytest.mark.parametrize('raw_file', [False, True])
def test_content_reuse_binds_additional_operation_key_across_restart(tmp_path, payload, raw_file):
    store = JobStore(tmp_path)
    converter = lambda *a: payload
    service = RenderService(store, Compute(), converter=converter)
    source = service.import_payload('a', payload)['input_id'] if not raw_file else None
    def submit(key, options):
        if raw_file:
            return service.render_file('a', b'public', '.cif', 'public.cif', converter, options,
                                       operation_key=key, idempotency_key=key)
        return service.submit('a', source, options, key, operation_key=key)
    first = submit('original', {})
    assert submit('reused-key', {})['job_id'] == first['job_id']
    store.close()
    store = JobStore(tmp_path)
    service = RenderService(store, Compute(), converter=converter)
    assert submit('reused-key', {})['job_id'] == first['job_id']
    with pytest.raises(JobError, match='different request'):
        submit('reused-key', {'rough': True})
    assert len(store.recent('a')['jobs']) == 1
    store.close()


def test_admitted_file_drains_without_reopening_admission(tmp_path, payload):
    store = JobStore(tmp_path)
    converter = lambda *a: payload
    service = RenderService(store, Compute(), converter=converter)
    job = service.render_file('a', b'public', '.cif', 'public.cif', converter, {})
    store.drain()
    service._execute(store.claim())
    assert store.get('a', job['job_id'])['status'] == 'done'
    assert service.backend.calls == [job['job_id']]
    with pytest.raises(JobError, match='draining'):
        service.render_file('a', b'another', '.cif', 'public.cif', converter, {})
    with pytest.raises(JobError, match='draining'):
        store.reserve_conversion('a')
    store.close()


def test_reused_operation_alias_preserves_accepted_identity_after_cleanup(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    first = service.submit('a', source, {'preset': 'pastel'}, 'a', operation_key='a')
    accepted = {'preset': 'preset_1_interface'}
    assert service.submit('a', source, accepted, 'b', operation_key='b')['job_id'] == first['job_id']
    assert service.submit('a', source, accepted, 'b-retry', operation_key='b')['job_id'] == first['job_id']
    service._execute(store.claim())
    new_source = service.import_payload('a', payload)['input_id']
    assert new_source != source
    assert service.submit('a', new_source, accepted, 'c', operation_key='c')['job_id'] == first['job_id']
    assert not store.input_path(new_source).exists()
    store.close()
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    assert service.submit('a', new_source, accepted, 'c-retry', operation_key='c')['job_id'] == first['job_id']
    with pytest.raises(JobError, match='different request'):
        service.submit('a', new_source, {**accepted, 'rough': True}, 'changed', operation_key='c')
    assert not service.backend.calls and len(store.recent('a')['jobs']) == 1
    store.close()


def test_conversion_dispatch_still_enforces_budget_and_owner(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute(), converter=lambda *a: payload)
    job = service.render_file('a', b'public', '.cif', 'public.cif', service.converter, {})
    store.claim()
    store.drain()
    with pytest.raises(JobError, match='admitted running job'):
        store.reserve_conversion('other', job_id=job['job_id'])
    store.db.execute("UPDATE settings SET value='0' WHERE key='budget_micro'")
    with pytest.raises(JobError, match='exhausted'):
        store.reserve_conversion('a', job_id=job['job_id'])
    store.close()


def test_raw_admission_invalid_catalog_is_a_validation_error(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute(), converter=lambda *a: payload)
    with pytest.raises(JobError, match='Unknown preset'):
        service.render_file('a', b'public', '.cif', 'public.cif', service.converter, {'preset': 'missing'})
    assert store.recent('a')['jobs'] == [] and not service.backend.calls
    store.close()


def test_raw_file_keeps_catalog_snapshot_across_queued_restart(tmp_path, payload, monkeypatch):
    from blender_render import core
    options = {'preset': 'pastel', 'overrides': {'camera': {'yaw_deg': 23}}}
    expected = prepare_options(options, payload, public=False, timeout=600)
    store = JobStore(tmp_path)
    converter = lambda *a: payload
    service = RenderService(store, Compute(), converter=converter)
    job = service.render_file('a', b'public', '.cif', 'public.cif', converter, options)
    store.close()
    monkeypatch.setattr(core, 'load_and_validate_style_json', lambda *a: pytest.fail('Reloaded changed preset after admission'))
    class Capture(Compute):
        def render(self, request, *args):
            assert request['render_spec']['sha256'] == expected['render_spec']['sha256']
            assert request['style_doc'] == expected['style_doc']
            return super().render(request, *args)
    store = JobStore(tmp_path)
    service = RenderService(store, Capture(), converter=converter)
    service._execute(store.claim())
    assert store.get('a', job['job_id'])['status'] == 'done'
    assert service.backend.calls == [job['job_id']]
    store.close()


def test_explicit_false_cannot_replay_an_enabled_json_request(tmp_path, payload):
    from blender_render import core
    style = core.load_and_validate_style_json(core._default_preset_style('preset_1_interface'))
    style['color_follow']['enabled'] = True
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    first = service.submit('a', source, {'style_doc': style}, 'first')
    with pytest.raises(JobError, match='different request'):
        service.submit('a', source, {'style_doc': style, 'color_follow': False}, 'first')
    second = service.submit('a', source, {'style_doc': style, 'color_follow': False}, 'second')
    assert first['job_id'] != second['job_id']
    row = store.db.execute('SELECT options FROM jobs WHERE id=?', (second['job_id'],)).fetchone()
    assert json.loads(row['options'])['style_doc']['color_follow']['enabled'] is False
    store.close()


def test_file_handle_precedes_conversion_and_is_durable(tmp_path, payload):
    entered, release = threading.Event(), threading.Event()
    converted = []
    def convert(data, suffix):
        converted.append(data)
        entered.set()
        assert release.wait(3)
        return payload
    store = JobStore(tmp_path)
    compute = Compute()
    service = RenderService(store, compute)
    first = service.render_file('a', b'public fixture', '.cif', 'fixture.cif', convert, {}, operation_key='intent')
    assert first['stage'] == 'conversion_pending' and not converted and not compute.calls
    staged_input = store.recent('a')['jobs'][0]['job_id']
    staged_input = store.db.execute('SELECT input_id FROM jobs WHERE id=?', (staged_input,)).fetchone()[0]
    assert service.submit('a', staged_input, {}, 'compatibility-retry')['job_id'] == first['job_id']
    with sqlite3.connect(tmp_path / 'jobs.sqlite3') as db:
        assert db.execute('SELECT id FROM jobs').fetchone()[0] == first['job_id']
    service.start()
    try:
        assert entered.wait(1)
        assert store.get('a', first['job_id'])['stage'] == 'conversion'
        retry = service.render_file('a', b'public fixture', '.cif', 'renamed.cif', convert, {},
                                    operation_key='intent', idempotency_key='changed')
        assert retry['job_id'] == first['job_id'] and not compute.calls
        store.cancel('a', first['job_id'])
        # A conversion which ignores cancellation still owns the FIFO slot.
        second = service.render_file('a', b'public fixture', '.cif', 'fixture.cif', convert, {},
                                     operation_key='second', rerender=True)
        assert store.get('a', first['job_id'])['status'] == 'running'
        assert store.get('a', second['job_id'])['status'] == 'queued'
        release.set()
        await_status(store, 'a', first['job_id'], 'cancelled')
        await_status(store, 'a', second['job_id'], 'done')
        assert compute.calls == [second['job_id']]
    finally:
        release.set()
        service.stop()
        store.close()


def test_queued_file_restarts_without_recreating_operation(tmp_path, payload):
    store = JobStore(tmp_path)
    converter = lambda *_: payload
    service = RenderService(store, Compute(), converter=converter)
    job = service.render_file('a', b'fixture', '.cif', 'fixture.cif', converter, {},
                              operation_key='saved', rerender=True)
    store.close()
    store = JobStore(tmp_path)
    compute = Compute()
    service = RenderService(store, compute, converter=converter)
    service.start()
    try:
        await_status(store, 'a', job['job_id'], 'done')
        retry = service.render_file('a', b'fixture', '.cif', 'fixture.cif', converter, {},
                                    operation_key='saved', rerender=True, idempotency_key='new-transport')
        assert retry['job_id'] == job['job_id']
        assert compute.calls == [job['job_id']]
    finally:
        service.stop()
        store.close()


def test_file_validation_fails_before_compute_and_releases_reservation(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    job = service.render_file('a', b'fixture', '.cif', 'fixture.cif', lambda *_: payload,
                              {'overrides': {'camera': {'unsupported': 1}}})
    service._execute(store.claim())
    result = store.get('a', job['job_id'])
    assert result['status'] == result['stage'] == 'failed'
    assert result['estimated_cost_usd'] == 0 and not service.backend.calls
    assert store.db.execute("SELECT COUNT(*) FROM operations WHERE note='conversion'").fetchone()[0] == 1
    assert not list(store.inputs.iterdir())
    store.close()


def test_invalid_operation_controls_fail_before_staging(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    converter = lambda *_: payload
    for controls in ({'operation_key': ''}, {'operation_key': False}, {'idempotency_key': []}, {'rerender': True}):
        with pytest.raises(JobError, match='key'):
            service.render_file('a', b'fixture', '.cif', 'fixture.cif', converter, {}, **controls)
    assert not store.recent('a')['jobs'] and not list(store.inputs.iterdir())
    store.close()


def test_binary_file_admission_api_and_read_only_routes(tmp_path, payload):
    converted = []
    compute = Compute()
    store = JobStore(tmp_path)
    service = RenderService(store, compute)
    # Do not start the app lifespan/dispatcher: request must still persist a job.
    app = create_app(service, token=TOKEN, converter=lambda *args: converted.append(args) or payload)
    with TestClient(app) as http:
        service.stopping.set()
        service.wakeup.set()
        service.thread.join(2)
        connection = Client('http://localhost', TOKEN)
        def request(method, path, body=None, **kw):
            response = http.request(method, path, content=body if kw.get('raw') else None,
                                    headers={'Authorization': 'Bearer ' + TOKEN})
            assert response.status_code in (200, 202), response.text
            return response.json()
        connection.request = request
        path = tmp_path / 'public.cif'
        path.write_bytes(b'fixture')
        job = connection.render_file(path, operation_key='api')
        assert job['job_id'] and not converted and not compute.calls
        headers = {'Authorization': 'Bearer ' + TOKEN}
        for route in ('/v1/quota', '/v1/presets', '/v1/capabilities', '/v1/structures', '/v1/jobs', '/health', '/v1/jobs/' + job['job_id']):
            assert http.get(route, headers=headers).status_code == 200
        assert not converted and not compute.calls
    store.close()


@pytest.mark.parametrize('profile', ['local', 'lab'])
def test_configured_reads_never_start_services_or_compute(monkeypatch, tmp_path, profile):
    from blender_render import runtime, hpc, config
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    monkeypatch.setattr(config, 'load_config', lambda: {'profiles': {'lab': {'kind': 'hpc'}}})
    def forbidden(*a, **kw):
        pytest.fail('Read attempted startup, allocation, conversion or a runtime process')
    monkeypatch.setattr(runtime, 'ensure_local', forbidden)
    monkeypatch.setattr(hpc, 'connect', forbidden)
    monkeypatch.setattr(runtime.subprocess, 'run', forbidden)
    monkeypatch.setattr(runtime.subprocess, 'Popen', forbidden)
    connection = configured_client(profile)
    for path in ('/v1/jobs/job', '/v1/jobs', '/v1/jobs/job/result', '/v1/quota', '/v1/presets', '/v1/capabilities', '/health'):
        with pytest.raises(RuntimeError, match='existing controller|existing HPC connection'):
            connection.request('GET', path)
    assert runtime.diagnose('/missing/blender')['runtime_verified'] is False
    assert not list(tmp_path.iterdir())


def test_gateway_distinct_outer_and_retry_map_to_intended_children(monkeypatch, tmp_path, payload):
    from blender_render import gateway
    outer = JobStore(tmp_path / 'outer')
    child = JobStore(tmp_path / 'child')
    child_service = RenderService(child, Compute())
    submissions = []
    class Child:
        def status(self, identifier):
            from blender_render.execution import scoped_owner
            return child.get(scoped_owner('controller', 'a'), identifier)
        def cancel(self, identifier):
            pytest.fail('Completed child was cancelled')
        def request(self, method, path, body=None, **kwargs):
            from blender_render.execution import scoped_owner
            owner = scoped_owner('controller', 'a')
            if path == '/internal/v1/resolved':
                job = child_service.submit_resolved('controller', body)
                submissions.append(job['job_id'])
                child_service._execute(child.claim())
                return job
            identifier = path.split('/')[3]
            if '/result' in path:
                return {**child.result(owner, identifier, inventory=True), 'delivery_policy':'explicit_ack_v1'}
            name = path.split('/')[5].split('?')[0]
            if path.endswith('/ack'):
                return child.artifact_delivered(owner, identifier, name, sha256=body['sha256'])
            return child_service.artifact(owner, identifier, name)[0].read_bytes()
    # All transport/compute is replaced. This test cannot contact a destination.
    monkeypatch.setattr(gateway, 'configured_client', lambda *a, **k: Child())
    backend = gateway.GatewayBackend(outer, 'fake-only')
    service = RenderService(outer, backend)
    source = service.import_payload('a', payload)['input_id']
    first = service.submit('a', source, {}, 'first')
    second = service.submit('a', source, {}, 'second', rerender=True)
    for _ in range(2):
        service._execute(outer.claim())
    assert len(set(submissions)) == 2
    assert len(child_service.backend.calls) == 2
    request = prepare_options({}, payload, public=False, timeout=600)
    request['payload'] = payload
    with pytest.raises((RuntimeError, JobError), match='retained'):
        backend.render(request, first['job_id'], lambda: False)
    assert len(submissions) == 2
    assert outer.get('a', second['job_id'])['status'] == 'done'
    child.close()
    outer.close()


def test_worker_timeout_holds_slot_until_late_exit(payload):
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    active = [0]
    class Backend:
        def __init__(self, root):
            pass
        def render(self, *args):
            active[0] += 1
            assert active[0] == 1
            entered.set()
            assert release.wait(4)
            active[0] -= 1
            return {'status': 'completed'}
        def stop(self):
            stopped.set()
    app = create_worker('x' * 40, timeout=.05, backend_factory=Backend)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + 'x' * 40}
        assert client.post('/render/' + 'a' * 32, json=worker_request(payload, 'a'*32, timeout=.01), headers=headers).status_code == 500
        assert entered.is_set() and active == [1]
        assert client.post('/render/' + 'b' * 32, json=worker_request(payload, 'b'*32, timeout=.01), headers=headers).status_code == 429
        assert client.post('/cancel/' + 'a' * 32, headers=headers).status_code == 200
        assert active == [1]
        release.set()
        assert stopped.wait(1)
        deadline = time.monotonic() + 1
        while True:
            result = client.post('/render/' + 'c' * 32, json=worker_request(payload, 'c'*32, timeout=.01), headers=headers)
            if result.status_code != 429 or time.monotonic() >= deadline:
                break
            time.sleep(.01)
        assert result.status_code == 200  # The old bug leaked admission forever.
        assert client.post('/render/' + 'd' * 32, json=[], headers=headers).status_code == 500
        assert client.post('/render/' + 'e' * 32, json=worker_request(payload, 'e'*32, timeout=.01), headers=headers).status_code == 200


def test_worker_request_cancellation_retains_admission(payload):
    entered, release = threading.Event(), threading.Event()
    class Backend:
        def __init__(self, root): pass
        def render(self, *args):
            entered.set()
            assert release.wait(3)
            return {'status': 'completed'}
        def stop(self): pass
    async def run():
        app = create_worker('x' * 40, backend_factory=Backend)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer ' + 'x' * 40}) as client:
            request = asyncio.create_task(client.post('/render/' + 'a' * 32, json=worker_request(payload, 'a'*32, timeout=.01)))
            assert await asyncio.to_thread(entered.wait, 1)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert (await client.post('/render/' + 'b' * 32, json=worker_request(payload, 'b'*32, timeout=.01))).status_code == 429
            release.set()
            for _ in range(100):
                response = await client.post('/render/' + 'c' * 32, json=worker_request(payload, 'c'*32, timeout=.01))
                if response.status_code != 429: break
                await asyncio.sleep(.01)
            assert response.status_code == 200
    asyncio.run(run())


def test_missing_artifact_is_not_reusable_completion(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    job = service.submit('a', source, {}, 'one')
    service._execute(store.claim())
    (store.artifact_root(job['job_id']) / 'render.png').unlink()
    with pytest.raises(JobError, match='unavailable'):
        store.result('a', job['job_id'])
    assert store.get('a', job['job_id'])['status'] == 'expired'
    assert store.get('a', job['job_id'])['next_action'] == 'stop'
    assert service.submit('a', source, {}, 'lost-response')['job_id'] == job['job_id']
    assert service.backend.calls == [job['job_id']]
    store.close()


def test_checkpoint_database_upgrade_keeps_terminal_identity(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    job = service.submit('a', source, {}, 'checkpoint-key')
    service._execute(store.claim())
    store.db.execute('DROP INDEX logical_operations')
    for column in ('stage', 'stage_history', 'operation_key', 'input_sha256'):
        store.db.execute('ALTER TABLE jobs DROP COLUMN ' + column)
    store.close()
    # Same schema as the checkpoint; no live controller is involved.
    store = JobStore(tmp_path)
    compute = Compute()
    service = RenderService(store, compute)
    state = service.submit('a', source, {}, 'checkpoint-key')
    assert state['job_id'] == job['job_id'] and state['stage'] == 'result_ready'
    assert state['stage_history'] == []  # Do not invent historical timings.
    assert not compute.calls and store.claim() is None
    store.close()


def test_existing_read_connection_does_not_probe_or_repair(monkeypatch, tmp_path):
    from blender_render import runtime, config
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    root = tmp_path / 'local'
    root.mkdir()
    token = root / 'token'
    token.write_text('x' * 40)
    (root / 'session.json').write_text(json.dumps({'url': 'http://127.0.0.1:9999', 'token_file': str(token)}))
    calls = []
    monkeypatch.setattr(runtime, 'ensure_local', lambda **kw: pytest.fail('Unexpected service startup'))
    def request(self, method, path, body=None, **kwargs):
        calls.append((method, path))
        return {'status': 'done'}
    monkeypatch.setattr(Client, 'request', request)
    connection = configured_client('local')
    assert connection.status('known')['status'] == 'done'
    assert calls == [('GET', '/v1/jobs/known')]  # No unconditional quota probe.


def test_local_worker_waits_for_killed_process_before_releasing_ownership(tmp_path, monkeypatch):
    import subprocess
    from blender_render.render_worker_manager import PersistentBlenderWorker
    worker = PersistentBlenderWorker(root_dir=tmp_path, blender_exe='fake', custom_script=tmp_path / 'fake.py',
                                     use_apptainer=False, apptainer_image='')
    release, waiting = threading.Event(), threading.Event()
    class Process:
        pid = 42
        exited = False
        def poll(self): return 0 if self.exited else None
        def wait(self, timeout):
            if not self.exited:
                waiting.set()
                release.wait(.01)
                raise subprocess.TimeoutExpired('fake', timeout)
        def terminate(self): pass
        def kill(self): pass
    process = Process()
    worker._process = process
    monkeypatch.setattr(worker, '_send_request_locked', lambda *a, **k: {})
    task = threading.Thread(target=worker._stop_locked)
    task.start()
    try:
        assert waiting.wait(1)
        assert worker._process is process and task.is_alive()
    finally:
        process.exited = True
        release.set()
        task.join(1)
    assert not task.is_alive()
    assert worker._process is None


def test_worker_journal_reaches_controller_before_render_returns(tmp_path, monkeypatch):
    from blender_render.render_worker_manager import PersistentBlenderWorker
    from blender_render.operation_progress import record_stage
    worker = PersistentBlenderWorker(root_dir=tmp_path, blender_exe='fake', custom_script=tmp_path / 'fake.py',
                                     use_apptainer=False, apptainer_image='')
    monkeypatch.setattr(worker, '_ensure_started_locked', lambda: None)
    manifest = tmp_path / 'manifest.json'
    observed, notified = [], threading.Event()
    def socket_request(*a, **kw):
        if a[0].get('action') == 'ping':
            from blender_render.engine import engine_identity
            return {'engine':engine_identity(), 'runtime':{'verified':True}}
        record_stage(manifest, 'scene_build')
        record_stage(manifest, 'rendering')
        assert notified.wait(1)
        return {'status': 'completed'}
    def progress(stage):
        observed.append(stage)
        if stage == 'rendering': notified.set()
    monkeypatch.setattr(worker, '_send_request_locked', socket_request)
    worker.submit_render(structure_path=tmp_path / 'public.cif', view_json=tmp_path / 'view.json',
        style_json=tmp_path / 'style.json', output_png=tmp_path / 'render.png', run_manifest=manifest,
        timeout=1, progress=progress)
    assert observed == ['scene_build', 'rendering']


def test_stage_journal_and_final_delivery(tmp_path, payload):
    from blender_render.operation_progress import record_stage, read_stages
    manifest = tmp_path / 'manifest.json'
    for stage in ('scene_build', 'rendering', 'rendering'):
        record_stage(manifest, stage)
    assert [x['stage'] for x in read_stages(manifest)] == ['scene_build', 'rendering']
    store = JobStore(tmp_path / 'store')
    class Staged(Compute):
        def set_progress(self, callback): self.progress = callback
        def render(self, request, identifier, cancelled):
            for item in read_stages(manifest):
                self.progress(identifier, item['stage'])
            return super().render(request, identifier, cancelled)
    service = RenderService(store, Staged())
    job = service.submit('a', service.import_payload('a', payload)['input_id'], {}, 'stages')
    service._execute(store.claim())
    state = store.get('a', job['job_id'])
    assert state['stage'] == 'result_ready'
    assert [x['stage'] for x in state['stage_history']] == ['validation', 'waiting_for_compute', 'scene_build', 'rendering', 'result_ready']
    store.artifact_delivered('a', job['job_id'], 'render.png')
    assert store.get('a', job['job_id'])['stage'] == 'delivery'
    store.close()


def test_temporary_multi_object_names_do_not_change_identity(monkeypatch, payload):
    from blender_render import export
    class Cmd:
        active_state = 1
        def get_object_list(self, selection): return ['first', 'second']
        def get_object_state(self, name): return self.active_state
        def get_object_matrix(self, name): return [1, 0, 0, 0] * 4
        def get_view(self): return payload['view']['pymol_view']
        def set_view(self, view): pass
        def create(self, *a, **kw): pass
        def get_chains(self, clone): return ['A']
        def alter(self, *a, **kw): pass
        def delete(self, name): pass
    def single(cmd, selection, state, **kwargs):
        return {**copy.deepcopy(payload), 'selection': selection, 'state': state,
                'dss': {'ran': True, 'selection': selection},
                'cif_text': payload['cif_text'].replace('data_test', 'data_' + selection)}
    monkeypatch.setattr(export, '_build_single_payload', single)
    first = export.build_render_payload(Cmd(), 'chain A', 2)
    second = export.build_render_payload(Cmd(), 'chain A', 2)
    assert JobStore._content_digest(first) == JobStore._content_digest(second)
    assert '_blender_export_' not in json.dumps(first)
    assert first['state'] == 2 and 'chain A' in first['selection']
    cmd = Cmd()
    active1 = export.build_render_payload(cmd, state=-1)
    cmd.active_state = 2
    active2 = export.build_render_payload(cmd, state=-1)
    assert JobStore._content_digest(active1) != JobStore._content_digest(active2)
    for key, value in [('cif_text', first['cif_text'].replace('0.0 0.0 0.0', '1.0 0.0 0.0')),
                       ('state', 3), ('selection', 'chain B'), ('dss', {'ran': False}),
                       ('ss_hints', {'A:1': 'H'}), ('color_hints', {'A:1': '#FF0000'}),
                       ('provenance', {'object_transforms': {'first': [2]}}),
                       ('view', {**first['view'], 'fov_deg': 40})]:
        assert JobStore._content_digest({**first, key: value}) != JobStore._content_digest(first), key
