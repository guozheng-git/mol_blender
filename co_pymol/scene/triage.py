"""Triage-specific rendering: focus a structure and snapshot it.

This drives PyMOL; the pure navigation state lives in `co_pymol.core.triage`.
"""

from __future__ import annotations

from pathlib import Path

from co_pymol.scene.preview import apply_plddt_palette, render_image


def triage_render(cmd, path: Path, width: int = 800, height: int = 600) -> bytes:
    """Focus on `path` (loading it if needed), hide siblings, color by pLDDT, render.

    Caller holds the pymol_session lock.
    """
    obj_name = path.stem
    if obj_name not in cmd.get_object_list():
        cmd.load(str(path), obj_name)
    cmd.disable("all")
    cmd.enable(obj_name)
    cmd.show("cartoon", obj_name)
    cmd.hide("lines", obj_name)
    apply_plddt_palette(cmd, obj_name)
    cmd.orient(obj_name)
    cmd.bg_color("white")
    return render_image(cmd, width, height, ray=False)
