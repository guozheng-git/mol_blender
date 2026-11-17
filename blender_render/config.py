"""Small, portable configuration shared by the CLI, PyMOL, and local rendering."""
from __future__ import annotations

import json
import os
from pathlib import Path

VERSION = "0.4.0rc4"


def config_root() -> Path:
    """The renderer's configuration: runtimes, presets and render defaults."""
    return Path(os.environ.get("BLENDER_RENDER_HOME", str(Path.home() / ".config" / "blender-render"))).expanduser()


def cluster_root() -> Path:
    """Lab profiles (clusters/) and SSH connection state (cluster-connections/).

    Kept apart from config_root so a client can share one set of lab profiles while
    using its own renderer configuration, and a lab render then resolves presets
    exactly as a local render does. Without BLENDER_RENDER_CLUSTER_HOME this is
    config_root, as before.
    """
    explicit = os.environ.get("BLENDER_RENDER_CLUSTER_HOME")
    return Path(explicit).expanduser() if explicit else config_root()


def load_config() -> dict:
    path = config_root() / "config.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_config(config: dict) -> Path:
    root = config_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "config.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return path


def runtime_versions() -> dict:
    from .engine import resource_bytes
    return json.loads(resource_bytes(Path(__file__).with_name("runtime.lock.json")))
