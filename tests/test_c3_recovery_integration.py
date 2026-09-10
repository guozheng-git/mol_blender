import copy
import json
from pathlib import Path
from types import SimpleNamespace
from test_gateway_recovery import pair
from test_jobs_api import payload


def test_restart_dead_tunnel_and_older_child_never_allocate(pair, monkeypatch, tmp_path):
    from blender_render import client, gateway, hpc, execution
    from blender_render.service import RenderService
    service, leaf, identifier, events, failures = pair
    failures['ack'] = True
    service._execute(service.store.claim())
    transport = gateway.configured_client('fake')
    old = execution.engine_identity()
    monkeypatch.setattr(execution, 'engine_identity', lambda:{**old, 'engine_build':'new-controller'})
    profile = json.loads(Path(hpc.__file__).with_name('profile_slurm.json').read_text())
    monkeypatch.setattr(client, 'Client', lambda *a, **k:transport)
    monkeypatch.setattr('blender_render.config.load_config', lambda:{'profiles':{'fake':profile}})
    monkeypatch.setattr(hpc, 'config_root', lambda:tmp_path/'connections')
    actions = []
    def remote(profile, action, **kwargs):
        actions.append(action)
        assert action in {'status', 'attach'}
        return {'status':'ready', 'session_id':'existing', 'node':'gpu', 'user':'user',
                'job_id':'123', 'port':1234, 'token':'t'*40, 'controller_token':'s'*40}
    monkeypatch.setattr(hpc, 'remote', remote)
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def bind(self, *a): pass
        def getsockname(self): return ('127.0.0.1', 11111)
    monkeypatch.setattr(hpc, 'socket', SimpleNamespace(socket=lambda *a, **k:Socket()))
    tunnels = []
    def tunnel(argv, **kwargs):
        tunnels.append(argv)
        return SimpleNamespace(pid=999999, poll=lambda:None, terminate=lambda:None)
    monkeypatch.setattr(hpc.subprocess, 'Popen', tunnel)
    monkeypatch.setattr(gateway, 'configured_client', client.configured_client)
    restarted = RenderService(service.store, service.backend)
    restarted._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert len(leaf.backend.calls) == 1
    assert sum(path == '/internal/v1/resolved' for _, path in events) == 1
    assert tunnels and 'start' not in actions and 'identity' not in actions


def test_timing_conflict_does_not_block_artifact_recovery(pair, monkeypatch):
    from blender_render.telemetry import record
    service, leaf, identifier, events, failures = pair
    failures['ack'] = True
    service._execute(service.store.claim())
    child = service.store.get('alice', identifier)['child_operation_id']
    leaf.store.add_timings(child, [record('rendering', 'worker', 999)])
    original = service.store.db.execute('SELECT child_timing_import FROM jobs').fetchone()[0]
    service.resume_transfer('alice', identifier)
    service._execute(service.store.claim())
    state = service.store.get('alice', identifier)
    assert state['status'] == 'done' and state['timing_integrity_conflict']
    assert service.store.db.execute('SELECT child_timing_import FROM jobs').fetchone()[0] == original
    assert len(leaf.backend.calls) == 1
    receipt = json.loads((service.store.root/'child-results'/identifier/'receipt.json').read_text()) if (service.store.root/'child-results'/identifier/'receipt.json').exists() else None
    # Terminal cleanup may remove private staging; owner-visible diagnostics survive.
    assert receipt is None or receipt['timing_integrity_conflict']


def test_integrity_latch_after_child_completion_resumes_same_transfer(pair, monkeypatch):
    from blender_render import gateway
    from blender_render.engine import RestartRequired
    from blender_render.service import RenderService
    service, leaf, identifier, events, failures = pair
    original = gateway.protect
    def changed(action):
        if action == 'transfer': raise RestartRequired('mutation before transfer')
        return original(action)
    monkeypatch.setattr(gateway, 'protect', changed)
    service._execute(service.store.claim())
    state = service.store.get('alice', identifier)
    assert state['status'] == 'transfer_pending' and state['child_completed']
    assert state['condition'] == 'restart_required'
    monkeypatch.setattr(gateway, 'protect', original)
    restarted = RenderService(service.store, service.backend)
    restarted._execute(service.store.claim())
    assert service.store.get('alice', identifier)['status'] == 'done'
    assert len(leaf.backend.calls) == 1
