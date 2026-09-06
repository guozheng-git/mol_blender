"""Compatibility entrypoint for the authenticated asynchronous render service."""
from __future__ import annotations
import sys
from pathlib import Path
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_render.rendering import RenderServerState, _process_render_request, _prepare_style_doc

def main(argv=None):
    from blender_render.cli import main as cli_main
    return cli_main(["serve", *(sys.argv[1:] if argv is None else argv)])

if __name__ == "__main__":
    raise SystemExit(main())
