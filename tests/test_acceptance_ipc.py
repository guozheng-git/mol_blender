"""Exercise the actual runner's prerequisites-only path with no external calls."""
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from blender_render import config, engine, worker_ipc
from test_hpc_negotiation import profile


@pytest.mark.parametrize('available', [True, False])
def test_preflight_reports_ipc_without_limiting_durable_registry(tmp_path, monkeypatch, available):
    main = runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))['main']
    root = tmp_path/'checkout'
    (root/'examples').mkdir(parents=True)
    (root/'examples/1sps_pose.pse').write_bytes(b'not converted in preflight')
    executable = tmp_path/'fake-runtime'
    executable.write_text('never executed')
    executable.chmod(0o700)
    doc = profile(tmp_path)
    doc['registry'] = str(tmp_path/('durable-registry-'+'x'*180))
    monkeypatch.setitem(main.__globals__, 'ROOT', root)
    monkeypatch.setattr(config, 'load_config', lambda:{'blender':str(executable),
        'pymol_python':str(executable), 'profiles':{'private':doc}})
    monkeypatch.setattr(engine, 'reviewed_checkout', lambda path:{'clean':True})
    def git(command, **kwargs):
        assert command == ['git', 'rev-parse', 'HEAD']
        return 'reviewed\n'
    monkeypatch.setattr(subprocess, 'check_output', git)
    output = tmp_path/'evidence'
    monkeypatch.setattr(sys, 'argv', ['acceptance', '--commit', 'reviewed', '--profile', 'private',
        '--output', str(output), '--prerequisites-only'])
    if not available:
        def deny(*a, **kw):
            raise PermissionError('no IPC storage')
        monkeypatch.setattr(worker_ipc.tempfile, 'mkdtemp', deny)
    assert main() == (0 if available else 2)
    report = json.loads((output/'acceptance.json').read_text())
    if available:
        assert report['worker_ipc']['durable_root_independent']
        assert report['worker_ipc']['socket_path_bytes'] <= worker_ipc.MAX_SOCKET_BYTES
        assert not report['blocked_reasons']
    else:
        assert report['blocked_reasons'][0].startswith('Worker IPC prerequisite:')


def test_acceptance_collects_outer_cleanup_without_compute(tmp_path,monkeypatch):
    from blender_render import hpc
    api=runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))
    calls=[]
    ready={'version':1,'session_id':'session','base_provisioned':True,'bind_configured':True,
           'container_verified':True,'child_exited':True,'cleanup_complete':True,
           'worker':{'socket_path_bytes':39,'durable_root_independent':True,'cleanup_complete':True}}
    replies=iter([{},ready])
    def read(profile,action,**kw):
        assert action=='ipc_evidence' and kw=={'session_id':'session'}
        calls.append(action);return next(replies)
    monkeypatch.setattr(hpc,'remote',read)
    monkeypatch.setattr(api['time'],'sleep',lambda _:None)
    result=api['collect_outer_ipc']({},'session',tmp_path)
    assert result==ready and len(calls)==2
    assert json.loads((tmp_path/'outer-ipc.json').read_text())==ready


def test_acceptance_missing_outer_evidence_cannot_pass(tmp_path,monkeypatch):
    from blender_render import hpc
    api=runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))
    monkeypatch.setattr(hpc,'remote',lambda *a,**kw:{})
    with pytest.raises(RuntimeError,match='IPC'):
        api['collect_outer_ipc']({},'session',tmp_path,timeout=0)


@pytest.mark.parametrize('field',['base_provisioned','bind_configured','container_verified','child_exited','cleanup_complete','worker'])
def test_acceptance_requires_actual_worker_and_cleanup_evidence(field):
    api=runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))
    value={'base_provisioned':True,'bind_configured':True,'container_verified':True,'child_exited':True,'cleanup_complete':True,
           'worker':{'socket_path_bytes':39,'durable_root_independent':True,'cleanup_complete':True}}
    api['require_outer_ipc'](value)
    value.pop(field)
    with pytest.raises(RuntimeError,match='IPC'):api['require_outer_ipc'](value)


def test_evidence_write_failure_still_cleans_owned_tunnel_tokens(tmp_path,monkeypatch):
    from blender_render import hpc
    api=runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))
    for parent in (tmp_path,tmp_path/'home/hpc/c3-private'):
        parent.mkdir(parents=True,exist_ok=True)
        for name in ('token','controller-token'):(parent/name).write_text('secret')
    actions=[];signals=[]
    def remote(profile,action,**kw):
        actions.append(action)
        if action=='status':return {'session_id':'session'}
        if action=='stop':return {}
        assert action=='ipc_evidence'
        return {'child_exited':True,'cleanup_complete':True}
    monkeypatch.setattr(hpc,'remote',remote)
    monkeypatch.setattr(api['os'],'killpg',lambda pid,sig:signals.append(pid))
    original=Path.write_text
    def write(path,*a,**kw):
        if path.name=='acceptance.json':raise PermissionError('output storage failed')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'write_text',write)
    assert api['cleanup_validation'](tmp_path,None,True,{'tunnel_pid':123},{},{})
    assert actions==['status','stop','ipc_evidence'] and signals==[123]
    assert not list(tmp_path.rglob('*token'))
