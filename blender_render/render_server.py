#!/usr/bin/env python3
"""HTTP render server with FIFO queue and persistent Blender worker."""

from __future__ import annotations

import argparse
import base64
import json
import queue
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import bridge_runner as _bridge_runner
except Exception:  # pragma: no cover - package import fallback
    from blender_render import bridge_runner as _bridge_runner

try:
    from render_worker_manager import PersistentBlenderWorker, WorkerError
except Exception:  # pragma: no cover - package import fallback
    from blender_render.render_worker_manager import PersistentBlenderWorker, WorkerError


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _ensure_png_suffix(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        return "render.png"
    if value.lower().endswith(".png"):
        return value
    return f"{value}.png"


def _suggest_output_name(*, request_doc: dict[str, Any], preset: str, style_doc: dict[str, Any]) -> str:
    requested = str(request_doc.get("output_name", "")).strip()
    if requested:
        return _ensure_png_suffix(requested)

    if preset:
        preset_name = _bridge_runner.PRESET_OUTPUT_FILENAMES.get(preset)
        if preset_name:
            return _ensure_png_suffix(preset_name)

    output = style_doc.get("output") if isinstance(style_doc, dict) else None
    if isinstance(output, dict):
        configured = str(output.get("filename", "")).strip()
        if configured:
            return _ensure_png_suffix(configured)
    return "custom_render.png"


def _prepare_style_doc(request_doc: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    preset = str(request_doc.get("preset", "")).strip()
    style_doc_input = request_doc.get("style_doc")
    style_json_path_raw = str(request_doc.get("style_json", "")).strip()

    style_doc: dict[str, Any]
    if isinstance(style_doc_input, dict):
        style_doc = _bridge_runner.validate_style_document(json.loads(json.dumps(style_doc_input)))
    elif style_json_path_raw:
        style_doc = _bridge_runner.load_and_validate_style_json(Path(style_json_path_raw).expanduser().resolve())
    else:
        if not preset:
            preset = "preset_1_interface"
        style_doc = _bridge_runner.load_and_validate_style_json(_bridge_runner._default_preset_style(preset))

    if _to_bool(request_doc.get("rough", False)):
        style_doc = _bridge_runner._apply_rough_style_overrides(style_doc)
    style_doc = _bridge_runner._apply_color_follow_override(style_doc, _to_bool(request_doc.get("color_follow", False)))
    style_doc = _bridge_runner._apply_raw_view_override(style_doc, enabled=_to_bool(request_doc.get("raw_view", False)))
    style_doc = _bridge_runner._apply_save_blend_override(
        style_doc,
        enabled=_to_bool(request_doc.get("save_blend", False)),
        blend_output=str(request_doc.get("blend_output", "") or ""),
    )
    return preset, style_doc


@dataclass
class RenderTask:
    request_id: str
    request_doc: dict[str, Any]
    submitted_at: float = field(default_factory=time.time)
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


class RenderServerState:
    def __init__(
        self,
        *,
        worker: PersistentBlenderWorker,
        default_job_timeout: float,
        verbose: bool = False,
    ) -> None:
        self.worker = worker
        self.default_job_timeout = max(30.0, float(default_job_timeout))
        self.verbose = bool(verbose)

        self.queue: queue.Queue[RenderTask | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.dispatch_thread: threading.Thread | None = None

        self.completed_count = 0
        self.failed_count = 0
        self.active_request_id = ""
        self.active_started_at = 0.0
        self._status_lock = threading.Lock()

    def log(self, message: str) -> None:
        if not self.verbose:
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        print(f"[render_server {timestamp}] {message}", flush=True)

    def start(self) -> None:
        if self.dispatch_thread is not None and self.dispatch_thread.is_alive():
            return
        self.dispatch_thread = threading.Thread(target=self._dispatch_loop, name="render-server-dispatch", daemon=True)
        self.dispatch_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.queue.put(None)
        if self.dispatch_thread is not None:
            self.dispatch_thread.join(timeout=5.0)
        self.worker.stop()
        self.log("server-stopped")

    def submit(self, request_doc: dict[str, Any]) -> RenderTask:
        task = RenderTask(request_id=uuid.uuid4().hex[:12], request_doc=request_doc)
        self.queue.put(task)
        self.log(f"request-enqueued id={task.request_id} queue_depth={self.queue_depth()}")
        return task

    def queue_depth(self) -> int:
        return int(self.queue.qsize())

    def _set_active(self, request_id: str) -> None:
        with self._status_lock:
            self.active_request_id = request_id
            self.active_started_at = time.time()

    def _clear_active(self, *, success: bool) -> None:
        with self._status_lock:
            self.active_request_id = ""
            self.active_started_at = 0.0
            if success:
                self.completed_count += 1
            else:
                self.failed_count += 1

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            active = str(self.active_request_id)
            started_at = float(self.active_started_at or 0.0)
            completed = int(self.completed_count)
            failed = int(self.failed_count)
        active_runtime = 0.0
        if active and started_at > 0:
            active_runtime = max(0.0, time.time() - started_at)
        return {
            "queue_depth": self.queue_depth(),
            "active_request_id": active,
            "active_runtime_seconds": round(active_runtime, 3),
            "completed_count": completed,
            "failed_count": failed,
            "worker": self.worker.status(),
        }

    def _dispatch_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                task = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if task is None:
                continue

            self.log(f"request-start id={task.request_id} queued_for={time.time() - task.submitted_at:.2f}s")
            self._set_active(task.request_id)
            success = False
            try:
                task.result = _process_render_request(
                    request_id=task.request_id,
                    request_doc=task.request_doc,
                    state=self,
                )
                success = True
            except Exception as exc:
                task.result = {
                    "status": "error",
                    "request_id": task.request_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
                self.log(f"request-failed id={task.request_id} error={exc}")
            finally:
                self._clear_active(success=success)
                if success:
                    self.log(f"request-complete id={task.request_id}")
                task.event.set()


def _process_render_request(*, request_id: str, request_doc: dict[str, Any], state: RenderServerState) -> dict[str, Any]:
    state.log(f"request-validate id={request_id}")
    payload = request_doc.get("payload")
    if not isinstance(payload, dict):
        raise _bridge_runner.RunnerError("Request JSON must include object field 'payload'.")
    payload = _bridge_runner.validate_payload_document(payload)

    preset, style_doc = _prepare_style_doc(request_doc)
    state.log(f"request-style-ready id={request_id} preset={preset or '<style_doc>'}")

    keep_temp = _to_bool(request_doc.get("keep_temp", False))
    job_timeout_raw = request_doc.get("job_timeout_seconds")
    job_timeout = state.default_job_timeout
    if job_timeout_raw is not None:
        try:
            job_timeout = max(30.0, float(job_timeout_raw))
        except Exception:
            job_timeout = state.default_job_timeout

    run_id_raw = payload.get("run_id")
    run_id = str(run_id_raw).strip() if isinstance(run_id_raw, str) and run_id_raw.strip() else request_id

    temp_root: Path | None = None
    try:
        temp_root, _bundle_dir, structure_path, pdb_fallback_path, view_path = _bridge_runner._materialize_payload_bundle(payload, run_id)
        state.log(
            "request-bundle id="
            f"{request_id} temp_root={temp_root} structure={structure_path.name} "
            f"pdb_fallback={int(pdb_fallback_path is not None)}"
        )
        run_manifest_path = temp_root / "run_manifest.json"
        _bridge_runner._write_run_manifest(
            run_manifest_path,
            payload,
            chosen_structure_path=structure_path,
            style_doc=style_doc,
        )

        style_json_path = temp_root / "style.json"
        style_json_path.write_text(json.dumps(style_doc, indent=2), encoding="utf-8")

        preset_gate_target = _bridge_runner._is_preset_1_gate_target(
            preset=preset,
            style_json_path=str(request_doc.get("style_json", "") or ""),
            style_doc=style_doc,
        )
        if preset_gate_target and structure_path.suffix.lower() != ".cif":
            raise _bridge_runner.RunnerError(
                "Preset gate failure for preset_1_interface. Payload did not provide a usable CIF export."
            )

        output_name = _suggest_output_name(request_doc=request_doc, preset=preset, style_doc=style_doc)
        output_png = temp_root / output_name
        state.log(f"request-render id={request_id} output={output_png}")

        used_structure = structure_path

        def _submit_render_with_structure(structure_file: Path) -> dict[str, Any]:
            _bridge_runner._write_run_manifest(
                run_manifest_path,
                payload,
                chosen_structure_path=structure_file,
                style_doc=style_doc,
            )
            return state.worker.submit_render(
                structure_path=structure_file,
                view_json=view_path,
                style_json=style_json_path,
                output_png=output_png,
                run_manifest=run_manifest_path,
                timeout=job_timeout,
            )

        try:
            worker_result = _submit_render_with_structure(used_structure)
        except WorkerError:
            if (
                used_structure.suffix.lower() == ".cif"
                and pdb_fallback_path is not None
                and not preset_gate_target
            ):
                state.log(f"request-render-retry-pdb id={request_id}")
                worker_result = _submit_render_with_structure(pdb_fallback_path)
                used_structure = pdb_fallback_path
            else:
                raise

        if (
            not output_png.exists()
            and used_structure.suffix.lower() == ".cif"
            and pdb_fallback_path is not None
            and not preset_gate_target
        ):
            worker_result = _submit_render_with_structure(pdb_fallback_path)
            if output_png.exists():
                used_structure = pdb_fallback_path

        if not output_png.exists():
            raise _bridge_runner.RunnerError(
                "Worker reported completion but output PNG is missing. "
                f"expected={output_png}"
            )

        _bridge_runner._write_run_manifest(
            run_manifest_path,
            payload,
            chosen_structure_path=used_structure,
            style_doc=style_doc,
        )
        manifest_doc = _bridge_runner._load_json(run_manifest_path)
        if preset_gate_target:
            _bridge_runner._enforce_preset_1_backend_gates(manifest=manifest_doc, manifest_path=run_manifest_path)

        png_base64 = base64.b64encode(output_png.read_bytes()).decode("ascii")

        response = {
            "status": "completed",
            "mode": "server",
            "request_id": request_id,
            "run_id": run_id,
            "output_name": output_name,
            "png_base64": png_base64,
            "runtime_seconds": worker_result.get("runtime_seconds", 0.0),
            "manifest": manifest_doc,
            "manifest_path": str(run_manifest_path),
            "temp_dir": str(temp_root),
            "temp_kept": bool(keep_temp),
            "worker": worker_result,
        }

        output_blend_path = _bridge_runner._resolve_output_blend(output_png, style_doc)
        if output_blend_path is not None and output_blend_path.exists():
            response["output_blend_name"] = output_blend_path.name

        return response
    finally:
        if temp_root is not None and temp_root.exists() and not keep_temp:
            state.log(f"request-cleanup id={request_id} temp_root={temp_root}")
            shutil.rmtree(temp_root, ignore_errors=True)


class RenderHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], state: RenderServerState):
        super().__init__(server_address, RenderRequestHandler)
        self.state = state


class RenderRequestHandler(BaseHTTPRequestHandler):
    server: RenderHTTPServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        content_len = int(self.headers.get("Content-Length", "0") or "0")
        if content_len <= 0:
            raise _bridge_runner.RunnerError("Request body is empty.")
        raw = self.rfile.read(content_len)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise _bridge_runner.RunnerError("Request body is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise _bridge_runner.RunnerError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            status = self.server.state.status()
            payload = {
                "status": "ok",
                "queue_depth": status["queue_depth"],
                "worker": status["worker"],
            }
            self._write_json(HTTPStatus.OK, payload)
            return

        if parsed.path == "/status":
            self._write_json(HTTPStatus.OK, self.server.state.status())
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/shutdown":
            self._write_json(HTTPStatus.OK, {"status": "shutting_down"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if parsed.path != "/render":
            self._write_json(HTTPStatus.NOT_FOUND, {"status": "error", "error": "Not found"})
            return

        try:
            request_doc = self._read_json_body()
        except Exception as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"status": "error", "error": str(exc)})
            return

        task = self.server.state.submit(request_doc)
        self.server.state.log(
            f"http-render client={self.client_address[0]} request_id={task.request_id}"
        )

        wait_timeout: float | None = None
        raw_timeout = request_doc.get("request_timeout_seconds")
        if raw_timeout is not None:
            try:
                timeout_value = float(raw_timeout)
                if timeout_value > 0:
                    wait_timeout = timeout_value
            except Exception:
                wait_timeout = None

        done = task.event.wait(timeout=wait_timeout)
        if not done:
            self._write_json(
                HTTPStatus.GATEWAY_TIMEOUT,
                {
                    "status": "timeout",
                    "request_id": task.request_id,
                    "error": "Request timed out while waiting in render queue.",
                },
            )
            return

        result = task.result if isinstance(task.result, dict) else {"status": "error", "error": "Unknown failure"}
        status_text = str(result.get("status", "")).strip().lower()
        if status_text == "completed":
            self._write_json(HTTPStatus.OK, result)
            return

        self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, result)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="render_server",
        description="Run HTTP FIFO render server backed by persistent Blender worker.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--server-root",
        default=str(_bridge_runner.TMP_BRIDGE_ROOT / "server"),
        help="Server runtime root for socket and logs.",
    )
    parser.add_argument("--blender-exe", default=_bridge_runner.DEFAULT_BLENDER_EXE)
    parser.add_argument("--custom-script", default=str(_bridge_runner._default_custom_script()))
    parser.add_argument("--use-apptainer", type=_bridge_runner._parse_bool, default=False)
    parser.add_argument("--apptainer-image", default="")
    parser.add_argument("--worker-start-timeout", type=float, default=120.0)
    parser.add_argument("--job-timeout", type=float, default=3600.0)
    parser.add_argument("--verbose", type=_bridge_runner._parse_bool, default=False)
    return parser


def run_server(args: argparse.Namespace) -> int:
    root = Path(args.server_root).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).absolute()
    root.mkdir(parents=True, exist_ok=True)

    worker = PersistentBlenderWorker(
        root_dir=root,
        blender_exe=str(args.blender_exe),
        custom_script=Path(args.custom_script),
        use_apptainer=bool(args.use_apptainer),
        apptainer_image=str(args.apptainer_image or ""),
        worker_start_timeout=float(args.worker_start_timeout),
        logger=(lambda message: print(f"[worker] {message}", flush=True)) if bool(args.verbose) else None,
    )
    state = RenderServerState(worker=worker, default_job_timeout=float(args.job_timeout), verbose=bool(args.verbose))
    state.start()

    server = RenderHTTPServer((str(args.host), int(args.port)), state=state)
    state.log(
        "server-started "
        f"host={args.host} port={int(args.port)} root={root} "
        f"use_apptainer={int(bool(args.use_apptainer))}"
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        state.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return run_server(args)
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
