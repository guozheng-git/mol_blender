"""Local service lifecycle and verified runtime discovery."""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .config import VERSION, config_root, load_config, save_config


@contextmanager
def file_lock(path, *, blocking=True):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_private(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as stream:
        os.chmod(temp, 0o600)
        stream.write(data)
    os.replace(temp, path)


def ensure_local(*, blender="", image="", attach_only=False, **ignored):
    root = config_root() / "local"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_file = root / "session.json"
    from .client import Client
    with file_lock(root / "startup.lock"):
        try:
            state = json.loads(state_file.read_text())
            token = Path(state["token_file"]).read_text().strip()
            health = Client(state["url"], token, 2).request("GET", "/v1/quota")
            if state.get("version") != VERSION:
                raise RuntimeError("A previous service version is running. Run blender-render service stop before upgrading.")
            if image and state.get("image", "") != image:
                raise RuntimeError("Local service uses a different runtime. Stop it before switching Apptainer images.")
            return state
        except RuntimeError as exc:
            from .client import ClientError
            if not isinstance(exc, ClientError):
                raise
        except (OSError, ValueError, KeyError):
            pass
        if attach_only:
            raise RuntimeError("No ready local session was found.")
        config = load_config()
        python = config.get("service_python", sys.executable)
        check = subprocess.run([python, "-c", "import fastapi, uvicorn; import blender_render"], capture_output=True)
        if check.returncode:
            raise RuntimeError("Install the service extra and run setup: python3.11 -m pip install 'pymol-plugin-bridge[server]'; blender-render setup")
        token_file = root / "token"
        if not token_file.exists():
            write_private(token_file, secrets.token_urlsafe(32))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        state = {"url": f"http://127.0.0.1:{port}", "token_file": str(token_file), "version": VERSION,
                 "session_id": secrets.token_hex(12), "started_at": time.time(), "image": image}
        command = [python, "-m", "blender_render.cli", "serve", "--port", str(port), "--root", str(root), "--token-file", str(token_file), "--idle-timeout", "900"]
        executable = blender or config.get("blender", "")
        if executable:
            command += ["--blender", executable]
        if image:
            command += ["--image", image]
        env = os.environ.copy()
        env["BLENDER_RENDER_SESSION_ID"] = state["session_id"]
        with (root / "service.log").open("a") as log:
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        state["pid"] = process.pid
        write_private(state_file, json.dumps(state))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Local service exited. See {root / 'service.log'}")
            try:
                Client(state["url"], token_file.read_text().strip(), 1).request("GET", "/v1/quota")
                return state
            except Exception:
                time.sleep(.2)
        process.terminate()
        raise RuntimeError(f"Local service startup timed out. See {root / 'service.log'}")


def export_file(path, *, python="", selection="(all)"):
    import tempfile
    config = load_config()
    executable = python or config.get("pymol_python", sys.executable)
    with tempfile.TemporaryDirectory(prefix="blender-export-") as temp:
        destination = Path(temp) / "payload.json"
        result = subprocess.run([executable, str(Path(__file__).with_name("export.py")), str(Path(path).expanduser().resolve()), str(destination), "--selection", selection], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError("PyMOL export failed. Configure --pymol-python with an interpreter containing pymol2.\n" + result.stderr[-3000:])
        return json.loads(destination.read_text())


def diagnose(blender=""):
    from .core import _detect_host_blender_executable
    blender = blender or load_config().get("blender") or _detect_host_blender_executable()
    env = os.environ.copy()
    site = load_config().get("runtime_site", str(config_root() / "runtime" / "site"))
    env["PYTHONPATH"] = site + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([blender, "--background", "--python-use-system-env", "--python-exit-code", "1", "--python", str(Path(__file__).with_name("runtime_probe.py"))], env=env, capture_output=True, text=True, timeout=90)
    prefix = "BLENDER_RENDER_RUNTIME="
    lines = [line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]
    if result.returncode or not lines:
        raise RuntimeError("Runtime is incomplete. Run blender-render setup --install-runtime.\n" + result.stderr[-1500:])
    versions = json.loads(lines[-1])
    from .config import runtime_versions
    lock = runtime_versions()
    drift = {key: value for key, value in versions.items() if value != lock[key]}
    return {"blender_executable": blender, "versions": versions, "matches_lock": not drift, "mismatches": drift}
