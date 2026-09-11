"""Small stdlib-only helper on the cluster login node and allocated GPU node."""
from __future__ import annotations
import fcntl
import getpass
import json
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen


def parse_sbatch(response):
    match = re.fullmatch(r'([1-9][0-9]*)(?:;([A-Za-z0-9_][A-Za-z0-9_.-]*))?', response.strip())
    if not match:
        raise ValueError('Ambiguous sbatch submission response; inspect scheduler before any new allocation')
    return match.group(1), match.group(2)


def scheduler(command, state, *args):
    job = str(state['job_id'])
    cluster = state.get('cluster')
    if not re.fullmatch(r'[1-9][0-9]*', job) or cluster and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', cluster):
        raise ValueError('Invalid scheduler identity')
    return [command, *(['--clusters=' + cluster] if cluster else []), *args, job]


def write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value))
    temp.chmod(0o600)
    os.replace(temp, path)


def check_session(root):
    path = root / "session.json"
    if not path.exists():
        return {"status":"absent"}
    state = json.loads(path.read_text())
    if state.get("job_id"):
        result = subprocess.run(scheduler('squeue', state, '-h', '-o', '%T', '-j'), capture_output=True, text=True, timeout=10)
        if result.returncode:
            return {**state, 'status': 'unavailable', 'error': 'Scheduler observation unavailable; allocation ownership retained'}
        if result.stdout.strip() not in {"RUNNING", "PENDING", "CONFIGURING"}:
            state.update(status="failed", error="GPU allocation ended; reconnect to restart it, or use mode='local'.")
    else:
        if state.get('status') == 'submission_unknown':
            return state
        try:
            os.kill(state["allocator_pid"], 0)
        except (ProcessLookupError, KeyError):
            state.update(status="failed", error="GPU allocator stopped. Check allocation.log and the cluster resource profile.")
    if state.get("node") and state["status"] not in {"failed", "absent"}:
        # Health is checked on the GPU node to keep the service loopback-only.
        code = ("from urllib.request import Request,urlopen; from pathlib import Path; "
                "token=Path(" + repr(str(root / "token")) + ").read_text().strip(); "
                "print(urlopen(Request('http://127.0.0.1:" + str(state["port"]) +
                "/health',headers={'Authorization':'Bearer '+token}),timeout=2).read().decode())")
        profile = json.loads((root / "profile.json").read_text())
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", state["node"], shlex.join([sys.executable,"-c",code])]
        if profile.get("transport") == "slurm":
            command = ["srun", "--jobid=" + str(state["job_id"]), "--overlap", "-N1", "-n1", sys.executable, "-c", code]
            if state.get('cluster'):
                command[1:1] = ['--clusters=' + state['cluster']]
        response = subprocess.run(command, capture_output=True, text=True, timeout=8)
        if response.returncode == 0:
            health = json.loads(response.stdout)
            if health.get("status") == "ok":
                state.update(status="ready", queue_depth=health.get("queue_depth",0), last_heartbeat=time.time())
                state['cached_observation'] = health
        elif time.time() - state.get("worker_started",time.time()) > 60:
            state.update(status="failed", error="GPU service health check failed; reconnect to allocate a new worker.")
    if state["status"] == "ready":
        state["token"] = (root / "token").read_text()
        if (root / 'controller-token').exists():
            state['controller_token'] = (root / 'controller-token').read_text()
    return state


def _read_ipc_json(path):
    """Bounded private evidence reads; never follow an evidence-file symlink."""
    import stat
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_size > 8192:
                return {}
            value = json.loads(stream.read(8193))
            return value if type(value) is dict else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _worker_ipc_evidence(value):
    result = {}
    for key in ('durable_root_independent', 'cleanup_complete'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    size = value.get('socket_path_bytes')
    if type(size) is int and 1 <= size <= 100:
        result['socket_path_bytes'] = size
    if value.get('directory_mode') == '0700':
        result['directory_mode'] = '0700'
    return result


def read_ipc_evidence(root, session_id):
    """Administrative read; no package import, scheduler call, or repair."""
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session_id):
        return {}
    value = _read_ipc_json(root/'ipc-evidence.json')
    if type(value.get('version')) is not int or value['version'] != 1 or value.get('session_id') != session_id:
        return {}
    result = {'version':1, 'session_id':session_id}
    for key in ('base_provisioned', 'bind_configured', 'container_verified', 'child_exited', 'cleanup_complete'):
        if type(value.get(key)) is bool:
            result[key] = value[key]
    if value.get('base_kind') in ('slurm_tmpdir', 'tmp'):
        result['base_kind'] = value['base_kind']
    if type(value.get('exit_code')) is int and -255 <= value['exit_code'] <= 255:
        result['exit_code'] = value['exit_code']
    if type(value.get('worker')) is dict:
        result['worker'] = _worker_ipc_evidence(value['worker'])
    diagnostic = value.get('compatibility_diagnostic')
    if type(diagnostic) is dict and diagnostic.get('reason') == 'worker_ipc_unavailable':
        result['compatibility_diagnostic'] = dict(version=1, reason='worker_ipc_unavailable', phase='startup',
            component='worker', before_execution=True, expected={}, observed={}, log_refs=[])
    return result


