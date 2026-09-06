"""Small stdlib-only helper on the cluster login node and allocated GPU node."""
from __future__ import annotations
import fcntl
import getpass
import json
import os
import secrets
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen


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
        result = subprocess.run(["squeue", "-h", "-j", str(state["job_id"]), "-o", "%T"], capture_output=True, text=True, timeout=10)
        if result.returncode or result.stdout.strip() not in {"RUNNING", "PENDING", "CONFIGURING"}:
            state.update(status="failed", error="GPU allocation ended; reconnect to restart it, or use mode='local'.")
    else:
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
        response = subprocess.run(command, capture_output=True, text=True, timeout=8)
        if response.returncode == 0:
            health = json.loads(response.stdout)
            if health.get("status") == "ok":
                state.update(status="ready", queue_depth=health.get("queue_depth",0), last_heartbeat=time.time())
        elif time.time() - state.get("worker_started",time.time()) > 60:
            state.update(status="failed", error="GPU service health check failed; reconnect to allocate a new worker.")
    if state["status"] == "ready":
        state["token"] = (root / "token").read_text()
    return state


def main():
    action = sys.argv[1]
    if action == "worker":
        root = Path(sys.argv[2])
        profile = json.loads((root / "profile.json").read_text())
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",0)); port = probe.getsockname()[1]
        state = json.loads((root / "session.json").read_text())
        state.update(status="starting", node=os.environ.get("SLURMD_NODENAME") or os.environ.get("SLURM_NODELIST") or socket.gethostname().split(".")[0], user=getpass.getuser(), port=port,
                     job_id=os.environ["SLURM_JOB_ID"], worker_started=time.time())
        write(root / "session.json",state)
        os.environ["BLENDER_RENDER_SESSION_ID"] = state["session_id"]
        os.chdir(profile["remote_root"])
        os.environ["PYTHONPATH"] = profile["remote_root"] + os.pathsep + os.environ.get("PYTHONPATH","")
        command = ["apptainer", "exec", "--nv", "--cleanenv", "--bind", profile["remote_root"] + ":" + profile["remote_root"],
                   "--env", "PYTHONPATH=" + profile["remote_root"] + ":/opt/runtime/site:/opt",
                   "--env", "BLENDER_RENDER_SITE=/opt/runtime/site",
                   "--env", "BLENDER_RENDER_SESSION_ID=" + state["session_id"],
                   profile["image"], "/usr/local/bin/python", "-m", "blender_render.cli", "serve",
                   "--root", str(root / "service"), "--token-file", str(root / "token"), "--port", str(port),
                   "--blender", profile.get("blender", "blender"), "--idle-timeout", "900"]
        os.execvp(command[0], command)
    data = json.load(sys.stdin)
    profile = data["profile"]
    root = Path(profile["registry"]).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "registry.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state = check_session(root)
        if action == "stop":
            if state.get("job_id"):
                subprocess.run(["scancel",str(state["job_id"])],check=True)
            elif state.get("allocator_pid"):
                try: os.killpg(state["allocator_pid"],signal.SIGTERM)
                except ProcessLookupError: pass
            state.pop("token",None)
            state["status"]="stopped"
            write(root / "session.json",state)
        elif action == "attach" and state["status"] in {"absent","failed","stopped"}:
            if data.get("attach_only"):
                state={"status":"absent","error":"No ready session; attach_only prevents allocation."}
            else:
                if state.get("job_id"):
                    subprocess.run(["scancel",str(state["job_id"])],capture_output=True)
                    # SLURM can leave a cancelled step alive while it terminates
                    # Blender. Do not rotate its token or start another database
                    # writer until the old allocation has actually disappeared.
                    deadline = time.monotonic() + 12
                    while True:
                        pending = subprocess.run(["squeue","-h","-j",str(state["job_id"]),"-o","%T"], capture_output=True, text=True, timeout=5)
                        if pending.returncode or not pending.stdout.strip():
                            break
                        if time.monotonic() >= deadline:
                            state.pop("token", None)
                            state["status"] = "stopping"
                            print(json.dumps(state))
                            return
                        time.sleep(1)
                write(root / "profile.json",profile)
                token=root / "token"
                token.write_text(secrets.token_urlsafe(32)); token.chmod(0o600)
                script=Path(profile["remote_root"]) / "blender_render/hpc_remote.py"
                shell=root / "worker.sh"
                shell.write_text("#!/bin/sh\nexec " + shlex.join([profile["remote_python"],str(script),"worker",str(root)]) + "\n")
                shell.chmod(0o700)
                allocation=profile.get("allocation",["-p","gpu","--gres=gpu:small:1","--constraint=A4000","--mem=64G","-c","4","--time=00:30:00"])
                command=["script","-q","-c",shlex.join(["env","SHELL=" + str(shell),"qlogin",*allocation]),"/dev/null"]
                env=os.environ.copy(); env["SHELL"]="/bin/sh"
                # Create registry before the allocator can publish its node.
                state={"status":"allocating","session_id":secrets.token_hex(12),"started_at":time.time(),"allocator_pid":0}
                write(root / "session.json",state)
                with (root / "allocation.log").open("a") as log:
                    process=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                state=json.loads((root / "session.json").read_text())
                state["allocator_pid"]=process.pid
                write(root / "session.json",state)
        print(json.dumps(state))


if __name__ == "__main__":
    main()
