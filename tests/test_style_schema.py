from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymol_plugin_bridge.bridge_runner import StyleValidationError, validate_style_document


def _example_style() -> dict:
    path = Path("pymol_plugin_bridge/example_full_custom.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _preset_style() -> dict:
    path = Path("pymol_plugin_bridge/style_preset_1_reference_exact.json")
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


def test_ptr_sticks_backend_can_use_legacy_ball_and_stick():
    style = _preset_style()
    ptr_styles = [entry for entry in style["styles"] if entry.get("name") == "ligand_ptr_sticks"]
    assert ptr_styles
    ptr_style = ptr_styles[0]
    assert ptr_style.get("sticks_backend") == "legacy_ball_and_stick"
    assert ptr_style.get("sticks_fallback_radius_scale") == 0.6
    assert ptr_style.get("sticks_fallback_atom_scale") == 0.0
    assert ptr_style.get("sticks_fallback_bond_resolution") == 24

    validated = validate_style_document(style)
    validated_ptr = [entry for entry in validated["styles"] if entry.get("name") == "ligand_ptr_sticks"][0]
    assert validated_ptr["sticks_backend"] == "legacy_ball_and_stick"
    assert validated_ptr["sticks_fallback_radius_scale"] == 0.6
    assert validated_ptr["sticks_fallback_atom_scale"] == 0.0
    assert validated_ptr["sticks_fallback_bond_resolution"] == 24


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
