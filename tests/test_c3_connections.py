import json
import pytest
from blender_render import hpc


def test_explicit_intention_and_conflict_before_network(monkeypatch):
    from blender_render.client import ConnectionIntention, _connect_client
    monkeypatch.setattr('blender_render.config.load_config', lambda:pytest.fail('conflicting intention reached configuration'))
    with pytest.raises(ValueError, match='Conflicting'):
        _connect_client('test', intention=ConnectionIntention.START_OR_ALLOCATE, read_only=True)


def test_inspect_cached_is_passive(tmp_path, monkeypatch):
    from blender_render.client import ConnectionIntention
    monkeypatch.setattr(hpc, 'config_root', lambda:tmp_path)
    profile = json.loads(__import__('pathlib').Path(hpc.__file__).with_name('profile_slurm.json').read_text())
    with pytest.raises(RuntimeError, match='existing'):
        hpc.connect('test', profile, intention=ConnectionIntention.INSPECT_CACHED)
    assert list(tmp_path.iterdir()) == []


def test_timing_identity_import_is_atomic_and_non_destructive(tmp_path):
    from blender_render.jobs import JobStore
    from blender_render.telemetry import record
    store = JobStore(tmp_path)
    store.db.execute("INSERT INTO jobs(id,owner,input_id,idempotency_key,request_hash,options,state,submitted,reserved,remote_id) VALUES ('parent','a','i','k','h','{}','transfer_pending',0,10,'child')")
    original = [record('rendering', 'worker', 1)]
    assert store.import_child_timings('parent', 'child', original) is False
    assert store.import_child_timings('parent', 'child', original) is False
    assert store.import_child_timings('parent', 'child', [record('rendering', 'worker', 2)]) is True
    row = store.db.execute('SELECT * FROM jobs').fetchone()
    assert json.loads(row['timings']) == original
    marker = json.loads(row['child_timing_import'])
    assert marker['child_operation_id'] == 'child' and marker['record_count'] == 1
    assert len(marker['sha256']) == 64 and marker['imported_at'] is not None
    assert row['state'] == 'transfer_pending' and row['reserved'] == 10
    store.close()
