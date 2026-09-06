"""Compatibility entrypoint. New clients use blender-render."""
from __future__ import annotations
import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_render import core as _core
globals().update({name: getattr(_core, name) for name in dir(_core) if not name.startswith("__")})

def main(argv=None):
    from blender_render.cli import legacy_runner
    return legacy_runner(argv)

if __name__ == "__main__":
    raise SystemExit(main())