def container_ipc_preflight(root, session_id, expected_base, generation=None):
    """Run inside --cleanenv before importing/starting the contained service."""
    from blender_render.worker_ipc import BASE_ENV, IPCUnavailable, WorkerIPC
    if os.environ.get(BASE_ENV) != expected_base:
        raise IPCUnavailable('Outer container did not receive its required IPC base')
    probe = None
    try:
        try:
            probe = WorkerIPC(purpose='probe')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.bind(str(probe.socket_path))
            observed = {'socket_path_bytes':len(os.fsencode(probe.socket_path)),
                        'durable_root_independent':True, 'directory_mode':'0700'}
        finally:
            if probe is not None:
                probe.close()
        # This success marker belongs to one invocation, and only follows full
        # probe cleanup. A same-session stale marker cannot verify another base.
        write(root/'ipc-container.json', {'version':1, 'session_id':session_id, 'generation':generation,
                                         'verified':True, 'probe':observed})
    except OSError as exc:
        raise IPCUnavailable('Outer container IPC verification failed') from exc


def _wait_service(child, pending):
    """Forward cancellation once, escalate after 20s, and bound exit confirmation.

    An unconfirmed exit returns None. Its IPC base must remain owned and intact.
    Signal handlers only set flags, including while Popen is assigning the child.
    """
    sent = False
    killed_at = None
    while True:
        if pending['signal'] is not None:
            now = time.monotonic()
            if not sent:
                try: os.killpg(child.pid, pending['signal'])
                except ProcessLookupError: pass
                sent = True
                pending['at'] = now
            elif killed_at is None and now - pending['at'] >= 20:
                try: os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                killed_at = now
            elif killed_at is not None and now - killed_at >= 10:
                return child.poll()
        try:
            return child.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            pass


