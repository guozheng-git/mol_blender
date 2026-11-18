import copy
import json

import pytest

from mol_blender import authoring, core
from mol_blender.presets import PRESET_STYLE_FILENAMES, preset_path
from mol_blender.spec import resolve_options, style_digest


def authored_style():
    return json.loads(preset_path("mint_sky_assembly").read_text())


@pytest.mark.parametrize("preset", PRESET_STYLE_FILENAMES)
def test_compact_json_and_named_preset_resolve_to_same_snapshot(preset):
    authored = json.loads(preset_path(preset).read_text())
    original = copy.deepcopy(authored)
    named = resolve_options({"preset": preset, "preserve_view": False}, {})
    custom = resolve_options({"style_doc": authored}, {})
    assert named["style_doc"] == custom["style_doc"]
    assert named["render_spec"]["sha256"] == custom["render_spec"]["sha256"]
    assert authored == original
    assert core.load_and_validate_style_json(preset_path(preset))["renderer"] == custom["style_doc"]["renderer"]


def test_legacy_omissions_keep_existing_execution_and_color_management_behavior():
    style = authored_style()
    style["renderer"] = {"engine": "CYCLES"}
    resolved = resolve_options({"style_doc": style}, {})["style_doc"]["renderer"]
    assert resolved == {
        "engine": "CYCLES", "device": "GPU", "compute_device_type": "AUTO",
        "transparent_background": False, "adaptive_sampling": True,
        "adaptive_threshold": 0.01, "denoise": False, "seed": 0,
    }
    # Color management still comes from Blender when custom JSON omits it.
    assert not {"view_transform", "look", "exposure", "gamma", "require_gpu"} & resolved.keys()
    assert style["renderer"] == {"engine": "CYCLES"}


def test_explicit_defaults_and_omissions_have_identical_resolved_hashes():
    compact = authored_style()
    explicit = copy.deepcopy(compact)
    explicit["renderer"].update(device="GPU", compute_device_type="AUTO",
                                transparent_background=False, adaptive_sampling=True,
                                adaptive_threshold=0.01)
    a = resolve_options({"style_doc": compact}, {})
    b = resolve_options({"style_doc": explicit}, {})
    assert a["style_doc"] == b["style_doc"]
    assert a["render_spec"]["sha256"] == b["render_spec"]["sha256"]


def test_explicit_overrides_and_convenience_options_beat_defaults():
    overrides = {"device": "CPU", "compute_device_type": "CUDA", "denoise": False,
                 "seed": 7, "adaptive_sampling": False, "adaptive_threshold": 0.008,
                 "transparent_background": True, "require_gpu": False}
    result = resolve_options({"preset": "mint_sky_assembly", "overrides": {"renderer": overrides}}, {})
    assert overrides.items() <= result["style_doc"]["renderer"].items()
    result = resolve_options({"preset": "mint_sky_assembly", "overrides": {"renderer": overrides},
                              "transparent_background": False}, {})
    assert result["style_doc"]["renderer"]["transparent_background"] is False


@pytest.mark.parametrize("renderer", [
    {"engine": "CYCLES", "devcie": "GPU"},
    {"engine": "CYCLES", "device": None},
    {"engine": "CYCLES", "adaptive_sampling": "yes"},
    {},
])
def test_invalid_authored_settings_cannot_be_hidden_by_defaults(renderer):
    style = authored_style()
    style["renderer"] = renderer
    with pytest.raises(core.StyleValidationError):
        core.resolve_style_document(style)
    with pytest.raises(core.StyleValidationError):
        resolve_options({"style_doc": style, "overrides": {"renderer": {"device": "GPU"}}}, {})


def test_saved_presets_materialize_defaults_without_mutating_authored_json(tmp_path):
    style = authored_style()
    original = copy.deepcopy(style)
    saved = authoring.save_preset("user/compact", style, tmp_path, description="compact style")
    loaded = authoring.inspect_preset("user/compact", tmp_path)
    assert loaded["style_doc"] == core.resolve_style_document(style)
    assert loaded["sha256"] == style_digest(loaded["style_doc"])
    assert saved["sha256"] == loaded["sha256"]
    assert style == original
    loaded["style_doc"]["renderer"]["device"] = "CPU"
    assert core.resolve_style_document(style)["renderer"]["device"] == "GPU"


def test_existing_sparse_saved_presets_still_verify_and_resolve(tmp_path):
    style = authored_style()
    saved = authoring.save_preset("user/legacy", style, tmp_path, description="legacy sparse style")
    path = tmp_path / "user/legacy.json"
    envelope = json.loads(path.read_text())
    envelope["style_doc"] = style
    envelope["sha256"] = style_digest(style)
    path.write_text(json.dumps(envelope))
    assert authoring.inspect_preset("user/legacy", tmp_path)["sha256"] == envelope["sha256"]
    result = authoring.resolve_authored({"preset": "user/legacy"}, {}, tmp_path)
    assert result["style_doc"]["renderer"] == saved["style_doc"]["renderer"]
