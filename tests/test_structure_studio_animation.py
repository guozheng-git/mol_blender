"""Recipe timelines: pure maths, no PyMOL."""

from __future__ import annotations

import math

import pytest
import json
from pathlib import Path
import sys
SKILL = Path(__file__).resolve().parents[1] / "plugins/structure-studio/skills/structure-studio"
sys.path.insert(0, str(SKILL / "scripts"))
import studio
from studio import animation_recipes

from co_pymol.core.animation import (
    Keys,
    blend,
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


@pytest.mark.parametrize('rotation', [
    (0, -1, 0, -1, 0, 0, 0, 0, -1),  # half-turn about (1, -1, 0)
    (-1, 0, 0, 0, 0, -1, 0, -1, 0),  # half-turn about (0, 1, -1)
    (-0.6, 0, -0.8, 0, -1, 0, -0.8, 0, 0.6),  # about (1, 0, -2)
])
@pytest.mark.parametrize('offset', [-0.0001, 0, 0.0001])
def test_half_turn_views_keep_their_orientation_through_scene_transitions(rotation, offset):
    view = turn(rotation + VIEW[9:], offset)
    assert close(blend(view, FOCUS, 0), view)
    assert close(blend(FOCUS, view, 1), view)
    frames = frames_for('scenes', Keys(overview=view, scenes=(view, view)), fps=4)
    assert all(close(frame.view, view) for frame in frames)


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


def test_bundled_templates_validate_and_cover_every_recipe():
    templates = list((SKILL / 'templates/animations').glob('*.json'))
    assert {p.stem for p in templates} == set(animation_recipes.RECIPES)
    for path in templates:
        config = animation_recipes.template(json.loads(path.read_text()))
        assert config['recipe'] == path.stem


@pytest.mark.parametrize('extra', [
    {'surprise': True}, {'fps': True}, {'fps': 0}, {'gif': 'yes'},
    {'duration': float('nan')}, {'duration': 16}, {'focus': 'name CA'},
    {'rock_angle': 46}, {'version': True},
])
def test_template_rejects_unknown_or_invalid_fields(extra):
    with pytest.raises(ValueError):
        animation_recipes.template({'version': 1, 'recipe': 'rock', **extra})


def test_template_overrides_are_explicit_and_old_orbits_stay_separate():
    args = studio.parser().parse_args(['render', 'x.pse', '--animation-template',
        str(SKILL / 'templates/animations/rock.json'), '--duration', '3', '--fps', '8', '--no-gif'])
    config = studio.animation_template(args)
    assert config == {'version': 1, 'recipe': 'rock', 'duration': 3, 'fps': 8, 'gif': False, 'rock_angle': 25}
    with pytest.raises(SystemExit):
        studio.parser().parse_args(['render', 'x.pse', '--recipe', 'rock', '--animation', '360'])
    legacy = studio.parser().parse_args(['render', 'x.pse', '--animation', 'rock'])
    assert studio.animation_template(legacy) is None


@pytest.mark.parametrize('recipe', animation_recipes.RECIPES)
def test_shortened_timelines_have_exact_frame_budget(recipe):
    keys = Keys(overview=VIEW, focus=FOCUS, scenes=(VIEW, FOCUS), states=3)
    frames = frames_for(recipe, keys, fps=7, duration=2)
    assert len(frames) == 14
    assert all(len(f.view) == 18 for f in frames)
    if recipe == 'cutaway':
        assert frames[0].lid_opacity == 1 and frames[-1].lid_opacity == 0


def test_movie_bundle_and_record_retain_playback_and_reusable_template(tmp_path):
    import artifact_files
    from PIL import Image
    output = tmp_path / 'movie.png'
    Image.new('RGB', (16, 12)).save(output)
    files = {'render.png': str(output)}
    for name in ('render.mp4', 'render.gif', 'frames.zip', 'animation.json'):
        path = tmp_path / name
        path.write_bytes(b'{"version":1,"recipe":"rock"}' if name.endswith('.json') else b'fixture')
        files[name] = str(path)
    args = studio.parser().parse_args(['render', 'x.cif', '--workspace', str(tmp_path)])
    animation = {'recipe': 'rock', 'frames': 8, 'fps': 4, 'duration': 2}
    result = {'outputs': files, 'animation': animation, 'preset': 'mint_sky_assembly'}
    result.update(studio._workspace_steps(args, result, output))
    record = artifact_files.record(result, {'image': 'image-id', 'session': ['bundle-id']})
    assert record['animation'] == animation
    assert {f['role'] for f in record['session']['files']} == {'mp4', 'gif', 'frames', 'animation'}
    restored = artifact_files.restore(result['session']['parts'], str(tmp_path / 'restored'))
    assert len(restored) == 4
    for item in restored:
        assert Path(item['path']).read_bytes() == (tmp_path / Path(item['path']).name).read_bytes()
