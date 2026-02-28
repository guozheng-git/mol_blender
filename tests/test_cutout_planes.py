from __future__ import annotations

import math

import pytest

from blender_render.cutout_planes import (
    DEFAULT_PLANE_ENABLED,
    legacy_box_scales_to_plane_scales,
    resolve_frame_axes,
    resolve_plane_enabled,
    resolve_plane_scales,
)


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


def test_resolve_frame_axes_returns_orthonormal_basis():
    resolved = resolve_frame_axes(
        {
            "x": [2.0, 0.0, 0.0],
            "y": [0.0, 3.0, 0.0],
            "z": [0.0, 0.0, 4.0],
        }
    )

    x = resolved["x"]
    y = resolved["y"]
    z = resolved["z"]
    assert math.isclose(_dot(x, x), 1.0, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(_dot(y, y), 1.0, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(_dot(z, z), 1.0, rel_tol=1e-6, abs_tol=1e-6)
    assert math.isclose(_dot(x, y), 0.0, abs_tol=1e-6)
    assert math.isclose(_dot(x, z), 0.0, abs_tol=1e-6)
    assert math.isclose(_dot(y, z), 0.0, abs_tol=1e-6)


def test_resolve_frame_axes_rejects_degenerate_axes():
    with pytest.raises(ValueError, match="degenerate"):
        resolve_frame_axes(
            {
                "x": [1.0, 0.0, 0.0],
                "y": [2.0, 0.0, 0.0],
                "z": [3.0, 0.0, 0.0],
            }
        )


def test_resolve_plane_scales_uses_legacy_defaults_when_missing():
    resolved = resolve_plane_scales(
        None,
        width_scale=1.2,
        depth_scale=0.7,
        height_scale=0.9,
    )

    assert resolved == {
        "left": 1.2,
        "right": 1.2,
        "top": 0.9,
        "bottom": 0.9,
        "back": 0.7,
    }


def test_resolve_plane_scales_overrides_selected_keys():
    resolved = resolve_plane_scales(
        {"left": 1.4, "back": 0.6},
        width_scale=1.0,
        depth_scale=0.5,
        height_scale=1.1,
    )

    assert resolved["left"] == 1.4
    assert resolved["right"] == 1.0
    assert resolved["top"] == 1.1
    assert resolved["bottom"] == 1.1
    assert resolved["back"] == 0.6


def test_resolve_plane_enabled_defaults_and_overrides():
    resolved = resolve_plane_enabled({"left": False, "top": False})

    assert resolved["left"] is False
    assert resolved["top"] is False
    for key, value in DEFAULT_PLANE_ENABLED.items():
        if key not in {"left", "top"}:
            assert resolved[key] is value


def test_legacy_box_scale_mapping_is_stable():
    mapped = legacy_box_scales_to_plane_scales(
        width_scale=1.3,
        depth_scale=0.8,
        height_scale=1.1,
    )
    assert mapped == {
        "left": 1.3,
        "right": 1.3,
        "top": 1.1,
        "bottom": 1.1,
        "back": 0.8,
    }
