"""PyMOL-side request queue for this checkout's Blender renderer (local or cluster)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from itertools import count
from pathlib import Path

from co_pymol.core.files import write_json

_ADJECTIVES = (
    "amber",
    "bright",
    "calm",
    "clever",
    "coral",
    "curious",
    "daring",
    "dusk",
    "eager",
    "gentle",
    "golden",
    "happy",
    "indigo",
    "jolly",
    "kind",
    "lively",
    "lucky",
    "mellow",
    "merry",
    "mint",
    "nimble",
    "patient",
    "quiet",
    "rosy",
    "silver",
    "snowy",
    "soft",
    "steady",
    "sunny",
    "swift",
    "velvet",
    "vivid",
)
_ANIMALS = (
    "badger",
    "beaver",
    "bison",
    "crane",
    "deer",
    "dolphin",
    "dove",
    "falcon",
    "finch",
    "fox",
    "gecko",
    "heron",
    "ibis",
    "koala",
    "lark",
    "lemur",
    "lynx",
    "marten",
    "moth",
    "newt",
    "otter",
    "owl",
    "panda",
    "puffin",
    "quail",
    "rabbit",
    "raven",
    "robin",
    "seal",
    "swan",
    "tiger",
    "wren",
)


def render_name(name: str | None) -> str | None:
    """Normalize an optional name without accepting paths or reserved job IDs."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Render name must be text")
    normalized = re.sub(r"\s+", "-", name.strip()).lower()
    if not normalized:
        return None
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", normalized):
        raise ValueError(
            "Use a render name of up to 64 letters, numbers, spaces, hyphens, "
            "or underscores; paths are not allowed"
        )
    if re.fullmatch(r"[a-f0-9]{32}", normalized):
        raise ValueError("Render names cannot be 32-character request IDs")
    return normalized


def random_render_name() -> str:
    return f"{secrets.choice(_ADJECTIVES)}-{secrets.choice(_ANIMALS)}"


def create_render_directory(root: Path, name: str | None) -> Path:
    """Reserve a folder atomically, including when clients submit concurrently."""
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if name is None:
        for _ in range(32):
            name = random_render_name()
            try:
                directory = root / name
                directory.mkdir(mode=0o700)
                return directory
            except FileExistsError:
                pass
    # Repeated custom names (or an exhausted random pool) receive a suffix.
    for number in count(1):
        directory = root / (name if number == 1 else f"{name}-{number}")
        try:
            directory.mkdir(mode=0o700)
            return directory
        except FileExistsError:
            pass


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKENDS = ("local", "cluster")


def _render_defaults() -> tuple[str | None, str]:
    """This checkout's render environment: repo-local .runtime, else the user config."""
    home = REPO_ROOT / ".runtime"
    candidates = [home] if (home / "config.json").is_file() else []
    candidates.append(
        Path(os.environ.get("BLENDER_RENDER_HOME", "~/.config/blender-render"))
        .expanduser()
    )
    for root in candidates:
        try:
            python = json.loads((root / "config.json").read_text()).get("local_python")
        except (OSError, ValueError):
            continue
        if python:
            return str(root), python
    return None, str(REPO_ROOT / ".venv/bin/python")


