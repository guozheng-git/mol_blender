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


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    action = sys.argv[1]
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
        command = ["apptainer", "exec", "--nv", "--cleanenv", "--bind", engine_root + ":" + engine_root,
                   "--bind", str(root) + ':' + str(root),
                   "--env", "PYTHONPATH=" + engine_root + ":/opt/runtime/site:/opt",
                   "--env", "BLENDER_RENDER_SITE=/opt/runtime/site",
                   "--env", "BLENDER_RENDER_SESSION_ID=" + state["session_id"],
                   profile["image"], "/usr/local/bin/python", "-m", "blender_render.cli", "serve",
                   "--root", str(root / "service"), "--token-file", str(root / "token"), "--port", str(port),
                   '--controller-token-file', str(root / 'controller-token'),
                   "--blender", profile.get("blender", "blender"), "--idle-timeout", "900"]
        os.execvp(command[0], command)
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
    main()
