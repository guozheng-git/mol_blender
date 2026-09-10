"""Small, portable configuration shared by the CLI, PyMOL, and services."""
from __future__ import annotations

import json
import os
from pathlib import Path

VERSION = "0.2.0b1"


def config_root() -> Path:
    return Path(os.environ.get("BLENDER_RENDER_HOME", str(Path.home() / ".config" / "blender-render"))).expanduser()


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
