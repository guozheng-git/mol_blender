"""Detached helper run in mol-blender's Python, never in PyMOL's Python.

PyMOL saves each request to its own folder and returns at once. This helper
renders it with this checkout's renderer through the shared job API
(`mol_blender.jobs`), locally or on the configured lab cluster, and mirrors
progress into state.json for chat. A lab run is identified by its receipt
(render.cluster.json); an unconfirmed upload is looked up, and resent only under
its original request ID, so it can never become a second job.
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
    from records import RECEIPT, write_json
else:
    from .records import RECEIPT, write_json

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
    from mol_blender.core import validate_payload_document

    raw = Path(request.get("scene_path", directory / "scene.bundle.json")).read_bytes()
    if hashlib.sha256(raw).hexdigest() != request["scene_sha256"]:
        raise ValueError("Captured scene changed before rendering")
    return validate_payload_document(json.loads(raw))


def _render_local(directory: Path, request: dict, update) -> dict:
    from mol_blender import jobs

    payload = _load_payload(directory, request)
    update(status="rendering", worker_pid=os.getpid())
    return jobs.submit(
        payload,
        request["options"],
        directory / "render.png",
        timeout=request["config"]["wait_seconds"],
        cancelled=(directory / "cancel.request").exists,
    )


def _render_cluster(directory: Path, request: dict, state: dict, update):
    """Submit once, then follow the run by its receipt through the shared job API.

    The receipt (render.cluster.json) is written before upload, so a restarted
    helper always finds the same request: an unconfirmed upload is looked up by its
    request ID, and sent again under that same ID only if the gateway never got it.
    """
    from mol_blender import jobs

    cfg = request["config"]
    cancelled = directory / "cancel.request"
    output = directory / "render.png"
    receipt = directory / RECEIPT
    # A detached helper keeps the tunnel open: siblings and later status checks share it.
    # Recovering an existing receipt must not fail just because SSH does; polling backs off.
    with jobs.lab_connection(cfg["cluster_profile"], keep=True, required=not receipt.exists()):
        if not state.get("job_id"):
            if cancelled.exists():
                observed = jobs.observe(receipt) if receipt.exists() else {}
                if not observed.get("job_id"):
                    update(status="cancelled", error=None)  # Nothing reached the gateway: nothing to cancel.
                    return None
            else:
                update(status="submitting")
                observed = jobs.submit(_load_payload(directory, request), request["options"], output,
                                       backend_name=jobs.LAB, profile=cfg["cluster_profile"])
            update(job_id=observed["job_id"], status=observed.get("status", "queued"), error=None)

        def progress(observed):
            # "completed" locally means downloaded and verified, never just remote.
            status = "cancelling" if observed["cancel_requested"] else observed["status"]
            update(status=status, remote={k: observed[k] for k in ("status", "stage") if k in observed},
                   error=observed.get("error"))

        final = jobs.wait(receipt, timeout=cfg["wait_seconds"], on_update=progress, cancelled=cancelled.exists)
        status = final["status"]
        if final.get("waiting_stopped"):
            raise TimeoutError("Stopped waiting; the cluster job may still be running. "
                               "Call blender_result to resume retrieval or blender_cancel.")
        if status == "completed":
            update(status="downloading", error=None)
            return jobs.fetch(receipt)
        if status == "submission_unknown":
            # The gateway may still reconcile it into a real job: keep it recoverable.
            raise RuntimeError("The gateway could not confirm the Slurm submission; it is kept for "
                               "reconciliation. Do not resubmit; ask blender_result again later.")
        update(status="cancelled" if status == "cancelled" else "failed",
               error=final.get("error") or status)
        return None


def run(directory: Path, *, recover: bool = False) -> None:
    from mol_blender.errors import RenderCancelled

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
                result = _render_cluster(directory, request, state, update)
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

        from mol_blender.config import config_root
        from mol_blender.presets import catalog

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
