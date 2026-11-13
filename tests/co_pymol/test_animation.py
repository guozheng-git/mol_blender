"""Recipe timelines: pure maths, no PyMOL."""

from __future__ import annotations

import math

import pytest

from co_pymol.core.animation import (
    Keys,
    blend,
    choose_recipe,
    frames_for,
    turn,
)

# A view looking down -z at the origin, 50 Å away, tilted 30° about x.
VIEW = turn((1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -50, 0, 0, 0, 40, 60, 0), 30, "x")
FOCUS = turn(VIEW[:11] + (-20, 5, 5, 5, 10, 30, 0), 70)


def close(a, b, tolerance=1e-6):
    return all(math.isclose(x, y, abs_tol=tolerance) for x, y in zip(a, b, strict=True))


def test_full_turn_returns_to_the_start_and_keeps_distance():
    assert close(turn(VIEW, 360), VIEW)
    assert close(turn(VIEW, 90)[9:], VIEW[9:])


def test_blend_follows_the_shortest_rotation_between_its_ends():
    assert close(blend(VIEW, FOCUS, 0), VIEW)
    assert close(blend(VIEW, FOCUS, 1), FOCUS)
    assert close(blend(VIEW, turn(VIEW, 40), 0.5), turn(VIEW, 20))
    assert close(blend(VIEW, turn(VIEW, -100), 0.5), turn(VIEW, -50))


@pytest.mark.parametrize("axis", [(1, -1, 0), (0, 1, -1), (-1, 0, 1), (1, -2, 3)])
@pytest.mark.parametrize("angle", [180, 179.999999, 180.000001])
def test_blend_preserves_mixed_axis_half_turns(axis, angle):
    # Independently construct the rotation with Rodrigues' formula.
    norm = math.sqrt(sum(v * v for v in axis))
    x, y, z = (v / norm for v in axis)
    cross = ((0, -z, y), (z, 0, -x), (-y, x, 0))
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    axis = (x, y, z)
    matrix = [[c * (r == col) + (1 - c) * axis[r] * axis[col] + s * cross[r][col]
               for col in range(3)] for r in range(3)]
    view = tuple(matrix[r][col] for col in range(3) for r in range(3)) + VIEW[9:]
    assert close(blend(view, view, .5), view)
    assert close(blend(view, FOCUS, 0), view)
    assert close(blend(FOCUS, view, 1), view)


def test_blend_exact_symmetric_half_turn():
    view = (0., -1., 0., -1., 0., 0., 0., 0., -1.) + VIEW[9:]
    assert close(blend(view, view, 0), view)
    assert close(blend(view, view, .5), view)


def test_turntable_loops_without_a_repeated_frame():
    frames = frames_for("turntable", Keys(overview=VIEW), fps=24)
    assert len(frames) == 240
    assert close(frames[0].view, VIEW)
    # The next frame after the last would be the first: one step, no seam.
    assert close(turn(frames[-1].view, 360 / 240), VIEW)


def test_cutaway_starts_buried_then_fades_the_lid_at_the_focus():
    frames = frames_for("cutaway", Keys(overview=VIEW, focus=FOCUS), fps=10)
    assert frames[0].lid_opacity == 1 and close(frames[0].view, VIEW)
    fading = [f for f in frames if 0 < f.lid_opacity < 1]
    assert fading and all(close(f.view, FOCUS) for f in fading)
    assert frames[-1].lid_opacity == 0
    opacity = [f.lid_opacity for f in frames]
    assert opacity == sorted(opacity, reverse=True)  # never flickers back to opaque


def test_reveal_eases_into_the_focus_before_rocking():
    frames = frames_for("reveal", Keys(overview=VIEW, focus=FOCUS), fps=10)
    assert close(frames[0].view, VIEW)
    assert any(close(f.view, FOCUS) for f in frames)
    # Eased: the first step of the move is far smaller than the middle one.
    move = [f.view for f in frames[15:45]]
    first = sum(abs(x - y) for x, y in zip(move[0], move[1], strict=True))
    middle = sum(abs(x - y) for x, y in zip(move[14], move[15], strict=True))
    assert first < middle / 10


def test_states_visits_every_state_in_order():
    frames = frames_for("states", Keys(overview=VIEW, states=10), fps=24)
    states = [f.state for f in frames]
    assert states == sorted(states)
    assert set(states) == set(range(1, 11))


def test_states_at_one_state_per_frame_never_repeat_or_skip():
    frames = frames_for('states', Keys(overview=VIEW, states=360), fps=24)
    assert [frame.state for frame in frames] == list(range(1, 361))


def test_scenes_hold_on_each_saved_view():
    scenes = (VIEW, FOCUS, turn(VIEW, 90))
    frames = frames_for("scenes", Keys(overview=VIEW, scenes=scenes), fps=10)
    for scene in scenes:
        assert sum(close(f.view, scene) for f in frames) >= 10


@pytest.mark.parametrize(
    ("recipe", "keys", "message"),
    [
        ("reveal", Keys(overview=VIEW), "focus"),
        ("cutaway", Keys(overview=VIEW), "focus"),
        ("states", Keys(overview=VIEW), "several states"),
        ("scenes", Keys(overview=VIEW, scenes=(VIEW,)), "two saved scenes"),
        ("spiral", Keys(overview=VIEW), "Unknown recipe"),
    ],
)
def test_recipes_explain_missing_content(recipe, keys, message):
    with pytest.raises(ValueError, match=message):
        frames_for(recipe, keys)


@pytest.mark.parametrize(
    ("scenes", "states", "has_focus", "recipe"),
    [
        (3, 10, True, "scenes"),
        (1, 10, True, "states"),
        (0, 1, True, "reveal"),
        (0, 1, False, "turntable"),
    ],
)
def test_default_recipe_prefers_the_most_specific_content(
    scenes, states, has_focus, recipe
):
    assert choose_recipe(scenes=scenes, states=states, has_focus=has_focus) == recipe
