"""Convert a Mol* camera (from the viewer's "Copy view" button) into a PyMOL view matrix.

PyMOL's 18-value view: 0-8 model-to-camera rotation (column-major), 9-11 the rotation
origin in camera space (0, 0, -distance), 12-14 the origin in model space, 15/16 the
front/rear clipping distances, and 17 the field of view (negative = perspective).
"""
from __future__ import annotations

import json
import math
from pathlib import Path


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(v):
    length = math.sqrt(sum(c * c for c in v))
    if length < 1e-9:
        raise ValueError("Degenerate camera vector")
    return [c / length for c in v]


def load_view(value: str) -> dict:
    """Accept a JSON string, a path to a JSON file, or the viewer's wrapped object."""
    text = Path(value).read_text() if not value.lstrip().startswith("{") and Path(value).is_file() else value
    data = json.loads(text)
    return data.get("structure_studio_view", data)


def molstar_to_pymol(view: dict, radius: float) -> list[float]:
    """PyMOL get_view()/set_view() list for a Mol* camera; radius bounds the clipping slab."""
    if "pymol_view" in view:
        return [float(v) for v in view["pymol_view"]]
    position, target, up = view["position"], view["target"], view["up"]
    back = _sub(position, target)
    distance = math.sqrt(sum(c * c for c in back))
    z = _unit(back)
    x = _unit(_cross(up, z))
    y = _cross(z, x)
    fov = float(view.get("fov_deg", 45.0))
    orthographic = view.get("mode") == "orthographic"
    rotation = [x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]]
    front = max(1.0, distance - radius)
    return rotation + [0.0, 0.0, -distance] + list(map(float, target)) + [front, distance + radius, fov if orthographic else -fov]


def pymol_to_molstar(view: list[float]) -> dict:
    """Inverse mapping, used to seed the Mol* page with a PyMOL session's pose."""
    x, y, z = [view[0], view[3], view[6]], [view[1], view[4], view[7]], [view[2], view[5], view[8]]
    origin, offset = view[12:15], view[9:12]
    # The camera sits at camera-space (0, 0, 0): origin - R^T * offset in model space.
    position = [origin[i] - (offset[0] * x[i] + offset[1] * y[i] + offset[2] * z[i]) for i in range(3)]
    distance = -offset[2]
    target = [position[i] - z[i] * distance for i in range(3)]
    fov = abs(view[17]) if abs(view[17]) > 1 else 20.0
    return {"position": position, "target": list(target), "up": y, "fov_deg": fov,
            "mode": "orthographic" if view[17] > 0 else "perspective"}
