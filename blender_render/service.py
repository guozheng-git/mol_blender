"""One job service used by HTTP, MCP, and all compute backends."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import math
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from . import core
from .jobs import JobError, JobStore

log = logging.getLogger(__name__)


class Backend(Protocol):
    def render(self, request: dict, job_id: str, cancelled) -> dict: ...
    def stop(self) -> None: ...


class HostBackend:
    """A single warm Blender process; Apptainer changes only its launcher."""
    def __init__(self, root: Path, blender: str = "", image: str = "", timeout: int = 600):
        from .render_worker_manager import PersistentBlenderWorker
        self.worker = PersistentBlenderWorker(
            root_dir=root, blender_exe=blender or core._detect_host_blender_executable(),
            custom_script=core._default_custom_script(), use_apptainer=bool(image),
            apptainer_image=image, worker_start_timeout=180,
        )
        self.timeout = timeout

    def render(self, request: dict, job_id: str, cancelled) -> dict:
        from .render_server import RenderServerState, _process_render_request
        finished = threading.Event()

        def monitor():
            while not finished.wait(0.2):
                if cancelled():
                    # submit_render holds the socket lock while waiting. Signal
                    # the process first so cancellation cannot wait for render.
                    self.worker.interrupt()
                    return

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            return _process_render_request(request_id=job_id, request_doc=request,
                state=RenderServerState(worker=self.worker, default_job_timeout=self.timeout, cancelled=cancelled))
        finally:
            finished.set()
            watcher.join(timeout=2)

    def stop(self):
        self.worker.interrupt()
        self.worker.stop()


def validate_input(payload: dict, *, max_atoms: int, max_bytes: int) -> dict:
    if not isinstance(payload, dict):
        raise JobError("Expected a structure payload object.")
    try:
        if len(json.dumps(payload, allow_nan=False).encode()) > max_bytes:
            raise JobError("Structure exceeds the upload size limit.", "too_large", 413)
        data = core.validate_payload_document(copy.deepcopy(payload))
    except JobError:
        raise
    except (TypeError, ValueError, core.PayloadValidationError) as exc:
        raise JobError(str(exc)) from exc
    from .export import _summarize_cif_text, _summarize_pdb_text
    text = data.get("cif_text") or data.get("pdb_text") or ""
    # Parse coordinates before queue admission so malformed files cannot start
    # a paid renderer. CIF metadata and secondary-structure hints stay intact.
    import gemmi
    try:
        molecule = gemmi.make_structure_from_block(gemmi.cif.read_string(text).sole_block()) if data.get('cif_text') else gemmi.read_pdb_string(text)
        count = 0
        for model in molecule:
            for chain in model:
                for residue in chain:
                    for atom in residue:
                        count += 1
                        if any(not math.isfinite(v) or abs(v) > 1e6 for v in (atom.pos.x, atom.pos.y, atom.pos.z)):
                            raise ValueError('Invalid atom coordinates')
        if not 0 < count <= max_atoms:
            raise JobError(f'Structure must contain 1 to {max_atoms} atoms.', 'atom_limit', 413)
    except JobError:
        raise
    except (RuntimeError, ValueError) as exc:
        raise JobError('Malformed structure or invalid coordinates.', 'invalid_structure', 422) from exc
    summary = _summarize_cif_text(text) if data.get("cif_text") else _summarize_pdb_text(text)
    if not 0 < summary.get("atom_count", 0) <= max_atoms:
        raise JobError(f"Structure must contain 1 to {max_atoms} atoms.", "atom_limit", 413)
    summary['atom_count'] = count
    data["structure_summary"] = summary
    data.pop("run_id", None)
    view = data["view"]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in view["pymol_view"]):
        raise JobError("Camera values must be finite numbers.")
    return data


def prepare_options(options: dict, payload: dict, *, public: bool, timeout: int) -> dict:
    """Compatibility boundary: all style resolution lives in spec.resolve_options."""
    from .spec import resolve_options
    try:
        return resolve_options(options, payload, public=public, timeout=timeout)
    except (ValueError, core.RunnerError) as exc:
        raise JobError(str(exc)) from exc


class RenderService:
    def __init__(self, store: JobStore, backend: Backend, *, recover: bool = True):
        self._imports_guard = threading.Lock()
        self._imports = {}
        self.store = store
        self.backend = backend
        self.wakeup = threading.Event()
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.cleanup_thread: threading.Thread | None = None
        self.last_activity = time.time()
        if recover:
            self.store.recover_interrupted()

    def start(self):
        if self.stopping.is_set() or self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="blender-render-fifo", daemon=True)
        self.thread.start()
        self.cleanup_thread = threading.Thread(target=self._maintain, name='blender-render-cleanup', daemon=True)
        self.cleanup_thread.start()

    def stop(self):
        self.stopping.set()
        self.wakeup.set()
        self.backend.stop()
        if self.thread:
            self.thread.join(timeout=10)
        if self.cleanup_thread:
            self.cleanup_thread.join(timeout=10)

    def _maintain(self):
        while not self.stopping.wait(2):
            try:
                self.store.cleanup()
            except Exception:
                log.exception('Temporary data cleanup failed')

    @contextmanager
    def _owner_import(self, owner):
        # Serialize one owner's imports across HTTP and MCP, so simultaneous
        # retries cannot reserve and launch duplicate paid conversions.
        with self._imports_guard:
            entry = self._imports.setdefault(owner, [threading.RLock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._imports_guard:
                entry[1] -= 1
                if not entry[1]:
                    del self._imports[owner]

    def import_payload(self, owner: str, payload: dict, name: str = "structure.cif", *, source_key: str | None = None) -> dict:
        with self._owner_import(owner):
            self.last_activity = time.time()
            p = self.store.policy
            data = validate_input(payload, max_atoms=p.max_atoms if p.public else 10000000, max_bytes=p.max_upload_bytes)
            return self.store.add_input(owner, data, name, source_key=source_key)

    def import_file(self, owner: str, data: bytes, suffix: str, name: str, converter) -> dict:
        if suffix not in {'.pse', '.cif', '.mmcif', '.pdb'}:
            raise JobError("Choose a .pse, .cif, or .pdb file.")
        if len(data) > self.store.policy.max_upload_bytes:
            raise JobError("File is too large.", "too_large", 413)
        key = suffix + ':' + hashlib.sha256(data).hexdigest()
        with self._owner_import(owner):
            existing = self.store.find_source(owner, key)
            if existing:
                return existing
            self.store.reserve_conversion(owner)
            try:
                payload = converter(data, suffix)
            except Exception as exc:
                raise JobError("The molecular file could not be converted. Attach a valid PSE, CIF, or PDB file.", "invalid_structure", 422) from exc
            return self.import_payload(owner, payload, name, source_key=key)

    @staticmethod
    def _client_hash(options, *, rerender=False):
        if not isinstance(options, dict):
            raise JobError('Render options must be an object.')
        canonical = dict(options)
        canonical.setdefault('preset', 'preset_1_interface')
        for flag in ('rough', 'raw_view', 'color_follow', 'keep_temp'):
            canonical.setdefault(flag, False)
        canonical.setdefault('preserve_view', True)
        if rerender:
            canonical['rerender'] = True
        try:
            return hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        except (TypeError, ValueError) as exc:
            raise JobError('Render options must be valid JSON.') from exc

    def render_file(self, owner, data, suffix, name, converter, options):
        """One upload-and-submit operation; retries never invent a paid job."""
        client_hash = self._client_hash(options)
        source_key = suffix + ':' + hashlib.sha256(data).hexdigest()
        with self._owner_import(owner):
            existing = self.store.file_replay(owner, source_key, client_hash)
            if existing:
                return existing
            source = self.import_file(owner, data, suffix, name, converter)
            return self.submit(owner, source['input_id'], options, secrets.token_hex(16))

    def submit(self, owner: str, input_id: str, options: dict, idempotency_key: str, *, rerender: bool = False) -> dict:
        self.last_activity = time.time()
        if not isinstance(rerender, bool):
            raise JobError('rerender must be a boolean')
        client_hash = self._client_hash(options, rerender=rerender)
        # Do not let completion delete the input between replay lookup and
        # normalization. This lock contains no network or compute operations.
        with self.store.lock:
            legacy_hash = hashlib.sha256(json.dumps(options, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            replay = self.store.submission_replay(owner, input_id, idempotency_key, client_hash, rerender=rerender, legacy_hash=legacy_hash if not rerender else None)
            if replay:
                return replay
            payload = self.store.input(owner, input_id)
            try:
                normalized = prepare_options(options, payload, public=self.store.policy.public, timeout=self.store.policy.max_job_seconds)
            except core.StyleValidationError as exc:
                raise JobError(str(exc)) from exc
            result = self.store.submit(owner, input_id, normalized, idempotency_key, client_hash=client_hash, rerender=rerender)
        self.wakeup.set()
        return result

    def _run(self):
        while not self.stopping.is_set():
            try:
                job = self.store.claim()
                if not job:
                    self.wakeup.wait(2)
                    self.wakeup.clear()
                    continue
                self._execute(job)
            except Exception:
                log.exception("Job dispatcher failed; admission remains conservative")
                self.stopping.wait(2)

    def _execute(self, job: dict):
        started = time.monotonic()
        identifier = job["id"]
        try:
            request = json.loads(job["options"])
            request["payload"] = self.store.input(job["owner"], job["input_id"])
            request["payload"]["run_id"] = identifier
            result = self.backend.render(request, identifier, lambda: self.stopping.is_set() or self.store.cancelled(identifier))
            if self.stopping.is_set():
                raise RuntimeError("Service stopped during render")
            if result.get("status") != "completed":
                raise RuntimeError("Renderer did not complete successfully")
            artifacts = self._save_artifacts(identifier, result)
            self.store.finish(identifier, result=artifacts, seconds=time.monotonic() - started)
            if self.store.cancelled(identifier):
                shutil.rmtree(self.store.artifacts / identifier, ignore_errors=True)
        except Exception as exc:
            log.exception("Render job %s failed", identifier)
            message = "Render failed. Check structure, preset, and resource limits." if self.store.policy.public else str(exc)
            seconds = self.store.policy.max_job_seconds if getattr(self.backend, "paid", False) else time.monotonic() - started
            self.store.finish(identifier, error=message, seconds=seconds)
            shutil.rmtree(self.store.artifacts / identifier, ignore_errors=True)
        finally:
            self.last_activity = time.time()

    def _save_artifacts(self, identifier: str, result: dict) -> dict:
        root = self.store.artifacts / identifier
        root.mkdir(mode=0o700, exist_ok=True)
        records = []
        for key, name, mime in (("png_base64", "render.png", "image/png"), ("blend_base64", "render.blend", "application/octet-stream")):
            if not result.get(key):
                continue
            data = base64.b64decode(result[key], validate=True)
            if key == "png_base64" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("Renderer returned invalid PNG bytes")
            if len(data) > 512 * 1024**2:
                raise RuntimeError("Render artifact exceeds 512 MiB limit")
            (root / name).write_bytes(data)
            records.append({"name": name, "mime_type": mime, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if not records or records[0]["name"] != "render.png":
            raise RuntimeError("Renderer returned no PNG")
        manifest = dict(result.get("manifest", {}))
        for key in ("chosen_structure_path", "temp_dir", "manifest_path"):
            manifest.pop(key, None)
        manifest.update(job_id=identifier, runtime_seconds=result.get("runtime_seconds"), artifacts=list(records))
        data = json.dumps(manifest, indent=2).encode()
        (root / "manifest.json").write_bytes(data)
        records.append({"name": "manifest.json", "mime_type": "application/json", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        with self.store.lock:
            if self.store.policy.public:
                existing_bytes = self.store.db.execute("SELECT COALESCE(SUM(json_extract(result,'$.bytes')),0) FROM jobs WHERE result IS NOT NULL").fetchone()[0]
                existing_bytes += self.store.db.execute("SELECT COALESCE(SUM(size),0) FROM inputs").fetchone()[0]
                if existing_bytes + sum(item["size"] for item in records) > self.store.policy.max_storage_bytes:
                    raise RuntimeError("Artifact storage limit exceeded")
            try:
                self.store.sync()
            except BaseException:
                self.store.healthy = False
                raise
        return {"job_id": identifier, "artifacts": records, "manifest": manifest, "bytes": sum(item["size"] for item in records)}

    def artifact(self, owner: str, identifier: str, name: str) -> tuple[Path, str]:
        result = self.store.result(owner, identifier)
        record = next((item for item in result["artifacts"] if item["name"] == name), None)
        if record is None or name not in {"render.png", "render.blend", "manifest.json"}:
            raise JobError("Artifact not found.", "not_found", 404)
        path = self.store.artifact_root(identifier) / name
        if not path.is_file():
            raise JobError('Temporary download is no longer available.', 'expired', 410)
        return path, record["mime_type"]
