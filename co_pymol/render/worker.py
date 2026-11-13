"""Detached helper run in Blender Render's Python, never in PyMOL's Python.

PyMOL saves each request to its own folder and returns at once. This helper
renders it with this checkout's renderer, locally (`blender_render.local`) or on
the configured lab cluster (`blender_render.cluster.client`), and records progress
in state.json. It never resubmits an ambiguous cluster submission.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__ in (None, ""):
    from records import write_json
else:
    from .records import write_json

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _device(manifest_path: Path) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        return {}
    device = manifest.get("cycles_device") or manifest.get("render", {}).get(
        "cycles_device", {}
    )
    return device if isinstance(device, dict) else {}


def _load_payload(directory: Path, request: dict) -> dict:
    from blender_render.core import validate_payload_document

    raw = Path(request.get("scene_path", directory / "scene.bundle.json")).read_bytes()
    if hashlib.sha256(raw).hexdigest() != request["scene_sha256"]:
        raise ValueError("Captured scene changed before rendering")
    return validate_payload_document(json.loads(raw))


def _render_local(directory: Path, request: dict, update) -> dict:
    from blender_render.local import render_payload

    payload = _load_payload(directory, request)
    update(status="rendering", worker_pid=os.getpid())
    result = render_payload(
        payload,
        output=directory / "render.png",
        options=request["options"],
        timeout=request["config"]["wait_seconds"],
        cancelled=(directory / "cancel.request").exists,
    )
    return result


def _render_cluster(directory: Path, request: dict, state: dict, update, recover):
    from urllib.error import URLError

    from blender_render.cluster.client import Client, RemoteError
    from blender_render.cluster.protocol import stop_waiting

    cfg = request["config"]
    cancelled = directory / "cancel.request"
    if not state.get("job_id"):
        if cancelled.exists() and state["status"] == "starting":
            update(status="cancelled")
            return None
        if recover or state["status"] != "starting":
            raise RuntimeError(
                "Submission outcome is unknown. Check `blender-render cluster status "
                f"{directory / 'render.cluster.json'}`; this helper will not resubmit."
            )
    client = Client(cfg["cluster_profile"])
    if not state.get("job_id"):
        payload = _load_payload(directory, request)
        update(status="submitting")
        job = client.submit(payload, request["options"], directory / "render.png")
        update(job_id=job["job_id"], status=job.get("status", "queued"))
    job_id = state["job_id"]
    deadline = time.monotonic() + cfg["wait_seconds"]
    delay = 2
    cancel_sent = False
    while time.monotonic() < deadline:
        try:
            if cancelled.exists() and not cancel_sent:
                client.api("POST", f"/v1/jobs/{job_id}/cancel")
                cancel_sent = True
                update(status="cancelling")
            observed = client.status(job_id)
        except (RemoteError, URLError, ConnectionError, TimeoutError, OSError) as exc:
            # Observation only: reconnecting never submits another job.
            update(status="recovering", error=f"{type(exc).__name__}: {exc}")
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
            delay = min(delay * 2, 30)
            continue
        delay = 2
        # "completed" locally means downloaded and verified, never just remote.
        update(
            status="cancelling"
            if cancel_sent
            else "downloading"
            if observed["status"] == "completed"
            else observed["status"],
            remote={k: observed[k] for k in ("status", "stage") if k in observed},
            error=None,
        )
        if observed["status"] == "completed":
            update(status="downloading")
            return client.fetch(job_id, directory / "render.png")
        if stop_waiting(observed["status"]):
            update(
                status="cancelled" if observed["status"] == "cancelled" else "failed",
                error=observed.get("error") or observed["status"],
            )
            return None
        time.sleep(min(3, max(0, deadline - time.monotonic())))
    raise TimeoutError(
        "Stopped waiting; the cluster job may still be running. "
        "Call blender_result to resume retrieval or blender_cancel."
    )


def run(directory: Path, *, recover: bool = False) -> None:
    from blender_render.errors import RenderCancelled

    with (directory / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        request = json.loads((directory / "request.json").read_text())
        cfg = request["config"]
        state_path = directory / "state.json"
        state = json.loads(state_path.read_text())

        def update(**fields):
            state.update(fields, updated_at=time.time())
            write_json(state_path, state)

        if state["status"] in {"completed", "cancelled", "failed"}:
            return
        try:
            if cfg.get("backend", "local") == "cluster":
                result = _render_cluster(directory, request, state, update, recover)
            else:
                if (directory / "cancel.request").exists():
                    update(status="cancelled")
                    return
                if recover and state["status"] != "starting":
                    raise RuntimeError(
                        "The local render was interrupted; submit a new render."
                    )
                result = _render_local(directory, request, update)
            if result is None:
                return
            write_json(directory / "result.json", result)
            outputs = result["outputs"]
            png_path = Path(outputs["render.png"])
            png = png_path.read_bytes()
            if not png.startswith(PNG_MAGIC):
                raise RuntimeError("Renderer returned an invalid PNG")
            manifest = outputs.get("manifest.json") or str(
                Path(result.get("artifacts", "")) / "manifest.json"
            )
            gpu = _device(Path(manifest))
            if cfg.get("require_gpu") and gpu.get("device") != "GPU":
                raise RuntimeError("Manifest does not confirm GPU rendering")
            update(
                status="completed",
                outputs=outputs,
                png_path=str(png_path),
                gpu=gpu,
                png_sha256=hashlib.sha256(png).hexdigest(),
                error=None,
            )
        except RenderCancelled:
            update(status="cancelled", error=None)
        except Exception as exc:
            update(status="needs_attention", error=f"{type(exc).__name__}: {exc}")


def run_batch(directory: Path) -> None:
    """Run a bounded set of helpers against one saved scene, outside PyMOL."""
    with (directory / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        batch = json.loads((directory / "request.json").read_text())
        if not batch.get("prepared"):
            return
        write_json(directory / "state.json", {"status": "running"})
        local = batch["config"].get("backend", "local") == "local"

        def resume(child):
            path = Path(child["directory"])
            state = json.loads((path / "state.json").read_text())
            # A starting request has never been submitted. Any ambiguous
            # submission keeps its existing fence in run().
            run(path, recover=state["status"] != "starting")

        # One local GPU renders one scene at a time; the cluster queues in parallel.
        with ThreadPoolExecutor(max_workers=1 if local else 4) as pool:
            list(pool.map(resume, batch["children"]))


def main() -> None:
    action, path = sys.argv[1:]
    if action in {"presets", "gallery"}:
        if __package__ in (None, ""):
            from gallery import preset_gallery
        else:
            from .gallery import preset_gallery

        from blender_render.config import config_root
        from blender_render.presets import catalog

        entries = catalog()
        result = (
            preset_gallery(Path(path), config_root() / "co-pymol-previews", entries)
            if action == "gallery"
            else {
                "presets": [
                    {
                        "id": item["id"],
                        "description": item["description"],
                        "aliases": item.get("aliases", []),
                    }
                    for item in entries
                ]
            }
        )
        print(json.dumps(result))
    elif action in {"run", "recover"}:
        run(Path(path), recover=action == "recover")
    elif action == "batch":
        run_batch(Path(path))
    else:
        raise ValueError("Unknown adapter action")


if __name__ == "__main__":
    main()
