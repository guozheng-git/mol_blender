"""Turn a PyMOL session into a workspace structure: one mmCIF of its enabled objects, plus its view and colors.

Runs inside the PyMOL environment. Reuses the renderer's own exporter (imported from the bundled wheel),
so multi-object sessions are flattened exactly as Blender renders them. PSE files can run code when
loaded: only import sessions the user supplied.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(next((HERE.parent / "assets" / "wheels").glob("*.whl"))))
sys.path.insert(0, str(HERE))

from mol_blender.scene.export import from_file  # noqa: E402
from camera import pymol_to_molstar  # noqa: E402


def main(source: str, out_dir: str) -> dict:
    session = Path(source).expanduser().resolve()
    directory = Path(out_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    payload = from_file(str(session))
    stem = session.name[:-4] if session.name.lower().endswith(".pse") else session.stem
    cif = directory / f"{stem}.cif"
    cif.write_text(payload["cif_text"])
    view = payload["view"]
    camera = pymol_to_molstar(view["pymol_view"])
    if view.get("fov_deg"):
        camera["fov_deg"] = view["fov_deg"]
    if "orthoscopic" in view:
        camera["mode"] = "orthographic" if view["orthoscopic"] else "perspective"
    hints = payload.get("color_hints", {})
    return {"structure": str(cif), "camera": camera, "chain_colors": hints.get("chain", {}),
            "objects": payload.get("provenance", {}).get("enabled_objects", []),
            "chain_mapping": payload.get("provenance", {}).get("chain_mapping", {}),
            "pymol_view": view["pymol_view"], "session": str(session)}


if __name__ == "__main__":
    stdout = os.dup(1)
    os.dup2(2, 1)  # PyMOL feedback goes to file descriptor 1; keep stdout for the JSON result
    try:
        result = main(sys.argv[1], sys.argv[2])
    finally:
        sys.stdout.flush()
        os.dup2(stdout, 1)
    print(json.dumps(result))
