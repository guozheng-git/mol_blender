from __future__ import annotations

import json
from pathlib import Path

import pytest

from blender_render.bridge_runner import (
    CAMERA_KEYS,
    COLOR_FOLLOW_KEYS,
    LIGHT_KEYS,
    MATERIAL_KEYS,
    OUTPUT_KEYS,
    RENDERER_KEYS,
    REQUIRED_STYLE_TOP_KEYS,
    STYLE_KEYS,
    StyleValidationError,
    STYLE_TOP_LEVEL_OPTIONAL_KEYS,
    WORLD_KEYS,
    validate_style_document,
)


def _example_style() -> dict:
    path = Path("blender_render/example_full_custom.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _preset_style() -> dict:
    path = Path("blender_render/style_preset_1_opaque_pastel.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_valid_full_custom_style_passes_validation():
    style = _example_style()
    validated = validate_style_document(style)
    assert validated["version"] == "1.0"
    assert validated["styles"]


def test_portable_preset_style_passes_validation():
    style = _preset_style()
    validated = validate_style_document(style)
    assert validated["output"]["filename"] == "01_opaque_pastel_ses_interface.png"
    assert any(entry.get("name") == "chain_a_cartoon" for entry in validated["styles"])


def test_ptr_style_uses_native_sticks_contract():
    style = _preset_style()
    ptr_styles = [entry for entry in style["styles"] if entry.get("name") == "ligand_ptr_sticks"]
    assert ptr_styles
    ptr_style = ptr_styles[0]
    assert ptr_style["style"] == "sticks"
    assert ptr_style["sticks_radius"] > 0
    assert "sticks_backend" not in ptr_style
    assert "sticks_fallback_radius_scale" not in ptr_style
    assert "sticks_fallback_atom_scale" not in ptr_style
    assert "sticks_fallback_bond_resolution" not in ptr_style

    validated = validate_style_document(style)
    validated_ptr = [entry for entry in validated["styles"] if entry.get("name") == "ligand_ptr_sticks"][0]
    assert validated_ptr["style"] == "sticks"
    assert validated_ptr["sticks_radius"] > 0


def test_preset_1_covers_polymer_nucleic_ligand_ion_water():
    style = _preset_style()
    style_names = {entry.get("name", "") for entry in style.get("styles", []) if isinstance(entry, dict)}
    assert "chain_a_cartoon" in style_names
    assert "nucleic_cartoon" in style_names
    assert "ligand_ptr_sticks" in style_names
    assert "ions_spheres" in style_names
    assert "water_spheres" in style_names


@pytest.mark.parametrize(
    "preset_path",
    sorted(Path("blender_render").glob("style_preset_*.json")),
)
def test_all_presets_use_supported_key_set_only(preset_path: Path):
    style = json.loads(preset_path.read_text(encoding="utf-8"))
    validate_style_document(style)

    allowed_top = set(REQUIRED_STYLE_TOP_KEYS) | set(STYLE_TOP_LEVEL_OPTIONAL_KEYS)
    assert set(style.keys()) <= allowed_top
    assert set(style["output"].keys()) <= set(OUTPUT_KEYS)
    assert set(style["renderer"].keys()) <= set(RENDERER_KEYS)
    assert set(style["camera"].keys()) <= set(CAMERA_KEYS)
    assert set(style["world"].keys()) <= set(WORLD_KEYS)
    if "color_follow" in style:
        assert set(style["color_follow"].keys()) <= set(COLOR_FOLLOW_KEYS)
    for light in style["lights"]:
        assert set(light.keys()) <= set(LIGHT_KEYS)
    for material in style["materials"].values():
        assert set(material.keys()) <= set(MATERIAL_KEYS)
    for style_entry in style["styles"]:
        assert set(style_entry.keys()) <= set(STYLE_KEYS)
        assert style_entry.get("style") != "ball_and_stick"
        assert "sticks_backend" not in style_entry
        assert "sticks_fallback_radius_scale" not in style_entry
        assert "sticks_fallback_atom_scale" not in style_entry
        assert "sticks_fallback_bond_resolution" not in style_entry


def test_missing_required_top_level_keys_fails_with_actionable_error():
    style = _example_style()
    del style["lights"]

    with pytest.raises(StyleValidationError) as exc:
        validate_style_document(style)

    message = str(exc.value)
    assert "Missing required top-level keys" in message
    assert "lights" in message


def test_invalid_selection_and_style_entries_fail_validation():
    style = _example_style()
    style["styles"][0]["selection"] = {"chain": 12}
    style["styles"][0]["material"] = "missing_material"
    style["styles"][0]["style"] = "not_supported"

    with pytest.raises(StyleValidationError) as exc:
        validate_style_document(style)

    message = str(exc.value)
    assert "styles[0].selection.chain" in message
    assert "styles[0].material" in message
    assert "styles[0].style" in message
