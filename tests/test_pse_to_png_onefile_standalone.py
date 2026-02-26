from __future__ import annotations

from pathlib import Path

import pse_to_png_onefile_standalone as standalone


def test_build_runner_command_uses_style_json(tmp_path: Path):
    payload_path = tmp_path / "payload.json"
    out_png = tmp_path / "out.png"
    style_json = tmp_path / "style.json"

    command = standalone._build_runner_command(
        payload_path=payload_path,
        out_png=out_png,
        blender_exe="/usr/bin/blender",
        preset="preset_1_interface",
        style_json=str(style_json),
        rough=False,
        color_follow=True,
        save_blend=False,
        blend_output="",
        use_apptainer=False,
        apptainer_image="",
    )

    assert "--style-json" in command
    assert str(style_json.resolve()) in command
    assert "--preset" not in command
    assert "--color-follow" in command
    assert command[command.index("--color-follow") + 1] == "1"


def test_build_runner_command_uses_preset_when_style_missing(tmp_path: Path):
    payload_path = tmp_path / "payload.json"
    out_png = tmp_path / "custom_name.png"

    command = standalone._build_runner_command(
        payload_path=payload_path,
        out_png=out_png,
        blender_exe="blender",
        preset="preset_1_interface",
        style_json="",
        rough=True,
        color_follow=False,
        save_blend=True,
        blend_output="scene.blend",
        use_apptainer=True,
        apptainer_image="/tmp/bridge.sif",
    )

    assert "--preset" in command
    assert command[command.index("--preset") + 1] == "preset_1_interface"
    assert "--style-json" not in command
    assert "--output-dir" in command
    assert command[command.index("--output-dir") + 1] == str(tmp_path)
    assert "--output-name" in command
    assert command[command.index("--output-name") + 1] == "custom_name.png"
    assert "--use-apptainer" in command
    assert "--apptainer-image" in command
