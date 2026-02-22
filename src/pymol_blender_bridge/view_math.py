"""Math helpers for converting PyMOL get_view into Blender camera transforms."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

try:
    import numpy as _np
except Exception:  # pragma: no cover - exercised by environments without numpy
    _np = None


Matrix3 = list[list[float]]
Matrix4 = list[list[float]]
Vector3 = list[float]


# Empirically validated with a PyMOL identity view + axis marker render:
# PyMOL camera-space +X points right and +Y points up in image space.
# Blender camera-space uses the same handed axes, so no axis flip is required.
PYMOL_TO_BLENDER_CAMERA_SPACE: Matrix3 = [
    [1.0, 0.0, 0.0],
    [0.0, 1.0, 0.0],
    [0.0, 0.0, 1.0],
]


@dataclass
class CameraSpec:
    matrix_world: Matrix4
    camera_type: str
    fov_deg: float
    ortho_scale: float | None
    clip_start: float
    clip_end: float


def _mat_transpose_3x3(mat: Matrix3) -> Matrix3:
    if _np is not None:
        return _np.asarray(mat, dtype=float).T.tolist()
    return [[mat[r][c] for r in range(3)] for c in range(3)]


def _mat_mul_3x3(a: Matrix3, b: Matrix3) -> Matrix3:
    if _np is not None:
        return (_np.asarray(a, dtype=float) @ _np.asarray(b, dtype=float)).tolist()
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]


def _mat_vec_mul_3x3(mat: Matrix3, vec: Sequence[float]) -> Vector3:
    if _np is not None:
        return (_np.asarray(mat, dtype=float) @ _np.asarray(vec, dtype=float)).tolist()
    return [sum(mat[i][j] * vec[j] for j in range(3)) for i in range(3)]


def _vec_sub(a: Sequence[float], b: Sequence[float]) -> Vector3:
    return [a[idx] - b[idx] for idx in range(3)]


def _validate_view(pymol_view: Sequence[float]) -> list[float]:
    if len(pymol_view) != 18:
        raise ValueError("PyMOL view must contain exactly 18 floats from cmd.get_view().")
    values = [float(item) for item in pymol_view]
    if any(not math.isfinite(item) for item in values):
        raise ValueError("PyMOL view contains non-finite values.")
    return values


def parse_rotation_from_view(pymol_view: Sequence[float]) -> Matrix3:
    """Parse PyMOL's column-major rotation into a standard 3x3 row-major matrix."""

    v = _validate_view(pymol_view)
    # Columns are contiguous in PyMOL view vector.
    return [
        [v[0], v[3], v[6]],
        [v[1], v[4], v[7]],
        [v[2], v[5], v[8]],
    ]


def infer_orthoscopic(orthoscopic: bool | None, pymol_view: Sequence[float]) -> bool:
    if orthoscopic is not None:
        return bool(orthoscopic)
    values = _validate_view(pymol_view)
    return values[17] <= 0.0


def infer_fov_deg(fov_deg: float | None, pymol_view: Sequence[float]) -> float:
    if fov_deg is not None:
        parsed = float(fov_deg)
        if parsed <= 0:
            raise ValueError(f"fov_deg must be > 0; got {fov_deg!r}.")
        return parsed
    values = _validate_view(pymol_view)
    candidate = abs(values[17])
    if 5.0 <= candidate <= 120.0:
        return candidate
    return 50.0


def convert_pymol_camera_vector_to_blender(vector: Sequence[float]) -> Vector3:
    if len(vector) != 3:
        raise ValueError("Camera-space vector must have 3 elements.")
    return _mat_vec_mul_3x3(PYMOL_TO_BLENDER_CAMERA_SPACE, vector)


def compute_rotation_matrices(pymol_view: Sequence[float]) -> tuple[Matrix3, Matrix3]:
    """Return (world_from_cam_blender, cam_blender_from_world)."""

    r = parse_rotation_from_view(pymol_view)
    cam_blender_from_world = _mat_mul_3x3(PYMOL_TO_BLENDER_CAMERA_SPACE, r)
    world_from_cam_blender = _mat_mul_3x3(_mat_transpose_3x3(r), PYMOL_TO_BLENDER_CAMERA_SPACE)
    return world_from_cam_blender, cam_blender_from_world


def compute_blender_camera_from_pymol_view(
    pymol_view: Sequence[float],
    fov_deg: float | None = None,
    orthoscopic: bool | None = None,
) -> CameraSpec:
    values = _validate_view(pymol_view)
    r = parse_rotation_from_view(values)
    rt = _mat_transpose_3x3(r)
    o_cam_p = values[9:12]
    o_model = values[12:15]
    front = values[15]
    rear = values[16]

    cam_pos_world = _vec_sub(o_model, _mat_vec_mul_3x3(rt, o_cam_p))
    r_world_from_cam, _ = compute_rotation_matrices(values)
    matrix_world: Matrix4 = [
        [r_world_from_cam[0][0], r_world_from_cam[0][1], r_world_from_cam[0][2], cam_pos_world[0]],
        [r_world_from_cam[1][0], r_world_from_cam[1][1], r_world_from_cam[1][2], cam_pos_world[1]],
        [r_world_from_cam[2][0], r_world_from_cam[2][1], r_world_from_cam[2][2], cam_pos_world[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]

    clip_start = max(1e-3, float(front))
    clip_end = max(clip_start + 1e-3, float(rear))
    effective_fov_deg = infer_fov_deg(fov_deg, values)
    is_orthoscopic = infer_orthoscopic(orthoscopic, values)

    camera_type = "ORTHO" if is_orthoscopic else "PERSP"
    ortho_scale: float | None = None
    if is_orthoscopic:
        distance = max(1e-6, abs(o_cam_p[2]))
        ortho_scale = 2.0 * distance * math.tan(math.radians(effective_fov_deg) / 2.0)

    return CameraSpec(
        matrix_world=matrix_world,
        camera_type=camera_type,
        fov_deg=effective_fov_deg,
        ortho_scale=ortho_scale,
        clip_start=clip_start,
        clip_end=clip_end,
    )
