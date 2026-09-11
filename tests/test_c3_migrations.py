import json
from blender_render.jobs import JobStore
from blender_render.service import RenderService
from test_jobs_api import payload, Compute


def test_pre_correction_database_preserves_lifecycle_and_durable_evidence(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    identifiers = {}
    for index, state in enumerate(('queued','running','transfer_pending','done','delivered','cancelled','expired')):
        source = service.import_payload('a', payload)['input_id']
        job = service.submit('a', source, {'width':512+index}, state, operation_key=state)
        identifiers[state] = job['job_id']
        if state in {'done','delivered','cancelled','expired'}:
            row = dict(store.db.execute('SELECT * FROM jobs WHERE id=?', (job['job_id'],)).fetchone())
            store.db.execute("UPDATE jobs SET state='running' WHERE id=?", (job['job_id'],))
            service._execute(row)
        store.db.execute('UPDATE jobs SET state=? WHERE id=?', (state, job['job_id']))
    # Include an actual acknowledgement so receipt preservation is non-vacuous.
    import hashlib
    png = store.artifacts/identifiers['done']/'render.png'
    store.db.execute('INSERT INTO delivery_receipts(owner,job_id,name,sha256) VALUES(?,?,?,?)',
        ('a', identifiers['done'], 'render.png', hashlib.sha256(png.read_bytes()).hexdigest()))
    assert store.db.execute('SELECT COUNT(*) FROM delivery_receipts').fetchone()[0] == 1
    # Freeze the evidence that predates the correction, including aliases and
    # the actual C2 inventory/receipts tables, rather than reconstructing jobs.
    tables = ('operation_keys', 'submission_keys', 'delivery_receipts')
    evidence = {table:[tuple(row) for row in store.db.execute('SELECT * FROM '+table)] for table in tables}
    rows = {r['id']:dict(r) for r in store.db.execute('SELECT * FROM jobs')}
    store.db.execute('DROP INDEX runnable_jobs')
    for name in ('blocked_condition','resume_phase','dispatch_fenced','child_timing_import','timing_conflict','recovery_ready','conversion_reserved','compatibility_diagnostic'):
        store.db.execute('ALTER TABLE jobs DROP COLUMN '+name)
    store.close()
    migrated = JobStore(tmp_path)
    for table in tables:
        assert [tuple(row) for row in migrated.db.execute('SELECT * FROM '+table)] == evidence[table]
    for row in migrated.db.execute('SELECT * FROM jobs'):
        for field in ('id','reserved','charged','operation_key','expected_artifacts','artifact_states','timings','execution'):
            assert row[field] == rows[row['id']][field]
    RenderService(migrated, Compute())
    for state in ('queued','running','transfer_pending'):
        visible = migrated.get('a', identifiers[state])
        assert visible['condition'] == 'recovery_required'
        assert visible['next_action'] == 'recover_operation'
    assert migrated.claim() is None
    migrated.close()
