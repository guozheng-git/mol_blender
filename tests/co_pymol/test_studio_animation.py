"""Real PyMOL inputs through both clients, with only render submission replaced."""
import json
import os
from pathlib import Path

import pytest

from co_pymol import cli
from co_pymol.core.animation import RECIPES, Frame
from co_pymol.defaults import ANIMATION
from co_pymol.utils.pymol.blender import capture_animation, open_session

SCRIPTS = Path(os.environ.get(
    "STRUCTURE_STUDIO_TEST_SCRIPTS",
    Path(__file__).resolve().parents[2] / "plugins/structure-studio/skills/structure-studio/scripts",
))


@pytest.fixture
def session(tmp_path):
    pymol2 = pytest.importorskip("pymol2")
    path = tmp_path / "scene.pse"
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        cmd.fragment("trp", "one")
        for state in (2, 3):
            cmd.create("one", "one", 1, state)
            cmd.translate([0, state, 0], "one", state=state)
        cmd.fragment("gly", "hidden")
        cmd.color("red", "one")
        cmd.orient()
        cmd.scene("first", "store")
        cmd.turn("y", 40)
        cmd.color("blue", "one")
        cmd.scene("second", "store")
        cmd.disable("hidden")
        cmd.color("green", "one")
        cmd.alter("one", "ss='H'")
        cmd.select("chosen", "one and name CA")
        cmd.set("state", 3)
        cmd.set("state", 2, "one")
        cmd.viewport(640, 480)
        cmd.save(str(path))
    return path


def session_state(cmd):
    atoms = []
    cmd.iterate("all", "atoms.append((model,index,color,ss))", space={"atoms": atoms})
    return {
        "view": cmd.get_view(), "state": cmd.get("state"),
        "objects": cmd.get_names("objects", enabled_only=1),
        "object_states": {name: cmd.get("state", name) for name in cmd.get_object_list("all")},
        "object_settings": {name: cmd.get_object_settings(name) or [] for name in cmd.get_object_list("all")},
        "matrices": {name: cmd.get_object_matrix(name) for name in cmd.get_object_list("all")},
        "selected": cmd.count_atoms("chosen"), "atoms": atoms,
    }


@pytest.mark.skipif(not SCRIPTS.is_dir(), reason="Structure Studio scripts are required for client parity")
@pytest.mark.parametrize("recipe", RECIPES)
@pytest.mark.parametrize("timing", [[], ["--fps", "7", "--duration", "2"]])
def test_clients_prepare_the_same_scene_and_animation(session, recipe, timing, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    from animation_session import prepare
    from blender_render.animation import normalize

    submitted = {}

    class Bridge:
        def select_presets(self, presets, all_presets):
            return presets

        def submit(self, payload, **options):
            submitted.update(payload=payload, **options)
            return {"status": "starting", "request_id": "captured"}

        def presentation(self, receipt):
            return receipt

    monkeypatch.setattr(cli, "blender_bridge", lambda args: Bridge())
    focus = ["--focus", "one and name CA"] if recipe in {"reveal", "cutaway"} else []
    argv = ["animate", str(session), "--recipe", recipe, "--blender", "--gif",
            "--width", "128", "--height", "96", *focus, *timing]
    if recipe == "rock":
        argv += ["--rock-angle", "17"]
    assert cli.main(argv) == 0
    capsys.readouterr()
    args = cli.build_parser().parse_args(argv)
    config = {"version": 1, "recipe": recipe, "fps": args.fps, "gif": args.gif}
    for key in ("focus", "duration", "rock_angle"):
        if getattr(args, key) is not None:
            config[key] = getattr(args, key)
    with open_session(session) as cmd:
        before = session_state(cmd)
        payload, overrides, report = prepare(cmd, config, width=128, height=96)
        assert session_state(cmd) == before
    for field in ("generated_at_utc", "run_id"):
        payload.pop(field)
        submitted["payload"].pop(field)
    assert submitted["payload"] == payload
    assert normalize(submitted["animation"], {"width": 128, "height": 96}) == overrides["output"]["animation"]
    assert report["frames"] == len(submitted["animation"]["path"])
    json.dumps({"payload": payload, "animation": submitted["animation"]}, allow_nan=False)


@pytest.mark.parametrize("pinned", [True, False])
def test_failed_animation_capture_restores_pinned_states_and_camera(session, monkeypatch, pinned):
    from co_pymol.utils.pymol import blender
    from co_pymol.core.animation import frames_for
    from co_pymol.utils.pymol.animate import keys_for

    def fail(*args, **kwargs):
        raise RuntimeError("export failed")

    with open_session(session) as cmd:
        if not pinned:
            cmd.unset("state", "one")
        before = session_state(cmd)
        frames = frames_for("states", keys_for(cmd, "states", None))
        monkeypatch.setattr(blender, "capture_scene", fail)
        with pytest.raises(RuntimeError, match="export failed"):
            capture_animation(cmd, "states", frames, 24)
        assert session_state(cmd) == before


def test_preview_maps_shared_opacity_to_ghost_shell_and_applies_object_states(session, monkeypatch, tmp_path):
    from co_pymol.utils.pymol import animate

    monkeypatch.setattr(animate.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(animate.subprocess, "run", lambda *args, **kwargs: None)
    observed = []
    with open_session(session) as cmd:
        hidden_state = cmd.get("state", "hidden")
        monkeypatch.setattr(cmd, "png", lambda *args, **kwargs: observed.append(
            (cmd.get_object_state("one"), float(cmd.get("transparency", "one")))))
        frames = [Frame(tuple(cmd.get_view()), state=1, lid_opacity=1),
                  Frame(tuple(cmd.get_view()), state=3, lid_opacity=0)]
        animate.render_preview(cmd, frames, tmp_path / "preview.mp4", fps=2, width=128, height=96, shell="one")
        assert cmd.get("state", "hidden") == hidden_state
    assert observed[0] == (1, 0)
    assert observed[1][0] == 3
    assert observed[1][1] == pytest.approx(ANIMATION["recipes"]["cutaway"]["pymol_shell_transparency"])
