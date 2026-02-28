"""Utility helpers for ligand cutout plane configuration.

These helpers are pure-Python and intentionally Blender-independent so they can
be unit-tested without bpy.
"""

from __future__ import annotations

import math
from typing import Any

PLANE_NAMES: tuple[str, ...] = ("left", "right", "top", "bottom", "back")
DEFAULT_PLANE_ENABLED: dict[str, bool] = {
    "left": True,
    "right": True,
    "top": True,
    "bottom": True,
    "back": True,
}
DEFAULT_PLANE_SCALES: dict[str, float] = {
    "left": 1.0,
    "right": 1.0,
    "top": 1.0,
    "bottom": 1.0,
    "back": 1.0,
}


def _to_float_triplet(raw: Any, *, label: str) -> tuple[float, float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError(f"{label} must be a numeric 3-tuple.")
    values: list[float] = []
    for idx, entry in enumerate(raw):
        try:
            values.append(float(entry))
        except Exception as exc:
            raise ValueError(f"{label}[{idx}] must be numeric.") from exc
    return (values[0], values[1], values[2])


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return float(left[0] * right[0] + left[1] * right[1] + left[2] * right[2])


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        float(left[1] * right[2] - left[2] * right[1]),
        float(left[2] * right[0] - left[0] * right[2]),
        float(left[0] * right[1] - left[1] * right[0]),
    )


def _norm(vec: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(vec, vec))


def _normalize(vec: tuple[float, float, float], *, label: str) -> tuple[float, float, float]:
    length = _norm(vec)
    if length <= 1e-9:
        raise ValueError(f"{label} must be non-zero.")
    inv = 1.0 / length
    return (float(vec[0] * inv), float(vec[1] * inv), float(vec[2] * inv))


def resolve_frame_axes(frame_axes: dict[str, Any]) -> dict[str, tuple[float, float, float]]:
    """Resolve and orthonormalize frame axes from style JSON.

    Uses y as primary axis, then orthogonalizes x against y, then recomputes z.
    """

    if not isinstance(frame_axes, dict):
        raise ValueError("ligand_cutout_frame_axes must be an object with x, y, z.")
    for axis_name in ("x", "y", "z"):
        if axis_name not in frame_axes:
            raise ValueError(f"ligand_cutout_frame_axes.{axis_name} is required.")

    x_raw = _to_float_triplet(frame_axes.get("x"), label="ligand_cutout_frame_axes.x")
    y_raw = _to_float_triplet(frame_axes.get("y"), label="ligand_cutout_frame_axes.y")
    z_raw = _to_float_triplet(frame_axes.get("z"), label="ligand_cutout_frame_axes.z")

    y = _normalize(y_raw, label="ligand_cutout_frame_axes.y")
    x_proj = (
        float(x_raw[0] - _dot(x_raw, y) * y[0]),
        float(x_raw[1] - _dot(x_raw, y) * y[1]),
        float(x_raw[2] - _dot(x_raw, y) * y[2]),
    )
    if _norm(x_proj) <= 1e-9:
        # fallback from z if x is colinear with y
        z_proj = (
            float(z_raw[0] - _dot(z_raw, y) * y[0]),
            float(z_raw[1] - _dot(z_raw, y) * y[1]),
            float(z_raw[2] - _dot(z_raw, y) * y[2]),
        )
        if _norm(z_proj) <= 1e-9:
            raise ValueError("ligand_cutout_frame_axes are degenerate (colinear).")
        z = _normalize(z_proj, label="ligand_cutout_frame_axes.z")
        x = _normalize(_cross(y, z), label="ligand_cutout_frame_axes.x")
    else:
        x = _normalize(x_proj, label="ligand_cutout_frame_axes.x")
        z = _normalize(_cross(x, y), label="ligand_cutout_frame_axes.z")
        if _dot(z, z_raw) < 0.0:
            z = (-z[0], -z[1], -z[2])
            x = _normalize(_cross(y, z), label="ligand_cutout_frame_axes.x")

    return {"x": x, "y": y, "z": z}


def legacy_box_scales_to_plane_scales(
    *,
    width_scale: float,
    depth_scale: float,
    height_scale: float,
) -> dict[str, float]:
    return {
        "left": float(width_scale),
        "right": float(width_scale),
        "top": float(height_scale),
        "bottom": float(height_scale),
        "back": float(depth_scale),
    }


def resolve_plane_enabled(planes_enabled: Any) -> dict[str, bool]:
    merged = dict(DEFAULT_PLANE_ENABLED)
    if not isinstance(planes_enabled, dict):
        return merged
    for name in PLANE_NAMES:
        if name in planes_enabled:
            merged[name] = bool(planes_enabled.get(name))
    return merged


def resolve_plane_scales(
    plane_scales: Any,
    *,
    width_scale: float,
    depth_scale: float,
    height_scale: float,
) -> dict[str, float]:
    merged = legacy_box_scales_to_plane_scales(
        width_scale=float(width_scale),
        depth_scale=float(depth_scale),
        height_scale=float(height_scale),
    )
    if not isinstance(plane_scales, dict):
        return merged
    for name in PLANE_NAMES:
        if name not in plane_scales:
            continue
        value = plane_scales.get(name)
        try:
            numeric = float(value)
        except Exception as exc:
            raise ValueError(f"ligand_cutout_plane_scales.{name} must be numeric.") from exc
        if numeric <= 0.0:
            raise ValueError(f"ligand_cutout_plane_scales.{name} must be > 0.")
        merged[name] = numeric
    return merged
