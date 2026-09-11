"""No compute: acceptance's failure reporter operates on an admitted job only."""
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
from test_jobs_api import payload


@pytest.mark.parametrize('status_available', [True, False])
def test_failure_saved_without_submit_or_cleanup(tmp_path, status_available):
    api = runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))
    class Client:
        calls = 0
        def status(self, identifier):
            self.calls += 1
            assert identifier == 'a'*32
            if not status_available:raise RuntimeError('https://secret /private/molecule')
            return {'condition':'compatibility_blocked', 'next_action':'restore_exact_engine',
                'error':'secret', 'compatibility_diagnostic':{'version':1,'reason':'ccd_missing',
                    'phase':'startup','component':'ccd','before_execution':True,'observed':{'path':'/secret'}}}
    log = tmp_path/'worker.stdout.log';log.write_text('private molecule token https://credential')
    client = Client()
    api['compatibility_failure'](tmp_path, client, 'a'*32, 'slurm', 'b'*40,
        allocation_id='123', log_paths={'worker.stdout.log':log})
    saved = json.loads((tmp_path/'validation-result.json').read_text())
    assert saved['condition'] == 'compatibility_blocked'
    assert saved['candidate_sha'] == 'b'*40 and saved['job_id']=='a'*32
    assert saved['compatibility_diagnostic']['reason'] == ('ccd_missing' if status_available else 'unknown_compatibility')
    assert client.calls == 1 and log.exists()
    assert not any(secret in (tmp_path/'validation-result.json').read_text() for secret in ('https://','molecule','/secret'))
    hashes = json.loads((tmp_path/'compatibility-log-hashes.json').read_text())
    assert hashes[0]['sha256'] and hashes[0]['name'] == 'worker.stdout.log'
    assert 'private molecule' not in json.dumps(hashes)


@pytest.mark.parametrize('collection_fails', [False, True])
def test_actual_runner_failure_precedes_cleanup_and_never_allocates(tmp_path, payload, monkeypatch, collection_fails):
    import subprocess
    import sys
    from blender_render import config, engine, client, hpc
    from test_hpc_negotiation import profile
    api = runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))
    main = api['main']; root = tmp_path/'checkout'; (root/'examples').mkdir(parents=True)
    (root/'examples/1sps_pose.pse').write_bytes(b'fixture boundary')
    fake = tmp_path/'fake'; fake.touch(); fake.chmod(0o700)
    output=tmp_path/'output'
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path/'home'))
    monkeypatch.setattr(config,'load_config',lambda:{'blender':str(fake),'pymol_python':str(fake),'profiles':{'p':profile(tmp_path)}})
    monkeypatch.setattr(engine,'reviewed_checkout',lambda path:{'clean':True})
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**kw:'a'*40+'\n')
    def convert(argv, **kw):
        assert argv[1].endswith('export_fixture.py')
        Path(argv[-1]).write_text(json.dumps(payload))
    monkeypatch.setattr(subprocess,'run',convert)
    monkeypatch.setattr(hpc,'remote',lambda profile, action: {'status':'absent'} if action=='status' else pytest.fail('unexpected external action'))
    monkeypatch.setattr(hpc,'connect',lambda *a,**kw:pytest.fail('allocation after failure'))
    events=[]
    class Process:
        def poll(self):return None
        def terminate(self):
            events.append('cleanup')
            if not collection_fails:assert (output/'validation-result.json').is_file()
        def wait(self,**kw):return 0
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:Process())
    class Probe:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def bind(self,*a):pass
        def getsockname(self):return ('127.0.0.1',1234)
    monkeypatch.setitem(main.__globals__,'socket',SimpleNamespace(socket=Probe))
    class Client:
        def __init__(self,url,token):self.url=url;self.timeout=30
        def request(self,method,path,*a):
            assert path in ('/health','/internal/v1/resolved')
            return {'job_id':'b'*32}
        def wait(self,*a,**kw):raise client.ClientError('original refusal',409,'compatibility_blocked')
        def status(self,identifier):
            assert 'cleanup' not in events
            events.append('status')
            return {'condition':'compatibility_blocked'}
    monkeypatch.setattr(client,'Client',Client)
    if collection_fails:
        def fail(*a,**kw):events.append('collection failed');raise OSError('private path')
        monkeypatch.setitem(main.__globals__,'compatibility_failure',fail)
    monkeypatch.setitem(main.__globals__,'ROOT',root)
    monkeypatch.setattr(sys,'argv',['acceptance','--commit','a'*40,'--profile','p','--output',str(output)])
    with pytest.raises(client.ClientError,match='original refusal'):main()
    assert events == (['collection failed','cleanup'] if collection_fails else ['status','cleanup'])
    assert not (output/'token').exists() and not (output/'controller-token').exists()
