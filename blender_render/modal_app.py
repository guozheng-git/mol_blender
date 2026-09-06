"""Modal deployment: one durable controller, one L4, isolated file conversion.

Deploy with the CLI so the previous controller is drained and stopped first.
Worker functions never receive the controller's OAuth or signing secrets.
"""
from __future__ import annotations
import json
import os
import threading
import time
from pathlib import Path

import modal

PUBLIC = os.environ.get("BLENDER_RENDER_PUBLIC") == "1"
APP_NAME = os.environ.get("BLENDER_RENDER_APP_NAME") or ("blender-render-beta" if PUBLIC else "blender-render-beta-dev")
PERSONAL = APP_NAME not in {"blender-render-beta", "blender-render-beta-dev"}
JOB_TIMEOUT = int(os.environ.get("BLENDER_RENDER_JOB_TIMEOUT", "600"))
app = modal.App(APP_NAME)
package = Path(__file__).resolve().parent
shared_env = {"PYTHONPATH":"/opt:/opt/runtime/site", "BLENDER_RENDER_SITE":"/opt/runtime/site", "BLENDER_RENDER_PUBLIC":"1" if PUBLIC else "0", "PYTHONUNBUFFERED":"1"}
shared_env.update(BLENDER_RENDER_APP_NAME=APP_NAME, BLENDER_RENDER_JOB_TIMEOUT=str(JOB_TIMEOUT))

controller_image = (modal.Image.debian_slim(python_version="3.11")
    .add_local_file(package / "service-requirements.txt", "/tmp/service-requirements.txt", copy=True)
    .run_commands("python -m pip install --require-hashes --only-binary=:all: -r /tmp/service-requirements.txt")
    .env(shared_env).add_local_dir(package, "/opt/blender_render", copy=True, ignore=["__pycache__", "*.pyc"]))

renderer_image = (modal.Image.debian_slim(python_version="3.11")
    .apt_install("curl", "xz-utils", "libgl1", "libegl1", "libx11-6", "libxi6", "libxrender1", "libxfixes3", "libxxf86vm1", "libxkbcommon0", "libsm6", "libice6", "libglib2.0-0", "libfontconfig1", "libxrandr2", "libxinerama1", "libxcursor1", "libwayland-client0", "libpulse0")
    .env(shared_env))
for filename in ("__init__.py", "config.py", "runtime.lock.json", "runtime-requirements.txt", "runtime_install.py", "runtime_initialize.py"):
    renderer_image = renderer_image.add_local_file(package / filename, "/opt/blender_render/" + filename, copy=True)
renderer_image = (renderer_image.run_commands("python -c 'from blender_render.runtime_install import install; install(root=\"/opt/runtime\")'")
    .add_local_dir(package, "/opt/blender_render", copy=True, ignore=["__pycache__", "*.pyc"]))

converter_image = (modal.Image.debian_slim(python_version="3.11")
    .apt_install("python3-pymol", "libgl1")
    .env({"QT_QPA_PLATFORM":"offscreen"})
    .add_local_file(package / "export.py", "/opt/export.py", copy=True))

volume = modal.Volume.from_name(APP_NAME + "-state", create_if_missing=True)
controller_secrets = modal.Secret.from_name(APP_NAME + "-credentials")
_gpu_backend = None


@app.function(image=renderer_image, gpu="L4", cpu=(4,4), memory=8192, timeout=JOB_TIMEOUT,
              retries=0, min_containers=0, max_containers=1, scaledown_window=2)
def render_gpu(request: dict, identifier: str) -> dict:
    global _gpu_backend
    from blender_render.service import HostBackend
    if _gpu_backend is None:
        _gpu_backend = HostBackend(Path("/tmp/blender-worker"), blender="/opt/runtime/blender-5.0.1-linux-x64/blender", timeout=JOB_TIMEOUT-20)
    request["job_timeout_seconds"] = JOB_TIMEOUT-20
    return _gpu_backend.render(request, identifier, lambda: False)


def convert_file(data: bytes, suffix: str) -> dict:
    if suffix not in {".pse", ".pdb", ".cif", ".mmcif"} or len(data) > 50 * 1024**2:
        raise ValueError("Invalid molecular upload")
    sandbox = modal.Sandbox.create("sleep", "90", app=app, image=converter_image,
        timeout=90, cpu=.5, memory=2048, secrets=[], volumes={}, block_network=True,
        include_oidc_identity_token=False)
    try:
        sandbox.filesystem.write_bytes(data, "/tmp/input" + suffix)
        process = sandbox.exec("/usr/bin/python3", "/opt/export.py", "/tmp/input" + suffix, "/tmp/payload.json")
        process.wait()
        if process.returncode:
            raise ValueError("The molecular file could not be converted")
        # Bound the read inside the sandbox, including a malicious session that
        # leaves a process writing the file after its converter exits.
        reader = sandbox.exec("/usr/bin/head", "-c", str(50 * 1024**2 + 1), "/tmp/payload.json")
        payload = reader.stdout.read()
        reader.wait()
        if reader.returncode or len(payload.encode()) > 50 * 1024**2:
            raise ValueError("Converted structure is too large")
        return json.loads(payload)
    finally:
        sandbox.terminate()


