from __future__ import annotations

from pathlib import Path

from blender_render.bridge_runner import (
    build_sbatch_script,
    extract_embedded_payload,
    extract_embedded_style,
)


def _payload() -> dict:
    return {
        "version": "1.0",
        "run_id": "run123",
        "cif_text": "data_demo\n#\n",
        "view": {
            "pymol_view": [
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
                -50.0,
                0.0,
                0.0,
                0.0,
                2.0,
                120.0,
                50.0,
            ],
            "fov_deg": 50.0,
            "orthoscopic": False,
        },
    }


def test_sbatch_flags_and_embedded_payload_roundtrip():
    payload = _payload()
    script = build_sbatch_script(
        run_id="run123",
        payload=payload,
        preset="preset_1_interface",
        style_doc=None,
        blender_exe="/Applications/Blender.app/Contents/MacOS/Blender",
        output_dir="/tmp/out",
        output_name="output.png",
        custom_script=Path("blender_render/blender_custom_render.py"),
        use_apptainer=False,
        apptainer_image="",
        rough=False,
        raw_view=False,
        color_follow=False,
        save_blend=False,
        blend_output="",
        slurm_partition="gpu",
        slurm_account="acct",
        slurm_gres="gpu:1",
        slurm_time="00:30:00",
    )

    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --account=acct" in script
    assert "#SBATCH --gres=gpu:1" in script
    assert "#SBATCH --time=00:30:00" in script

    decoded_payload = extract_embedded_payload(script)
    assert decoded_payload["cif_text"] == payload["cif_text"]
    assert decoded_payload["view"]["pymol_view"] == payload["view"]["pymol_view"]


def test_sbatch_embeds_style_when_custom_mode():
    payload = _payload()
    style_doc = {
        "version": "1.0",
        "output": {"format": "PNG", "width": 100, "height": 100, "samples": 1},
        "renderer": {"engine": "CYCLES"},
        "camera": {},
        "world": {},
        "lights": [{"type": "AREA", "energy": 1.0, "location": [0, 0, 1]}],
        "materials": {"m": {"base_color": "#FFFFFF"}},
        "styles": [{"name": "s", "style": "surface", "selection": {"polymer": True}, "material": "m"}],
    }

    script = build_sbatch_script(
        run_id="run123",
        payload=payload,
        preset=None,
        style_doc=style_doc,
        blender_exe="blender",
        output_dir="/tmp/out",
        output_name="output.png",
        custom_script=Path("blender_render/blender_custom_render.py"),
        use_apptainer=False,
        apptainer_image="",
        rough=False,
        raw_view=False,
        color_follow=False,
        save_blend=False,
        blend_output="",
        slurm_partition="",
        slurm_account="",
        slurm_gres="gpu:1",
        slurm_time="00:30:00",
    )

    decoded_style = extract_embedded_style(script)
    assert decoded_style == style_doc


def test_sbatch_includes_rough_flag_for_local_replay():
    payload = _payload()
    script = build_sbatch_script(
        run_id="run123",
        payload=payload,
        preset="preset_1_interface",
        style_doc=None,
        blender_exe="blender",
        output_dir="/tmp/out",
        output_name="output.png",
        custom_script=Path("blender_render/blender_custom_render.py"),
        use_apptainer=False,
        apptainer_image="",
        rough=True,
        raw_view=True,
        color_follow=False,
        save_blend=False,
        blend_output="",
        slurm_partition="",
        slurm_account="",
        slurm_gres="gpu:1",
        slurm_time="00:30:00",
    )

    assert "  --rough 1" in script
    assert "  --raw-view 1" in script