def run_service(root, profile, session_id, port, engine_root):
    """Own the allocation's IPC base until the outer service child has exited."""
    from blender_render.worker_ipc import OuterIPCBase, IPCUnavailable
    base = child = None
    pending = {'signal':None, 'at':None}
    generation = secrets.token_hex(16)
    evidence = {'version':1, 'session_id':session_id, 'base_provisioned':False,
                'bind_configured':False, 'container_verified':False,
                'child_exited':False, 'cleanup_complete':False}
    previous = {}
    exit_code = None
    wait_completed = False
    def terminate(signum, frame):
        if pending['signal'] is None:
            pending['signal'] = signum
    try:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, terminate)
        base = OuterIPCBase()
        evidence.update(base_provisioned=True, base_kind=base.kind)
        ipc_path = str(base.directory)
        command = ['apptainer', 'exec', '--nv', '--cleanenv', '--bind', engine_root+':'+engine_root,
                   '--bind', str(root)+':'+str(root), '--bind', ipc_path+':'+ipc_path+':rw',
                   '--env', 'PYTHONPATH='+engine_root+':/opt/runtime/site:/opt',
                   '--env', 'BLENDER_RENDER_SITE=/opt/runtime/site',
                   '--env', 'BLENDER_RENDER_SESSION_ID='+session_id,
                   '--env', 'BLENDER_RENDER_IPC_BASE='+ipc_path,
                   profile['image'], '/usr/local/bin/python', engine_root+'/blender_render/hpc_remote.py',
                   'service', str(root), session_id, ipc_path, generation,
                   '--root', str(root/'service'), '--token-file', str(root/'token'), '--port', str(port),
                   '--controller-token-file', str(root/'controller-token'),
                   '--blender', profile.get('blender', 'blender'), '--idle-timeout', '900']
        evidence['bind_configured'] = True
        write(root/'ipc-evidence.json', evidence)
        if pending['signal'] is not None:
            exit_code = -pending['signal']
        else:
            from blender_render.engine import protect
            protect('startup')
            child = subprocess.Popen(command, start_new_session=True)
            exit_code = _wait_service(child, pending)
            wait_completed = True
    except (IPCUnavailable, OSError) as exc:
        # No service was started when provisioning/Popen fails. Do not relabel
        # any ambiguous running child as a known pre-execution failure.
        if child is None:
            evidence['compatibility_diagnostic'] = IPCUnavailable('Outer IPC startup unavailable').diagnostic
        exit_code = 1
    finally:
        try:
            try:
                if child is not None and child.poll() is None and not wait_completed:
                    pending['signal'] = pending['signal'] or signal.SIGTERM
                    confirmed = _wait_service(child, pending)
                    if confirmed is not None:
                        exit_code = confirmed
                exited = child is None or child.poll() is not None
                evidence['child_exited'] = exited
                container = _read_ipc_json(root/'ipc-container.json')
                evidence['container_verified'] = (container.get('session_id') == session_id
                    and container.get('generation') == generation and container.get('verified') is True)
                if base is not None:
                    evidence['worker'] = _worker_ipc_evidence(_read_ipc_json(base.directory/'worker-ipc.json'))
                    # A leftover directory cannot prove a separate-session
                    # Blender child exited after abrupt contained-service death.
                    if exited and not list(base.directory.glob('br-*')):
                        base.close()
                        evidence['cleanup_complete'] = True
                else:
                    evidence['cleanup_complete'] = True
                if child is not None and exited and not evidence['container_verified'] and pending['signal'] is None:
                    evidence['compatibility_diagnostic'] = IPCUnavailable('Outer container IPC verification unavailable').diagnostic
                    exit_code = exit_code or 1
            except Exception:
                # Retain potentially live IPC and the original exit/failure.
                # Cleanup failures are not pre-execution compatibility failures.
                evidence['cleanup_complete'] = False
            if exit_code is not None:
                evidence['exit_code'] = exit_code
            try:
                write(root/'ipc-evidence.json', evidence)
            except Exception:
                print('Outer IPC lifecycle evidence unavailable', file=sys.stderr)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
    return 124 if exit_code is None else 128-exit_code if exit_code < 0 else exit_code


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    action = sys.argv[1]
    if action == 'service':
        root, session_id, expected_base = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
        container_ipc_preflight(root, session_id, expected_base, sys.argv[5])
        from blender_render.cli import main as service_main
        sys.argv = ['blender-render', 'serve', *sys.argv[6:]]
        return service_main()
    if action == "worker":
        root = Path(sys.argv[2])
        profile = json.loads((root / "profile.json").read_text())
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",0)); port = probe.getsockname()[1]
        with (root / 'registry.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads((root / 'session.json').read_text())
            if len(sys.argv) < 4 or sys.argv[3] != state['session_id']:
                raise RuntimeError('Stale allocation cannot replace the active controller')
            state.update(status="starting", node=os.environ.get("SLURMD_NODENAME") or socket.gethostname().split(".")[0], user=getpass.getuser(), port=port,
                         job_id=os.environ["SLURM_JOB_ID"], worker_started=time.time())
            write(root / 'session.json', state)
        os.environ["BLENDER_RENDER_SESSION_ID"] = state["session_id"]
        engine_root = str(Path(__file__).resolve().parent.parent)
        from blender_render.engine import protect
        protect('startup')
        os.chdir(engine_root)
        os.environ["PYTHONPATH"] = engine_root + os.pathsep + os.environ.get("PYTHONPATH","")
        return run_service(root, profile, state['session_id'], port, engine_root)
    data = json.load(sys.stdin)
    profile = data["profile"]
    if action == 'start':
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from blender_render.hpc import validate_profile
        validate_profile(profile)
        from blender_render.engine import engine_identity, require_compatible
        expected = data.get('expected_engine') or profile.get('expected_engine')
        if expected:
            require_compatible(expected, engine_identity(), [])
    if action == 'identity':
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from blender_render.engine import engine_identity, reviewed_checkout
        identity = {'engine': engine_identity(), 'python_supported': sys.version_info >= (3, 10)}
        if profile.get('expected_commit'):
            result = subprocess.run(['git', '-C', profile['remote_root'], 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True, timeout=5)
            clean = reviewed_checkout(profile['remote_root'])
            identity['commit'] = result.stdout.strip()
            identity['tracked_clean'] = clean['clean']
            identity['reviewed_clean'] = clean['clean']
        print(json.dumps(identity))
        return
    root = Path(profile["registry"]).expanduser()
    if action == 'ipc_evidence':
        print(json.dumps(read_ipc_evidence(root, data.get('session_id'))))
        return
    if action == 'status':
        # A read must not create a registry or enter allocation/repair logic.
        state = check_session(root)
        state.pop('token', None)
        state.pop('controller_token', None)
        print(json.dumps(state))
        return
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "registry.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state = check_session(root)
        if action == "stop":
            if state['status'] == 'submission_unknown' and not state.get('job_id'):
                raise RuntimeError('Unknown scheduler job cannot be confirmed stopped; inspect scheduler before clearing this allocation fence')
            if data.get('session_id') and data['session_id'] != state.get('session_id'):
                raise RuntimeError('Refusing to stop a different allocation session')
            if state.get("job_id"):
                subprocess.run(scheduler('scancel', state),check=True)
            elif state.get("allocator_pid"):
                try: os.killpg(state["allocator_pid"],signal.SIGTERM)
                except ProcessLookupError: pass
            state.pop("token",None)
            state.pop('controller_token', None)
            state["status"]="stopped"
            write(root / "session.json",state)
        elif action in {'attach', 'start'} and state["status"] in {"absent","failed","stopped"}:
            if action == 'attach':
                state={"status":"absent","error":"No ready session; attach_only prevents allocation."}
            else:
                if state.get("job_id"):
                    subprocess.run(scheduler('scancel', state),capture_output=True)
                    # SLURM can leave a cancelled step alive while it terminates
                    # Blender. Do not rotate its token or start another database
                    # writer until the old allocation has actually disappeared.
                    deadline = time.monotonic() + 12
                    while True:
                        pending = subprocess.run(scheduler('squeue', state, '-h', '-o', '%T', '-j'), capture_output=True, text=True, timeout=5)
                        if not pending.returncode and not pending.stdout.strip():
                            break
                        if time.monotonic() >= deadline:
                            state.pop("token", None)
                            state.pop('controller_token', None)
                            state["status"] = "stopping"
                            print(json.dumps(state))
                            return
                        time.sleep(1)
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
                from blender_render.hpc import validate_profile
                validate_profile(profile)
                write(root / "profile.json",profile)
                token=root / "token"
                token.write_text(secrets.token_urlsafe(32)); token.chmod(0o600)
                service_token = root / 'controller-token'
                service_token.write_text(secrets.token_urlsafe(32)); service_token.chmod(0o600)
                session_id = secrets.token_hex(12)
                from blender_render.engine import protect
                seal = protect('startup')
                captured = seal.capture(root/'engines'/session_id/'blender_render')
                script=captured/'hpc_remote.py'
                shell=root / "worker.sh"
                shell.write_text("#!/bin/sh\nexec " + shlex.join([profile["remote_python"],str(script),"worker",str(root),session_id]) + "\n")
                shell.chmod(0o700)
                allocation=profile['launcher']['arguments']
                command=["script","-q","-c",shlex.join(["env","SHELL=" + str(shell),"qlogin",*allocation]),"/dev/null"]
                env=os.environ.copy(); env["SHELL"]="/bin/sh"
                # Create registry before the allocator can publish its node.
                state={"status":"submission_unknown","session_id":session_id,"started_at":time.time(),"allocator_pid":0}
                write(root / "session.json",state)
                if profile['launcher']['kind'] == 'slurm_sbatch':
                    protect('startup')
                    response = subprocess.run(['sbatch', '--parsable', *allocation, str(shell)], capture_output=True, text=True, timeout=20)
                    if response.returncode:
                        raise RuntimeError('sbatch submission failed; inspect scheduler before retrying')
                    state['job_id'], state['cluster'] = parse_sbatch(response.stdout)
                else:
                    protect('startup')
                    with (root / "allocation.log").open("a") as log:
                        process=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                    state['allocator_pid'] = process.pid
                state['status'] = 'allocating'
                write(root / "session.json",state)
        print(json.dumps(state))


if __name__ == "__main__":
    result = main()
    if result is not None:
        raise SystemExit(result)