class ModalBackend:
    paid = True

    def __init__(self, store):
        self.store, self.active = store, None

    def render(self, request, identifier, cancelled):
        # The dispatcher already committed the reservation and running state.
        call = render_gpu.spawn(request, identifier)
        self.active = call
        try:
            self.store.set_remote_id(identifier, call.object_id)
            while True:
                if cancelled():
                    call.cancel(terminate_containers=True)
                    raise RuntimeError("Render cancelled")
                try:
                    return call.get(timeout=1)
                except TimeoutError:
                    continue
        except BaseException:
            call.cancel(terminate_containers=True)
            raise
        finally:
            self.active = None

    def stop(self):
        if self.active:
            self.active.cancel(terminate_containers=True)


@app.function(image=controller_image, cpu=(.25,.5), memory=1024, timeout=3600,
              min_containers=1, max_containers=1,
              volumes={"/state":volume}, secrets=[controller_secrets])
@modal.concurrent(max_inputs=64)
@modal.asgi_app(label=APP_NAME)
def gateway():
    # A failed database/auth initialization must not leave a paid minimum
    # container restarting indefinitely before the budget monitor exists.
    try:
        return create_gateway()
    except Exception:
        stop_current_app()
        raise


def stop_current_app():
    import subprocess, sys
    if not app.app_id:
        raise RuntimeError("Cannot determine the current Modal app to stop")
    # The CLI uses Modal's current container identity, never worker secrets.
    for attempt in range(3):
        try:
            subprocess.run([sys.executable, "-m", "modal", "app", "stop", app.app_id, "--yes"], check=True, timeout=30)
            return
        except (subprocess.SubprocessError, OSError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def create_gateway():
    from dataclasses import replace
    from blender_render.api import create_app, Downloads
    from blender_render.auth import github_provider
    from blender_render.jobs import Policy, JobStore
    from blender_render.mcp_server import create_http
    from blender_render.service import RenderService
    from blender_render.budget import OperatingBudget
    # Development reserves $10 of its $50 allowance for builds/control/storage.
    policy = Policy(public=True, budget_usd=450 if PUBLIC else 40)
    if PERSONAL:
        policy = replace(policy, budget_usd=float(os.environ['BLENDER_RENDER_COMPUTE_BUDGET']),
            expires_at=float(os.environ['BLENDER_RENDER_CREDITS_EXPIRE']), max_job_seconds=JOB_TIMEOUT,
            daily_jobs=100, daily_gpu_seconds=24*3600)
    elif not PUBLIC:
        policy = replace(policy, daily_jobs=100, daily_gpu_seconds=60000)
    store = JobStore(Path("/state"), policy, sync=volume.commit)
    # $2 remains reserved for the final 24 hours of retention/cleanup. Public
    # operations total at most $30; development control/storage at most $5.
    operations = OperatingBudget(store, cap_usd=float(os.environ['BLENDER_RENDER_OPERATIONS_BUDGET'])-2 if PERSONAL else (28 if PUBLIC else 3))
    try:
        funded = operations.reserve_hour()
    except Exception:
        funded = False
    token = os.environ["BLENDER_RENDER_ADMIN_TOKEN"]
    url = os.environ["BLENDER_RENDER_PUBLIC_URL"]
    if PUBLIC:
        provider = github_provider(store, client_id=os.environ["GITHUB_CLIENT_ID"], client_secret=os.environ["GITHUB_CLIENT_SECRET"],
            base_url=url, encryption_key=os.environ["BLENDER_RENDER_ENCRYPTION_KEY"], jwt_key=os.environ["BLENDER_RENDER_JWT_KEY"],
            allowed_users=set(os.environ['BLENDER_RENDER_ALLOWED_USERS'].split(',')) if os.environ.get('BLENDER_RENDER_ALLOWED_USERS') else None)
    else:
        from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
        provider = StaticTokenVerifier(tokens={token:{"client_id":"development", "scopes":[], "sub":"development"}})
    downloads = Downloads(os.environ["BLENDER_RENDER_DOWNLOAD_KEY"], url)
    service = RenderService(store, ModalBackend(store))
    mcp = create_http(service, auth=provider, downloads=downloads, converter=convert_file)
    application = create_app(service, token=token, public_url=url, signing_key=os.environ["BLENDER_RENDER_DOWNLOAD_KEY"], auth_provider=provider, mcp_server=mcp, converter=convert_file)
    if funded:
        operations.monitor(service, stop_current_app)
    else:
        store.drain()
        service.stopping.set()
        threading.Thread(target=operations.stop_unfunded, args=(service, stop_current_app), daemon=True).start()
    return application
