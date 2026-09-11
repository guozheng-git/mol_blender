"""Outer Slurm container IPC: actual files, injected process/signal boundaries."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
from types import SimpleNamespace

import pytest
from blender_render import worker_ipc as ipc, hpc_remote as remote


@pytest.fixture
def short_base(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='ci-', dir='/tmp') as directory:
        monkeypatch.delenv('BLENDER_RENDER_IPC_BASE', raising=False)
        monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
        monkeypatch.delenv('SLURM_TMPDIR', raising=False)
        class UnixSocket:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def bind(self,path):
                assert len(os.fsencode(path))<=100 and Path(path).parent.is_dir()
                Path(path).write_bytes(b'fake Unix socket')
        monkeypatch.setattr(remote.socket,'socket',lambda *a:UnixSocket())
        yield Path(directory)


def test_explicit_base_survives_cleanenv_and_unusable_tmp(short_base, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE', str(short_base))
    original = ipc.tempfile.mkdtemp
    def create(*args, **kwargs):
        assert Path(kwargs['dir']) == short_base, 'Attempted container /tmp fallback'
        return original(*args, **kwargs)
    monkeypatch.setattr(ipc.tempfile, 'mkdtemp', create)
    worker = ipc.WorkerIPC()
    assert worker.directory.parent == short_base
    assert len(os.fsencode(worker.socket_path)) <= ipc.MAX_SOCKET_BYTES
    worker.close()
    assert short_base.exists()


@pytest.mark.parametrize('kind', ['empty', 'relative', 'missing', 'file', 'symlink', 'ancestor_symlink',
                                  'owner', 'mode', 'unwritable', 'colon', 'comma', 'long'])
def test_explicit_invalid_never_falls_back(short_base, monkeypatch, kind):
    base = short_base/'base';base.mkdir(mode=0o700)
    value = str(base)
    if kind == 'empty': value = ''
    elif kind == 'relative': value = 'relative'
    elif kind == 'missing': value = str(short_base/'absent')
    elif kind == 'file': base.rmdir();base.write_text('file')
    elif kind == 'symlink': base.rmdir();base.symlink_to(short_base, target_is_directory=True)
    elif kind == 'ancestor_symlink':
        alias=short_base/'alias';alias.symlink_to(short_base, target_is_directory=True);value=str(alias/'base')
    elif kind == 'owner': monkeypatch.setattr(ipc.os, 'geteuid', lambda:base.stat().st_uid+1)
    elif kind == 'mode': base.chmod(0o755)
    elif kind == 'unwritable': monkeypatch.setattr(ipc.os, 'access', lambda *a:False)
    elif kind in ('colon','comma','long'):
        base=short_base/('bad:' if kind=='colon' else 'bad,' if kind=='comma' else 'x'*110);base.mkdir(mode=0o700);value=str(base)
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE', value)
    with pytest.raises(ipc.IPCUnavailable) as error:ipc.WorkerIPC()
    assert error.value.diagnostic['reason']=='worker_ipc_unavailable'
    assert error.value.diagnostic['before_execution'] is True


def test_explicit_creation_failure_does_not_fallback(short_base, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE', str(short_base))
    calls=[]
    def fail(*a, **kw):calls.append(kw['dir']);raise PermissionError('private details')
    monkeypatch.setattr(ipc.tempfile, 'mkdtemp', fail)
    with pytest.raises(ipc.IPCUnavailable):ipc.WorkerIPC()
    assert calls==[short_base]


def test_outer_base_selects_slurm_tmpdir_and_is_independent(short_base, monkeypatch):
    monkeypatch.setenv('SLURM_TMPDIR', str(short_base))
    monkeypatch.setenv('TMPDIR', '/private/durable/'+'x'*150)
    base=ipc.OuterIPCBase();first=base.directory
    assert first.parent==short_base and base.kind=='slurm_tmpdir'
    assert stat.S_IMODE(first.stat().st_mode)==0o700
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE',str(first))
    w=ipc.WorkerIPC();assert w.directory.parent==first;w.close()
    base.close();assert not first.exists()
    second=ipc.OuterIPCBase()
    try:assert second.directory!=first
    finally:second.close()


@pytest.mark.parametrize('value',['relative','/missing','/tmp/'+('x'*150)])
def test_unusable_slurm_parent_falls_back_to_validated_tmp(short_base, monkeypatch, value):
    monkeypatch.setenv('SLURM_TMPDIR',value)
    base=ipc.OuterIPCBase()
    try:assert base.kind=='tmp' and base.directory.parent==Path('/tmp').resolve()
    finally:base.close()


@pytest.fixture
def service(short_base,tmp_path,monkeypatch):
    monkeypatch.setenv('SLURM_TMPDIR',str(short_base))
    root=tmp_path/('durable-'+'x'*150);root.mkdir()
    launches=[];signals=[];handlers={};created=[]
    monkeypatch.setattr(remote.signal,'getsignal',lambda sig:signal.SIG_DFL)
    monkeypatch.setattr(remote.signal,'signal',lambda sig,fn:handlers.__setitem__(sig,fn))
    monkeypatch.setattr(remote.os,'killpg',lambda pid,sig:signals.append((pid,sig)))
    class Process:
        pid=987654;returncode=None;mode='normal';waits=0
        def poll(self):return self.returncode
        def wait(self,timeout=None):
            assert created[-1].exists(), 'Outer base deleted while child alive'
            self.waits+=1
            if self.mode=='term' and self.waits==1:
                handlers[signal.SIGTERM](signal.SIGTERM,None)
                raise subprocess.TimeoutExpired('fake',timeout)
            if self.mode=='kill' and not any(sig==signal.SIGKILL for _,sig in signals):
                if self.waits==1:handlers[signal.SIGTERM](signal.SIGTERM,None)
                raise subprocess.TimeoutExpired('fake',timeout)
            if self.mode=='unknown':raise subprocess.TimeoutExpired('fake',timeout)
            self.returncode=0 if self.mode=='normal' else -signal.SIGTERM
            return self.returncode
    def launch(argv,**kw):
        launches.append((argv,kw))
        env=[argv[i+1] for i,x in enumerate(argv[:-1]) if x=='--env']
        base=Path(next(x.split('=',1)[1] for x in env if x.startswith('BLENDER_RENDER_IPC_BASE=')))
        created.append(base)
        assert base.exists() and base.parent==short_base and not base.is_relative_to(root)
        assert str(base)+':'+str(base)+':rw' in argv and '--cleanenv' in argv
        # Model the actual contained preflight with no inherited XDG or usable /tmp.
        monkeypatch.setenv('BLENDER_RENDER_IPC_BASE',str(base))
        remote.container_ipc_preflight(root,'session',str(base),argv[argv.index('service')+4])
        worker=ipc.WorkerIPC();worker.close()
        p=Process();p.mode=state.mode;state.process=p;return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    state=SimpleNamespace(root=root,mode='normal',launches=launches,signals=signals,handlers=handlers,created=created)
    yield state
    # Test-only cleanup when the fake cannot confirm exit; application must retain.
    for path in created:
        if path.exists():__import__('shutil').rmtree(path)


def invoke(service):
    return remote.run_service(service.root, {'image':'prepared.sif','blender':'blender'}, 'session', 1234, '/captured/engine')


def test_outer_command_normal_exit_and_evidence(service):
    assert invoke(service)==0
    assert not service.created[0].exists()
    argv,kw=service.launches[0]
    assert kw['start_new_session'] is True
    assert str(service.root)+':'+str(service.root) in argv
    assert str(service.root/'service') in argv
    evidence=json.loads((service.root/'ipc-evidence.json').read_text())
    assert evidence['base_provisioned'] and evidence['bind_configured'] and evidence['container_verified']
    assert evidence['cleanup_complete'] and evidence['worker']['socket_path_bytes']<=100
    assert evidence['worker']['durable_root_independent']
    assert str(service.created[0]) not in json.dumps(evidence)


def test_start_failure_cleans_base_and_keeps_diagnostic(service,monkeypatch):
    def fail(argv,**kw):
        raise OSError('token=SECRET /private/path https://private')
    monkeypatch.setattr(remote.subprocess,'Popen',fail)
    assert invoke(service)!=0
    evidence=json.loads((service.root/'ipc-evidence.json').read_text())
    assert evidence['cleanup_complete']
    assert evidence['compatibility_diagnostic']['reason']=='worker_ipc_unavailable'
    assert 'SECRET' not in json.dumps(evidence)
    assert not list(Path(os.environ['SLURM_TMPDIR']).glob('bi-*'))


@pytest.mark.parametrize('mode',['term','kill'])
def test_termination_forwards_waits_and_cleans(service,monkeypatch,mode):
    service.mode=mode
    ticks=iter(range(0,10000,40));monkeypatch.setattr(remote.time,'monotonic',lambda:next(ticks))
    assert invoke(service)==128+signal.SIGTERM
    assert (service.process.pid,signal.SIGTERM) in service.signals
    if mode=='kill':assert (service.process.pid,signal.SIGKILL) in service.signals
    assert not service.created[0].exists()


def test_unconfirmed_child_exit_retains_base(service,monkeypatch):
    service.mode='unknown'
    ticks=iter(range(0,10000,40));monkeypatch.setattr(remote.time,'monotonic',lambda:next(ticks))
    # Simulated supervisor exception starts bounded termination.
    original=remote.subprocess.Popen
    def launch(*a,**kw):
        p=original(*a,**kw);service.handlers[signal.SIGTERM](signal.SIGTERM,None);return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    assert invoke(service)!=0
    assert service.created[0].exists()
    assert not json.loads((service.root/'ipc-evidence.json').read_text())['cleanup_complete']


def test_missing_or_changed_container_env_refuses_before_service(short_base,tmp_path,monkeypatch):
    root=tmp_path/'registry';root.mkdir()
    for value in (None,'/different'):
        if value is None:monkeypatch.delenv('BLENDER_RENDER_IPC_BASE',raising=False)
        else:monkeypatch.setenv('BLENDER_RENDER_IPC_BASE',value)
        with pytest.raises(ipc.IPCUnavailable):remote.container_ipc_preflight(root,'session',str(short_base))


def test_container_preflight_is_not_actual_worker_evidence(short_base,tmp_path,monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE',str(short_base))
    remote.container_ipc_preflight(tmp_path,'session',str(short_base))
    assert not (short_base/'worker-ipc.json').exists()
    assert json.loads((tmp_path/'ipc-container.json').read_text())['verified'] is True


def test_abrupt_container_exit_retains_possible_live_worker(service,monkeypatch):
    original=remote.subprocess.Popen
    def launch(*a,**kw):
        p=original(*a,**kw)
        service.worker=ipc.WorkerIPC() # No confirmed inner shutdown.
        return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    invoke(service)
    assert service.created[0].exists()
    assert not json.loads((service.root/'ipc-evidence.json').read_text())['cleanup_complete']
    service.worker.close()


def test_allocation_worker_actual_entry_preserves_state_and_command(service,monkeypatch):
    from blender_render import engine
    state={'session_id':'session','status':'allocating','allocator_pid':123}
    remote.write(service.root/'session.json',state)
    remote.write(service.root/'profile.json',{'image':'prepared.sif','blender':'blender'})
    for name in ('token','controller-token'):(service.root/name).write_text('existing token')
    class Socket:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,address):assert isinstance(address,str) or address==('127.0.0.1',0)
        def getsockname(self):return ('127.0.0.1',1234)
    monkeypatch.setattr(remote.socket,'socket',Socket)
    monkeypatch.setattr(remote.os,'chdir',lambda path:None)
    monkeypatch.setattr(engine,'protect',lambda action:None)
    monkeypatch.setenv('PYTHONPATH',os.environ.get('PYTHONPATH',''))
    monkeypatch.setenv('BLENDER_RENDER_SESSION_ID','prior-test-session')
    monkeypatch.setattr(remote.sys,'path',list(remote.sys.path))
    monkeypatch.setenv('SLURM_JOB_ID','12345')
    monkeypatch.setenv('SLURMD_NODENAME','gpu-node')
    monkeypatch.setattr(remote.sys,'argv',['helper','worker',str(service.root),'session'])
    assert remote.main()==0
    after=json.loads((service.root/'session.json').read_text())
    assert after['session_id']=='session' and after['job_id']=='12345' and after['allocator_pid']==123
    assert after['status']=='starting' and after['port']==1234
    assert (service.root/'token').read_text()=='existing token'
    assert (service.root/'controller-token').read_text()=='existing token'
    assert not (service.root/'service').exists() # Fake container created no database.


def test_evidence_read_is_passive_scoped_and_sanitized(tmp_path,monkeypatch,capsys):
    import io
    remote.write(tmp_path/'ipc-evidence.json',{'version':1,'session_id':'session','base_provisioned':True,
        'base_kind':'/private/token','base_path':'/private/token','worker':{'socket_path_bytes':39,'path':'secret'},
        'compatibility_diagnostic':{'reason':'worker_ipc_unavailable','observed':{'token':'secret'}}})
    monkeypatch.setattr(remote.sys,'argv',['helper','ipc_evidence'])
    monkeypatch.setattr(remote.sys,'stdin',io.StringIO(json.dumps({'profile':{'registry':str(tmp_path)},'session_id':'session'})))
    remote.main()
    value=json.loads(capsys.readouterr().out)
    assert value['worker']=={'socket_path_bytes':39}
    assert 'private' not in json.dumps(value) and 'secret' not in json.dumps(value)
    assert remote.read_ipc_evidence(tmp_path,'other')=={}


def test_typed_ipc_evidence_allowlist():
    from blender_render.compatibility import make
    value=make('worker_ipc_unavailable',phase='startup',component='worker',before_execution=True,
        observed={'base_kind':'tmp','base_available':False,'bind_configured':True,'socket_path_bytes':39,'path':'/private','token':'secret'})
    assert value['observed']=={'base_kind':'tmp','base_available':False,'bind_configured':True,'socket_path_bytes':39}


def test_no_safe_parent_is_typed_and_never_launches(service,monkeypatch):
    def deny(value):raise PermissionError('denied')
    monkeypatch.setattr(ipc,'_shared_parent',deny)
    assert invoke(service)==1 and not service.launches
    value=json.loads((service.root/'ipc-evidence.json').read_text())
    assert value['compatibility_diagnostic']['reason']=='worker_ipc_unavailable'
    assert not value['base_provisioned'] and value['cleanup_complete']


def test_failed_apptainer_before_container_verification_is_ipc_refusal(service,monkeypatch):
    original=remote.subprocess.Popen
    def launch(*a,**kw):
        p=original(*a,**kw);(service.root/'ipc-container.json').unlink()
        p.wait=lambda timeout:1;p.returncode=1;return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    assert invoke(service)==1
    value=json.loads((service.root/'ipc-evidence.json').read_text())
    assert value['compatibility_diagnostic']['reason']=='worker_ipc_unavailable'
    assert value['cleanup_complete'] and not value['container_verified']


def test_container_service_entry_checks_base_before_cli(short_base,tmp_path,monkeypatch):
    from blender_render import cli
    calls=[]
    monkeypatch.setattr(cli,'main',lambda:calls.append(list(remote.sys.argv)) or 7)
    monkeypatch.setattr(remote.sys,'argv',['helper','service',str(tmp_path),'session',str(short_base),'generation','--root',str(tmp_path/'service')])
    with pytest.raises(ipc.IPCUnavailable):remote.main()
    assert not calls
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE',str(short_base))
    assert remote.main()==7
    assert calls==[['blender-render','serve','--root',str(tmp_path/'service')]]


def test_nonzero_service_after_handshake_not_mislabeled_ipc(service,monkeypatch):
    original=remote.subprocess.Popen
    def launch(*a,**kw):
        p=original(*a,**kw);p.returncode=17;p.wait=lambda timeout:17;return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    assert invoke(service)==17
    assert 'compatibility_diagnostic' not in json.loads((service.root/'ipc-evidence.json').read_text())


def test_container_unix_bind_failure_is_typed_before_cli(short_base,tmp_path,monkeypatch):
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,*args):raise OSError('unusable mount /private with token')
    monkeypatch.setattr(remote.socket,'socket',lambda *a:Socket())
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE',str(short_base))
    with pytest.raises(ipc.IPCUnavailable) as exc:remote.container_ipc_preflight(tmp_path,'session',str(short_base))
    assert exc.value.diagnostic['reason']=='worker_ipc_unavailable'
    assert not list(short_base.glob('br-*'))
    assert not (tmp_path/'ipc-container.json').exists()

def test_old_handshake_cannot_verify_new_outer_base(service,monkeypatch):
    assert invoke(service)==0
    monkeypatch.setattr(remote,'container_ipc_preflight',lambda *a:None)
    assert invoke(service)!=0
    evidence=json.loads((service.root/'ipc-evidence.json').read_text())
    assert not evidence['container_verified']
    assert evidence['compatibility_diagnostic']['reason']=='worker_ipc_unavailable'


def test_cleanup_failure_keeps_evidence_and_restores_signals(service,monkeypatch):
    def deny(self):raise PermissionError('private data')
    monkeypatch.setattr(ipc.OuterIPCBase,'close',deny)
    assert invoke(service)==0
    assert service.created[0].exists()
    evidence=json.loads((service.root/'ipc-evidence.json').read_text())
    assert not evidence['cleanup_complete']
    assert all(handler==signal.SIG_DFL for handler in service.handlers.values())


def test_nested_evidence_does_not_skip_cleanup(service,monkeypatch):
    original=remote.subprocess.Popen
    def launch(*a,**kw):
        p=original(*a,**kw)
        (service.root/'ipc-container.json').write_text('['*2000+'0'+']'*2000)
        return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    assert invoke(service)!=0
    assert not service.created[0].exists()
    assert all(handler==signal.SIG_DFL for handler in service.handlers.values())


def test_probe_cleanup_failure_is_typed_and_not_verified(short_base,tmp_path,monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_IPC_BASE',str(short_base))
    original=ipc.WorkerIPC.close
    def close(self):
        original(self)
        raise PermissionError('probe cleanup failed')
    monkeypatch.setattr(ipc.WorkerIPC,'close',close)
    with pytest.raises(ipc.IPCUnavailable):remote.container_ipc_preflight(tmp_path,'session',str(short_base))
    assert not (tmp_path/'ipc-container.json').exists()


def test_cancel_keeps_live_inner_ipc_until_contained_shutdown(service,monkeypatch):
    original=remote.subprocess.Popen
    def launch(*a,**kw):
        p=original(*a,**kw)
        service.worker=ipc.WorkerIPC()
        oldwait=p.wait
        def wait(timeout):
            assert service.worker.directory.exists() and service.created[-1].exists()
            if p.waits==0:
                service.handlers[signal.SIGTERM](signal.SIGTERM,None)
                p.waits+=1
                raise subprocess.TimeoutExpired('fake',timeout)
            service.worker.close() # Contained controller confirms its worker exit.
            return oldwait(timeout)
        p.wait=wait;return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    invoke(service)
    assert service.worker.directory is None
    assert not service.created[-1].exists()
    assert json.loads((service.root/'ipc-evidence.json').read_text())['worker']['cleanup_complete']


def test_killed_container_probe_does_not_look_like_a_live_blender_worker(service,monkeypatch):
    original=remote.subprocess.Popen
    def launch(*a,**kw):
        p=original(*a,**kw)
        service.probe=ipc.WorkerIPC(purpose='probe')
        service.handlers[signal.SIGTERM](signal.SIGTERM,None)
        p.returncode=-signal.SIGTERM
        p.wait=lambda timeout:p.returncode
        return p
    monkeypatch.setattr(remote.subprocess,'Popen',launch)
    assert invoke(service)==128+signal.SIGTERM
    assert not service.created[0].exists()
    assert json.loads((service.root/'ipc-evidence.json').read_text())['cleanup_complete']
