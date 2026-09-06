"""Mac/desktop cluster adapter. All GPU service traffic uses an SSH tunnel."""
from __future__ import annotations
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import config_root
from .runtime import file_lock, write_private


def validate_profile(profile):
    for key in ("host", "remote_python", "remote_root", "image", "registry"):
        if not isinstance(profile.get(key), str) or not profile[key].strip() or "\n" in profile[key] or "\x00" in profile[key]:
            raise ValueError(f"HPC profile requires {key}")
    if profile["host"].startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.@:-]+", profile["host"]):
        raise ValueError("Invalid SSH host")
    if not isinstance(profile.get("allocation", []), list) or any(not isinstance(x, str) or "\x00" in x for x in profile.get("allocation", [])):
        raise ValueError("allocation must be a list of SLURM arguments")
    return profile


def remote(profile, action, **options):
    validate_profile(profile)
    script = str(Path(profile["remote_root"]) / "blender_render/hpc_remote.py")
    command = shlex.join([profile["remote_python"], script, action])
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", profile["host"], command],
        input=json.dumps({"profile": profile, **options}), capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Cluster session failed: " + result.stderr[-2000:])
    return json.loads(result.stdout)


def connect(name, profile, *, attach_only=False, timeout=600, **ignored):
    validate_profile(profile)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Invalid profile name")
    root = config_root() / "hpc" / name
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    from .client import Client
    with file_lock(root / "attach.lock"):
        try:
            previous = json.loads((root / "connection.json").read_text())
            current = remote(profile, "status")
            if current.get("status") != "ready" or current.get("session_id") != previous.get("session_id"):
                raise RuntimeError("Cached allocation ended")
            token = Path(previous["token_file"]).read_text().strip()
            Client(previous["url"], token, timeout=2).request("GET", "/v1/quota")
            return previous
        except (OSError, ValueError, RuntimeError):
            pass
        start = remote(profile, "attach", attach_only=bool(attach_only))
        deadline = time.monotonic() + timeout
        recoveries = 0
        while start["status"] != "ready":
            if start["status"] in {"failed", "absent"}:
                if attach_only or recoveries >= 1:
                    raise RuntimeError(start.get("error", "No ready GPU session. Use mode='local' or check the cluster profile."))
                recoveries += 1
                start = remote(profile, "attach")
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("GPU allocation is still pending. Reconnect to the same profile; use mode='local' to render now.")
            print("Waiting for interactive GPU allocation...", file=sys.stderr)
            time.sleep(3)
            start = remote(profile, "attach") if start["status"] == "stopping" else remote(profile, "status")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            local_port = probe.getsockname()[1]
        # A login-host forward to node:port cannot reach the node's loopback.
        # Terminate SSH on the GPU node itself, via ProxyJump on the login host.
        node = start["node"]
        user = start["user"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", node) or not re.fullmatch(r"[A-Za-z0-9_.-]+", user):
            raise RuntimeError("Invalid node identity in cluster registry")
        command = ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3", "-J", profile["host"], "-L", f"127.0.0.1:{local_port}:127.0.0.1:{int(start['port'])}", f"{user}@{node}"]
        if profile.get("transport") == "slurm":
            remote_command = ["srun", "--jobid=" + str(start["job_id"]), "--overlap", "-N1", "-n1", profile["remote_python"],
                str(Path(profile["remote_root"]) / "blender_render/hpc_proxy.py"), "stdio", "--port", str(start["port"])]
            ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=20", profile["host"], shlex.join(remote_command)]
            write_private(root / "proxy-command.json", json.dumps(ssh))
            command = [sys.executable, str(Path(__file__).with_name("hpc_proxy.py")), "listen", "--port", str(local_port), "--command-file", str(root / "proxy-command.json")]
        with (root / "tunnel.log").open("a") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        token_path = root / "token"
        write_private(token_path, start.pop("token"))
        connection = {**start, "url":f"http://127.0.0.1:{local_port}", "token_file":str(token_path), "tunnel_pid":process.pid}
        for _ in range(50):
            try:
                Client(connection["url"], token_path.read_text(), 1).request("GET", "/v1/quota")
                write_private(root / "connection.json", json.dumps(connection))
                print("Interactive GPU session ready", file=sys.stderr)
                return connection
            except Exception:
                if process.poll() is not None:
                    break
                time.sleep(.2)
        process.terminate()
        raise RuntimeError(f"SSH tunnel could not reach the GPU service; inspect {root / 'tunnel.log'}. Use mode='local' as fallback.")


def stop(name, profile):
    return remote(profile, "stop")
