"""`co-pymol animate --blender`: recipes become one backend animation job."""

from __future__ import annotations

import json

import pytest

from co_pymol import cli
from test_blender import bridge, fake_repo, preset_backend  # noqa: F401 (fixtures)


def saved_session(path, *, scenes=0, states=1):
    pymol2 = pytest.importorskip("pymol2")
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        cmd.fragment("trp", "one")
        for state in range(2, states + 1):
            cmd.create("one", "one", 1, state)
            cmd.translate([0, 0.5 * state, 0], "one", state=state)
        cmd.viewport(1200, 500)
        cmd.orient()
        for number in range(scenes):
            cmd.turn("y", 40)
            cmd.scene(f"F{number + 1}", "store")
        cmd.save(str(path))
    return path


@pytest.fixture
def submitted(preset_backend, tmp_path, monkeypatch):  # noqa: F811
    """Run animate --blender and return what reached the backend request."""
    from co_pymol.utils.pymol import blender as pymol_blender

    views = []
    captured = []

    def capture(cmd, selection, state, *, all_states=False):
        views.append(list(cmd.get_view()))
        captured.append({"all_states": all_states, "state": cmd.get("state")})
        return {
            "cif_text": "from pse",
            "view": {"viewport_px": list(cmd.get_viewport())},
            "provenance": {"enabled_objects": ["one"]},
        }

    monkeypatch.setattr(pymol_blender, "capture_scene", capture)

    def run(session, recipe, capsys, *extra):
        code = cli.main(
            ["animate", str(session), "--recipe", recipe, "--blender", "--wait"]
            + ["--config", str(tmp_path / "blender.json"), "--name", recipe]
            + list(extra)
        )
        receipt = json.loads(capsys.readouterr().out)
        request = json.loads(
            (tmp_path / "renders" / recipe / "request.json").read_text()
        )
        return code, receipt, request, views

    run.captured = captured
    return run


def test_turntable_uses_the_shared_camera_path(submitted, tmp_path, capsys):
    session = saved_session(tmp_path / "one.pse")
    code, receipt, request, _ = submitted(session, "turntable", capsys)
    assert code == 0 and receipt["status"] == "completed"
    options = request["options"]
    animation = options["overrides"]["output"]["animation"]
    assert animation["mode"] == "path" and animation["fps"] == 24
    assert len(animation["path"]) == receipt["frames"] == 240
    # The saved request keeps the configured lab backend and GPU requirement.
    assert request["config"]["backend"] == "cluster"
    assert request["config"]["require_gpu"] is True
    assert receipt["gpu"]["device"] == "GPU"


@pytest.mark.parametrize("backend, expected", [("local", "local"), ("lab", "cluster")])
def test_animation_backend_override_reaches_worker(submitted, tmp_path, capsys, backend, expected):
    session = saved_session(tmp_path / "one.pse")
    code, receipt, request, _ = submitted(session, "turntable", capsys, "--backend", backend)
    assert code == 0 and receipt["status"] == "completed"
    assert request["config"]["backend"] == expected


def test_scenes_send_one_view_per_frame_starting_at_the_capture(
    submitted, tmp_path, capsys
):
    session = saved_session(tmp_path / "tour.pse", scenes=3)
    code, receipt, request, views = submitted(session, "scenes", capsys)
    # Hold 1.5 s, then (move 2.5 s + hold 1.5 s) per further scene: 9.5 s.
    assert code == 0 and receipt["frames"] == 228
    output = request["options"]["overrides"]["output"]
    animation = output["animation"]
    assert animation["mode"] == "path" and animation["fps"] == 24
    # The frame budget is checked against this render's size, not the preset's.
    assert (output["width"], output["height"]) == (640, 267)
    assert len(animation["path"]) == 228
    assert all(len(view) == 18 for view in animation["path"])
    # Blender's first frame is the captured camera, so the path starts there.
    assert animation["path"][0] == pytest.approx(views[-1], abs=1e-4)


def test_cutaway_fades_the_presets_lid_from_the_close_up(submitted, tmp_path, capsys):
    session = saved_session(tmp_path / "one.pse")
    # The fake backend's catalog has no cutaway preset, so name one it has.
    focus = ["--focus", "name CA", "--preset", "mint"]
    code, _, request, views = submitted(session, "cutaway", capsys, *focus)
    assert code == 0
    animation = request["options"]["overrides"]["output"]["animation"]
    lid, anchor = animation["lid"], animation["anchor"]
    assert len(lid) == len(animation["path"])
    assert lid[0] == 1 and lid[-1] == 0 and lid == sorted(lid, reverse=True)
    # The anchor is the first frame with the cut complete, and the scene is
    # captured there so the camera-facing cut is aimed from the close-up.
    assert lid[anchor] == 0 and lid[anchor - 1] > 0
    assert animation["path"][anchor] == pytest.approx(views[-1], abs=1e-4)
    # The preset's own colours, not the session's.
    assert request["options"]["color_follow"] is False


def test_states_send_each_frames_state_and_export_all_states(
    submitted, tmp_path, capsys
):
    session = saved_session(tmp_path / "ensemble.pse", states=6)
    code, receipt, request, _ = submitted(session, "states", capsys)
    assert code == 0
    animation = request["options"]["overrides"]["output"]["animation"]
    assert len(animation["states"]) == len(animation["path"]) == receipt["frames"]
    assert animation["states"][0] == 1 and set(animation["states"]) == set(range(1, 7))
    # Exported at the first frame's state, with every state's coordinates.
    assert submitted.captured[-1] == {"all_states": True, "state": "1"}


@pytest.mark.parametrize('recipe', ['reveal', 'cutaway', 'states', 'rock'])
def test_real_session_recipe_views_are_json_serializable(tmp_path, recipe):
    """PyMOL coordinates are float32; measured clip planes must not leak it."""
    from co_pymol.core.animation import frames_for
    from co_pymol.utils.pymol.animate import keys_for
    from co_pymol.utils.pymol.blender import open_session

    session = saved_session(tmp_path / 'motion.pse', states=3)
    with open_session(session) as cmd:
        frames = frames_for(recipe, keys_for(cmd, recipe, 'name CA'), fps=24)
    document = {'path': [list(frame.view) for frame in frames],
                'states': [frame.state for frame in frames]}
    # Exercise the same strict JSON boundary used before launching a worker.
    assert json.loads(json.dumps(document, allow_nan=False)) == document