@dataclass(frozen=True)
class BlenderConfig:
    """Where renders run and land. Every field has a working default."""

    output_directory: str = str(Path.home() / "Documents/blender-renders")
    backend: str = "local"
    cluster_profile: str = "lab"
    render_python: str | None = None
    render_home: str | None = None
    cluster_home: str | None = None
    require_gpu: bool = False
    wait_seconds: int = 1800
    project_directory: str | None = None
    notification_app: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> BlenderConfig:
        data = dict(data)
        if "profile" in data and "cluster_profile" not in data:
            data["cluster_profile"] = data.pop("profile")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError("Unknown Blender settings: " + ", ".join(sorted(unknown)))
        cfg = cls(**data)
        home, python = _render_defaults()
        data.setdefault("render_python", python)
        if cfg.render_home is None and home is not None:
            data["render_home"] = home
        for name in (
            "output_directory",
            "render_python",
            "render_home",
            "cluster_home",
            "project_directory",
            "notification_app",
        ):
            if data.get(name) is None:
                continue
            value = Path(data[name]).expanduser()
            if not value.is_absolute():
                raise ValueError(f"{name} must be an absolute path")
            data[name] = str(value.absolute())
        cfg = cls(**data)
        if cfg.backend not in BACKENDS:
            raise ValueError("backend must be 'local' or 'cluster'")
        if not Path(cfg.render_python).is_file():
            raise ValueError(
                "render_python must point to the Blender Render environment's Python "
                "(run blender-render setup in this checkout)"
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", cfg.cluster_profile):
            raise ValueError("Invalid cluster profile name")
        if type(cfg.require_gpu) is not bool:
            raise ValueError("require_gpu must be a boolean")
        if type(cfg.wait_seconds) is not int or not 30 <= cfg.wait_seconds <= 14400:
            raise ValueError("wait_seconds must be between 30 and 14400")
        return cfg

    @classmethod
    def load(cls, path: Path) -> BlenderConfig:
        path = path.expanduser()
        return cls.from_dict(json.loads(path.read_text()) if path.is_file() else {})

    def using(self, backend: str | None) -> BlenderConfig:
        """This configuration with a per-request backend ('local' or 'cluster')."""
        if backend is None:
            return self
        aliases = {"local": "local", "cluster": "cluster", "lab": "cluster", "gpu": "cluster"}
        if backend not in aliases:
            raise ValueError("backend must be 'local' or 'cluster'")
        return BlenderConfig.from_dict({**asdict(self), "backend": aliases[backend]})


class BlenderBridge:
    """File-backed request handles survive PyMOL restarts; the renderer owns jobs."""

    def __init__(self) -> None:
        self._config: BlenderConfig | None = None
        self._lock = threading.Lock()
        self._processes: list[subprocess.Popen] = []

    @property
    def config(self) -> BlenderConfig:
        if self._config is None:
            self.configure(
                os.environ.get(
                    "CO_PYMOL_BLENDER_CONFIG",
                    str(Path.home() / ".config/co-pymol/blender.json"),
                )
            )
        assert self._config is not None
        return self._config

    def configure(self, path: str) -> dict:
        cfg = BlenderConfig.load(Path(path))
        with self._lock:
            self._config = cfg
        return {"status": "configured", **asdict(cfg)}

    @staticmethod
    def _command(cfg: BlenderConfig, action: str, path: Path) -> list[str]:
        return [
            cfg.render_python,
            "-B",
            str(Path(__file__).with_name("blender_worker.py")),
            action,
            str(path),
        ]

    @staticmethod
    def _environment(cfg: BlenderConfig) -> dict[str, str]:
        # Keep SSH agent access, but do not load PyMOL's Python libraries into
        # the render environment's separate interpreter.
        env = os.environ.copy()
        for name in ("PYTHONHOME", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
            env.pop(name, None)
        env.update(
            PYTHONPATH=str(REPO_ROOT),
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
        )
        env.pop("BLENDER_RENDER_HOME", None)
        home = cfg.cluster_home if cfg.backend == "cluster" else cfg.render_home
        if home:
            env["BLENDER_RENDER_HOME"] = home
        return env

    def presets(self, *, images: bool = False) -> dict:
        cfg = self.config
        result = subprocess.run(
            self._command(cfg, "gallery" if images else "presets", REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=self._environment(cfg),
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-4000:] or "Cannot read presets")
        return json.loads(result.stdout)

    def select_presets(self, presets: list[str] | None, all_presets: bool) -> list[str]:
        if all_presets and presets:
            raise ValueError("Choose either presets or all_presets, not both")
        if not all_presets and not presets:
            raise ValueError("Provide presets or explicitly set all_presets=True")
        catalog = self.presets()["presets"]
        aliases = {
            alias: item["id"]
            for item in catalog
            for alias in [item["id"], *item.get("aliases", [])]
        }
        requested = [item["id"] for item in catalog] if all_presets else presets
        if not isinstance(requested, list) or any(
            not isinstance(p, str) or p not in aliases for p in requested
        ):
            raise ValueError("Unknown preset; use blender_presets to see available IDs")
        return list(dict.fromkeys(aliases[p] for p in requested))

    def _launch(self, cfg: BlenderConfig, directory: Path, action: str) -> None:
        with self._lock, (directory / "adapter.log").open("ab") as log:
            self._processes = [p for p in self._processes if p.poll() is None]
            process = subprocess.Popen(
                self._command(cfg, action, directory),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                cwd=directory,
                start_new_session=True,
                env=self._environment(cfg),
            )
            self._processes.append(process)

    def submit(
        self,
        payload: dict,
        *,
        preset: str,
        width: int,
        height: int,
        samples: int,
        keep_colors: bool,
        save_blend: bool,
        pse: bytes | None = None,
        name: str | None = None,
        protein: str | None = None,
        view: str | None = None,
        animation: dict | None = None,
        backend: str | None = None,
        _batch: dict | None = None,
    ) -> dict:
        cfg = self.config.using(backend)
        name = render_name(name)
        if pse is not None and (not isinstance(pse, bytes) or not pse):
            raise ValueError("The captured PyMOL session must be nonempty bytes")
        if not preset:
            raise ValueError("A preset is required")
        for field, value, minimum, maximum in (
            ("width", width, 64, 8192),
            ("height", height, 0, 8192),
            ("samples", samples, 1, 4096),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{field} must be between {minimum} and {maximum}")
        options = {
            "preset": preset,
            "width": width,
            "samples": samples,
            "color_follow": keep_colors,
            "save_blend": save_blend,
            "preserve_view": True,
        }
        if not _batch and cfg.project_directory:
            # A single preset uses the same scene/variant layout as a batch.
            receipt = self.submit_batch(
                payload,
                presets=[preset],
                pse=pse,
                name=name,
                protein=protein,
                view=view,
                width=width,
                height=height,
                samples=samples,
                keep_colors=keep_colors,
                save_blend=save_blend,
                animation=animation,
                backend=backend,
            )
            return self.status(receipt["runs"][0]["request_id"])
        if not _batch and (protein is not None or view is not None):
            raise ValueError("Configure project_directory to use protein/view folders")
        if height:
            options["height"] = height
        if animation is not None:
            # One animation job; the backend validates and renders every frame.
            if not isinstance(animation, dict) or "mode" not in animation:
                raise ValueError("animation must be a backend animation object")
            if animation["mode"] in {"rock", "360"}:
                # The backend's own orbit controls, sized like any render.
                options["animation"] = animation["mode"]
                options.update(
                    (key, animation[key])
                    for key in ("duration", "fps", "rock_angle")
                    if key in animation
                )
            else:
                # Overrides are checked before top-level sizes apply, so the
                # frame budget must see this render's own dimensions.
                output = {"animation": animation, "width": width}
                viewport = payload.get("view", {}).get("viewport_px")
                if height:
                    output["height"] = height
                elif isinstance(viewport, list) and len(viewport) == 2 and viewport[0]:
                    output["height"] = max(2, round(width * viewport[1] / viewport[0]))
                options.setdefault("overrides", {})["output"] = output
        request_id = uuid.uuid4().hex
        if _batch:
            directory = Path(_batch["directory"]) / "renders" / preset
            directory.mkdir(parents=True, mode=0o700, exist_ok=False)
            display_name = _batch["name"] + "--" + preset
        else:
            directory = create_render_directory(Path(cfg.output_directory), name)
            display_name = directory.name
        state = {"status": "starting", **(_batch.get("snapshot", {}) if _batch else {})}
        if pse is not None:
            pse_path = directory / "scene.pse"
            # The unique private request directory is not visible to a worker
            # until every capture file is complete. A write failure prevents
            # submission; it must never silently drop the session snapshot.
            with pse_path.open("xb") as stream:
                stream.write(pse)
            pse_path.chmod(0o600)
            state.update(
                pse_path=str(pse_path),
                pse_sha256=hashlib.sha256(pse).hexdigest(),
            )
        scene_path = (
            Path(_batch["directory"]) / "scene.bundle.json"
            if _batch
            else directory / "scene.bundle.json"
        )
        if not _batch:
            write_json(scene_path, payload)
        write_json(
            directory / "request.json",
            {
                "request_id": request_id,
                "name": display_name,
                "batch_id": _batch["request_id"] if _batch else None,
                "protein": _batch.get("protein") if _batch else None,
                "view": _batch.get("view") if _batch else None,
                "scene_path": str(scene_path),
                "created_at": time.time(),
                "config": asdict(cfg),
                "options": options,
                "scene_sha256": (
                    _batch["scene_sha256"]
                    if _batch
                    else hashlib.sha256(scene_path.read_bytes()).hexdigest()
                ),
            },
        )
        write_json(directory / "state.json", state)
        if _batch:
            return self.status(request_id)
        try:
            self._launch(cfg, directory, "run")
        except OSError as exc:
            write_json(
                directory / "state.json",
                {
                    **state,
                    "status": "needs_attention",
                    "error": str(exc),
                },
            )
            raise
        return self.status(request_id)

    def submit_batch(
        self,
        payload: dict,
        *,
        presets: list[str],
        pse: bytes,
        name: str | None = None,
        protein: str | None = None,
        view: str | None = None,
        **options,
    ) -> dict:
        """Persist one snapshot and all requests, then detach the batch helper."""
        name = render_name(name)
        if not presets or len(presets) > 100 or len(set(presets)) != len(presets):
            raise ValueError("Provide between 1 and 100 distinct presets")
        if any(not re.fullmatch(r"[a-z0-9_]+", p) for p in presets):
            raise ValueError("Use canonical preset IDs for a batch")
        if not isinstance(pse, bytes) or not pse:
            raise ValueError("The captured PyMOL session must be nonempty bytes")
        cfg = self.config.using(options.get("backend"))
        if cfg.project_directory:
            if name and view is not None:
                raise ValueError("Use view or name for the saved view, not both")
            if protein is None:
                objects = payload.get("provenance", {}).get("enabled_objects", [])
                if len(objects) != 1:
                    raise ValueError(
                        "Specify protein for a scene with multiple or unnamed objects"
                    )
                protein = objects[0]
            if not isinstance(protein, str):
                raise ValueError("Protein name must be text")
            protein = re.sub(r"\s+", "-", protein.strip())
            # Validate paths with the same rules as render names, retaining case
            # for familiar protein/object identifiers such as 5GGS.
            if render_name(protein) is None:
                raise ValueError("Protein name must not be blank")
            view = render_name(view) or name or "overview"
            project = Path(cfg.project_directory)
            root = project / "figures" / protein
            if (project / "figures").is_symlink() or root.is_symlink():
                raise ValueError("Protein/view output directories cannot be symlinks")
            directory = create_render_directory(root, view)
            view = directory.name
            display_name = f"{protein.lower()}--{view}"
        else:
            if protein is not None or view is not None:
                raise ValueError(
                    "Configure project_directory to use protein/view folders"
                )
            directory = create_render_directory(Path(cfg.output_directory), name)
            display_name = directory.name
        pse_path = directory / "scene.pse"
        with pse_path.open("xb") as stream:
            stream.write(pse)
        pse_path.chmod(0o600)
        write_json(directory / "scene.bundle.json", payload)
        batch = {
            "kind": "batch",
            "request_id": uuid.uuid4().hex,
            "name": display_name,
            "protein": protein,
            "view": view,
            "directory": str(directory),
            "created_at": time.time(),
            "config": asdict(cfg),
            "options": {},
            "scene_sha256": hashlib.sha256(
                (directory / "scene.bundle.json").read_bytes()
            ).hexdigest(),
            "snapshot": {
                "pse_path": str(pse_path),
                "pse_sha256": hashlib.sha256(pse).hexdigest(),
            },
            "children": [],
            "prepared": False,
        }
        write_json(directory / "request.json", batch)
        write_json(directory / "state.json", {"status": "starting"})
        try:
            for preset in presets:
                child = self.submit(payload, preset=preset, _batch=batch, **options)
                batch["children"].append(
                    {k: child[k] for k in ("request_id", "name", "preset", "directory")}
                )
            batch["prepared"] = True
            write_json(directory / "request.json", batch)
            self._launch(cfg, directory, "batch")
        except Exception as exc:
            write_json(directory / "request.json", batch)
            write_json(
                directory / "state.json",
                {
                    "status": "needs_attention",
                    "error": str(exc),
                },
            )
            raise
        return {
            "batch_id": batch["request_id"],
            "request_id": batch["request_id"],
            "name": batch["name"],
            "protein": protein,
            "view": view,
            "status": "accepted_locally",
            "count": len(presets),
            "directory": str(directory),
            "pse_path": str(pse_path),
            "runs": batch["children"],
            "message": (
                f"Saved one scene for {len(presets)} presets. "
                "Background submission is underway; "
                "you can continue using PyMOL and this chat."
            ),
            "next_action": "return_to_user",
        }

    def _requests(self) -> list[tuple[Path, dict]]:
        """Discover saved runs directly; no index or running process is required."""
        root = Path(self.config.output_directory)
        requests = []
        directories = list(root.iterdir()) if root.exists() else []
        if self.config.project_directory:
            figures = Path(self.config.project_directory) / "figures"
            if figures.is_dir() and not figures.is_symlink():
                for protein in figures.iterdir():
                    if protein.is_dir() and not protein.is_symlink():
                        directories.extend(protein.iterdir())
        for parent in tuple(directories):
            children = parent / "renders"
            if (
                not parent.is_symlink()
                and not children.is_symlink()
                and children.is_dir()
            ):
                directories.extend(children.iterdir())
        for directory in dict.fromkeys(directories):
            if directory.is_symlink() or not directory.is_dir():
                continue
            path = directory / "request.json"
            if path.is_symlink():
                continue
            try:
                request = json.loads(path.read_text())
                if not isinstance(request, dict) or not re.fullmatch(
                    r"[a-f0-9]{32}", str(request.get("request_id", ""))
                ):
                    continue
                if not isinstance(request.get("options", {}), dict):
                    continue
                request.setdefault("created_at", path.stat().st_mtime)
                requests.append((directory, request))
            except (OSError, ValueError):
                # Ignore unrelated or incomplete folders, including one being
                # created by another client. Never start work while discovering.
                continue
        return requests

    def _directory(self, request_id: str) -> Path:
        if not isinstance(request_id, str):
            raise ValueError("Use a render name or request_id")
        reference = re.sub(r"\s+", "-", request_id.strip()).lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,255}", reference):
            raise ValueError("Use a render name or request_id, not a path")
        requests = self._requests()
        if re.fullmatch(r"[a-f0-9]{32}", reference):
            matches = [d for d, r in requests if r["request_id"] == reference]
        else:
            matches = [d for d, r in requests if r.get("name", d.name) == reference]
        if len(matches) > 1:
            raise ValueError("Ambiguous render reference; use its unique request_id")
        if not matches:
            raise ValueError(
                "Unknown Blender render. Use blender_renders to find saved runs."
            )
        return matches[0]

    def status(self, request_id: str) -> dict:
        directory = self._directory(request_id)
        request = json.loads((directory / "request.json").read_text())
        state = json.loads((directory / "state.json").read_text())
        if request.get("kind") == "batch":
            children = [
                self.presentation(self.status(c["request_id"]))
                for c in request["children"]
            ]
            counts = {
                key: sum(c["status"] == key for c in children)
                for key in {c["status"] for c in children}
            }
            if state["status"] != "needs_attention":
                state["status"] = (
                    "completed"
                    if children and counts.get("completed") == len(children)
                    else "cancelled"
                    if children and counts.get("cancelled") == len(children)
                    else "needs_attention"
                    if any(
                        c["status"] in {"needs_attention", "failed"} for c in children
                    )
                    else "cancelled"
                    if children
                    and all(c["status"] in {"completed", "cancelled"} for c in children)
                    else "starting"
                    if children and counts.get("starting") == len(children)
                    else "running"
                )
            state.update(
                kind="batch",
                runs=children,
                counts=counts,
                count=len(children),
                outputs={"scene.pse": request["snapshot"]["pse_path"]},
            )
        pid = state.get("worker_pid")
        if (
            state.get("status") == "rendering"
            and isinstance(pid, int)
            and request.get("config", {}).get("backend", "local") == "local"
        ):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                state.update(
                    status="needs_attention",
                    error="The local render helper stopped before finishing.",
                )
            except PermissionError:
                pass
        if state.get("pse_path"):
            state["outputs"] = {
                **state.get("outputs", {}),
                "scene.pse": state["pse_path"],
            }
        return {
            **state,
            "request_id": request["request_id"],
            "name": request.get("name", directory.name),
            "batch_id": request.get("batch_id"),
            "protein": request.get("protein"),
            "view": request.get("view"),
            "created_at": request.get(
                "created_at", (directory / "request.json").stat().st_mtime
            ),
            "preset": request.get("options", {}).get("preset"),
            "backend": request.get("config", {}).get("backend", "local"),
            "directory": str(directory),
            "cancel_requested": (directory / "cancel.request").exists(),
        }

    @staticmethod
    def presentation(state: dict) -> dict:
        """Concise progress for chat; full backend diagnostics remain on disk."""
        if state.get("kind") == "batch":
            return {
                "next_action": "return_to_user",
                **{
                    k: state[k]
                    for k in (
                        "request_id",
                        "name",
                        "kind",
                        "protein",
                        "view",
                        "status",
                        "counts",
                        "count",
                        "runs",
                        "directory",
                        "outputs",
                        "error",
                    )
                    if k in state
                },
            }
        backend = state.get("remote") or {}
        status = state["status"]
        stage = backend.get("stage") or status
        if status in {
            "starting",
            "submitting",
            "completed",
            "failed",
            "cancelled",
            "cancelling",
            "downloading",
            "needs_attention",
            "recovering",
        }:
            stage = status
        messages = {
            "starting": "Scene saved locally; preparing the render.",
            "submitting": "Submitting the saved scene to the render service.",
            "queued": "The request is queued; Blender is not confirmed running yet.",
            "waiting_for_compute": "Waiting for remote compute allocation.",
            "scene_build": "Blender is building the scene.",
            "rendering": "Blender is rendering.",
            "running": "The renderer is processing the request.",
            "recovering": "Reconnecting to the existing job; no new render submitted.",
            "transfer_pending": "The remote job is retained; reconnect to check it.",
            "child_observation_pending": (
                "The remote job is retained; reconnect to check it."
            ),
            "downloading": "Downloading and verifying the finished render.",
            "completed": "Render saved locally and ready to display.",
            "needs_attention": "Progress needs attention; see the saved error.",
            "failed": "The render failed; see the saved error.",
            "cancelled": "Render cancelled. Saved scene files are retained.",
            "cancelling": "Cancellation requested; waiting for confirmation.",
        }
        fields = (
            "request_id",
            "batch_id",
            "name",
            "protein",
            "view",
            "status",
            "job_id",
            "preset",
            "backend",
            "directory",
            "created_at",
            "updated_at",
            "outputs",
            "pse_path",
            "png_path",
            "gpu",
            "error",
            "notification",
        )
        result = {k: state[k] for k in fields if k in state}
        result.update(
            stage=stage, message=messages.get(stage, "Checking render progress.")
        )
        started = state.get("created_at")
        if isinstance(started, (int, float)):
            end = (
                state.get("updated_at", time.time())
                if status in {"completed", "failed", "cancelled"}
                else time.time()
            )
            result["elapsed_seconds"] = max(0, int(end - started))
        result["next_action"] = (
            "blender_result"
            if status == "completed"
            else "inspect_error"
            if status in {"needs_attention", "failed"}
            else "none"
            if status == "cancelled"
            else "return_to_user"
        )
        if status == "needs_attention" and state.get("job_id"):
            result["next_action"] = "blender_result"
        return result

    def recent(self, limit: int = 20) -> dict:
        """List newest saved requests with cached progress; never contact SSH."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        renders = []
        for directory, request in self._requests():
            if request.get("kind") == "batch":
                continue
            try:
                state = json.loads((directory / "state.json").read_text())
                if not isinstance(state, dict):
                    raise ValueError("Invalid state")
            except (OSError, ValueError):
                state = {
                    "status": "needs_attention",
                    "error": "Saved progress is missing or unreadable",
                }
            created_at = request.get("created_at")
            if type(created_at) not in (int, float):
                created_at = (directory / "request.json").stat().st_mtime
            renders.append(
                {
                    "request_id": request["request_id"],
                    "name": request.get("name", directory.name),
                    "batch_id": request.get("batch_id"),
                    "protein": request.get("protein"),
                    "view": request.get("view"),
                    "created_at": created_at,
                    "preset": request.get("options", {}).get("preset"),
                    "status": state.get("status", "needs_attention"),
                    "updated_at": state.get("updated_at"),
                    "job_id": state.get("job_id"),
                    "directory": str(directory),
                    "error": state.get("error"),
                }
            )
        renders.sort(key=lambda r: (r["created_at"], r["name"]), reverse=True)
        return {"renders": renders[:limit], "total": len(renders)}

    def cancel(self, request_id: str) -> dict:
        directory = self._directory(request_id)
        state = self.status(request_id)
        if state["status"] in {"completed", "cancelled", "failed"}:
            return state
        if state.get("kind") == "batch":
            for child in state["runs"]:
                self.cancel(child["request_id"])
            return self.status(request_id)
        (directory / "cancel.request").touch(mode=0o600)
        request = json.loads((directory / "request.json").read_text())
        # The owning worker confirms cancellation after stopping the local
        # runner or observing the gateway. This also works for batch children.
        # A stopped worker is resumed; its lock prevents concurrent operations.
        self._launch(BlenderConfig.from_dict(request["config"]), directory, "recover")
        return self.status(request_id)

    def result(self, request_id: str) -> tuple[dict, bytes | None]:
        directory = self._directory(request_id)
        state = self.status(request_id)
        if state.get("kind") == "batch":
            if state["status"] not in {"completed", "cancelled"}:
                request = json.loads((directory / "request.json").read_text())
                if request.get("prepared"):
                    self._launch(BlenderConfig.from_dict(request["config"]), directory, "batch")
            return state, None
        if state["status"] == "completed":
            png = Path(state["png_path"]).read_bytes()
            if hashlib.sha256(png).hexdigest() != state["png_sha256"]:
                raise RuntimeError("Saved PNG changed; refusing to display it")
            return state, png
        if state.get("job_id") and state["status"] not in {"cancelled", "failed"}:
            request = json.loads((directory / "request.json").read_text())
            self._launch(BlenderConfig.from_dict(request["config"]), directory, "recover")
        return self.status(request_id), None
