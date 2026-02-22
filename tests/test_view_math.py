import math

import pytest

from pymol_blender_bridge.view_math import (
    PYMOL_TO_BLENDER_CAMERA_SPACE,
    compute_blender_camera_from_pymol_view,
    compute_rotation_matrices,
    convert_pymol_camera_vector_to_blender,
    parse_rotation_from_view,
)


SAMPLE_VIEW = [
    0.8660254,
    0.5,
    0.0,
    -0.46984631,
    0.81379768,
    0.34202014,
    0.17101007,
    -0.29619813,
    0.93969262,
    1.5,
    -2.0,
    -25.0,
    10.0,
    20.0,
    30.0,
    2.0,
    120.0,
    45.0,
]


EXPECTED_MATRIX = [
    [-0.8660254, -0.5, 0.0, 9.7009619],
    [0.46984631, -0.81379768, 0.34202014, 30.882868325],
    [-0.17101007, 0.29619813, 0.93969262, 52.643404135],
    [0.0, 0.0, 0.0, 1.0],
]


def _mat_vec_mul(mat, vec):
    return [sum(mat[i][j] * vec[j] for j in range(3)) for i in range(3)]


def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _transpose(mat):
    return [[mat[j][i] for j in range(3)] for i in range(3)]


def _vec_sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _assert_matrix_close(actual, expected, tol=1e-6):
    assert len(actual) == len(expected)
    for row_idx, row in enumerate(actual):
        assert row == pytest.approx(expected[row_idx], abs=tol)


def test_rotation_is_orthonormal():
    rotation = parse_rotation_from_view(SAMPLE_VIEW)
    columns = [[rotation[row][col] for row in range(3)] for col in range(3)]

    for col in columns:
        assert math.sqrt(sum(v * v for v in col)) == pytest.approx(1.0, abs=1e-6)

    for i in range(3):
        for j in range(i + 1, 3):
            dot = sum(columns[i][k] * columns[j][k] for k in range(3))
            assert dot == pytest.approx(0.0, abs=1e-6)


def test_camera_position_maps_origin_correctly():
    spec = compute_blender_camera_from_pymol_view(SAMPLE_VIEW)
    rotation = parse_rotation_from_view(SAMPLE_VIEW)
    cam_pos_world = [spec.matrix_world[r][3] for r in range(3)]
    o_cam_p = SAMPLE_VIEW[9:12]
    o_model = SAMPLE_VIEW[12:15]

    projected = _mat_vec_mul(rotation, _vec_sub(o_model, cam_pos_world))
    assert projected == pytest.approx(o_cam_p, abs=1e-6)


def test_blender_conversion_and_inverse_relations():
    world_from_cam, cam_from_world = compute_rotation_matrices(SAMPLE_VIEW)
    rotation = parse_rotation_from_view(SAMPLE_VIEW)
    spec = compute_blender_camera_from_pymol_view(SAMPLE_VIEW)
    cam_pos_world = [spec.matrix_world[r][3] for r in range(3)]
    o_model = SAMPLE_VIEW[12:15]
    o_cam_p = SAMPLE_VIEW[9:12]

    o_cam_b_expected = convert_pymol_camera_vector_to_blender(o_cam_p)
    o_cam_b_projected = _mat_vec_mul(cam_from_world, _vec_sub(o_model, cam_pos_world))
    assert o_cam_b_projected == pytest.approx(o_cam_b_expected, abs=1e-6)

    identity = _mat_mul(world_from_cam, cam_from_world)
    identity_2 = _mat_mul(cam_from_world, world_from_cam)
    expected_identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    _assert_matrix_close(identity, expected_identity)
    _assert_matrix_close(identity_2, expected_identity)

    # Sanity: cam_from_world should equal M @ R.
    m = PYMOL_TO_BLENDER_CAMERA_SPACE
    expected_cam_from_world = _mat_mul(m, rotation)
    _assert_matrix_close(cam_from_world, expected_cam_from_world)
    _assert_matrix_close(world_from_cam, _transpose(expected_cam_from_world))


def test_regression_matrix_values():
    spec = compute_blender_camera_from_pymol_view(SAMPLE_VIEW, fov_deg=45.0, orthoscopic=False)
    assert spec.camera_type == "PERSP"
    assert spec.clip_start == pytest.approx(2.0, abs=1e-8)
    assert spec.clip_end == pytest.approx(120.0, abs=1e-8)
    assert spec.fov_deg == pytest.approx(45.0, abs=1e-8)
    _assert_matrix_close(spec.matrix_world, EXPECTED_MATRIX)
