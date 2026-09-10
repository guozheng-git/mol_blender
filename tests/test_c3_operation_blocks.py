import json
import pytest
from blender_render.jobs import JobStore, Policy, JobError
from blender_render.service import RenderService
from blender_render.engine import RestartRequired
from test_jobs_api import payload, Compute


def admitted(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    job = service.submit('a', source, {}, 'one')
    return store, service, source, job


def test_latch_preserves_admitted_operation_and_operator_drain(tmp_path, payload, monkeypatch):
    store, service, source, job = admitted(tmp_path, payload)
    row = store.claim()
    before = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    def changed(*args): raise RestartRequired('restart_required')
    monkeypatch.setattr(service, '_protect', changed)
    service._execute(row)
    after = dict(store.db.execute('SELECT * FROM jobs').fetchone())
    for key in ('id', 'reserved', 'charged', 'input_id', 'execution', 'operation_key'):
        assert after[key] == before[key]
    assert after['state'] == 'queued' and after['blocked_condition'] == 'restart_required'
    assert not service.backend.calls and store.claim() is None
    store.drain()
    restarted = RenderService(store, Compute())
    assert store.db.execute("SELECT value FROM settings WHERE key='admission'").fetchone()[0] == 'draining'
    with pytest.raises(JobError): restarted.submit('a', source, {'width': 512}, 'blocked-by-drain')
    store.resume()
    resumed = store.claim()
    assert resumed['id'] == job['job_id'] and resumed['reserved'] == before['reserved']
    restarted._execute(resumed)
    assert len(restarted.backend.calls) == 1
    store.close()


def test_new_admission_latch_has_no_job_or_reservation(tmp_path, payload, monkeypatch):
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    source = service.import_payload('a', payload)['input_id']
    def changed(*a): raise RestartRequired('restart_required')
    monkeypatch.setattr(service, '_protect', changed)
    with pytest.raises(RestartRequired): service.submit('a', source, {}, 'new')
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    store.close()


def test_operation_build_block_uses_runnable_fifo(tmp_path, payload):
    store, service, source, first = admitted(tmp_path, payload)
    second = service.submit('a', source, {'width': 1024}, 'two')
    third = service.submit('a', source, {'width': 512}, 'three')
    store.block(first['job_id'], 'compatibility_blocked', 'dispatch')
    assert store.claim()['id'] == second['job_id']
    assert store.claim()['id'] == third['job_id']
    assert store.claim() is None
    visible = store.get('a', first['job_id'])
    assert visible['condition'] == 'compatibility_blocked'
    assert visible['next_action'] == 'restore_exact_engine'
    store.close()


def test_ambiguous_dispatch_never_replayed_on_restart(tmp_path, payload):
    store, service, source, job = admitted(tmp_path, payload)
    store.claim()
    store.checkpoint(job['job_id'], 'dispatch', dispatched=True)
    RenderService(store, Compute())
    visible = store.get('a', job['job_id'])
    assert visible['condition'] == 'execution_unknown'
    assert store.claim() is None
    assert store.input('a', source)
    assert store.db.execute('SELECT reserved,charged FROM jobs').fetchone()[1] == 0
    store.close()


def test_explicit_unknown_cancellation_does_not_refund_possible_compute(tmp_path, payload):
    store, service, source, job = admitted(tmp_path, payload)
    store.claim()
    store.checkpoint(job['job_id'], 'dispatch', dispatched=True)
    store.block(job['job_id'], 'execution_unknown', 'dispatch')
    store.cancel('a', job['job_id'])
    row = store.db.execute('SELECT reserved,charged FROM jobs').fetchone()
    assert row['reserved'] == row['charged'] > 0
    store.close()
