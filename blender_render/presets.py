"""Small built-in catalog; IDs and filenames retain the existing public contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path

# No new visual presets. User/project persistence belongs to the developer loop.
_PRESETS = (
    ("preset_1_interface", "style_preset_1_opaque_pastel.json", "01_opaque_pastel_ses_interface.png",
     "Soft opaque pastel cartoons with native ligand sticks.", ("pastel",), "clay"),
    ("preset_2_frosted_blue", "style_preset_2_frosted_blue.json", "02_saturated_blue_core_frosted_shell.png",
     "Saturated blue cartoon core with a frosted surface shell.", ("frosted_blue",), "frosted"),
    ("preset_3_black_tubes", "style_preset_3_black_tubes.json", "03_pastel_tube_cartoon_black.png",
     "Pastel ribbons and native sticks on black.", ("black_tubes",), "clay"),
    ("preset_4_milky_green", "style_preset_4_milky_green.json", "04_milky_white_green_shell_cyan_dna.png",
     "Milky green surface, protein ribbons and cyan nucleic acids.", ("milky_green",), "milky"),
    ("preset_5_black_interface", "style_preset_5_black_interface.json", "05_black_background_interface.png",
     "Cartoon and ligand interface on a black background.", ("black_interface",), "clay"),
    ("dried_paint_green", "style_preset_dried_paint_green.json", "dried_paint_green.png",
     "Textured matte green paint with rounded ribbon edges.", ("green_paint", "dried-paint-green"), "dried_paint"),
)
DEFAULT_PRESET = "preset_1_interface"
PRESET_STYLE_FILENAMES = {row[0]: row[1] for row in _PRESETS}
PRESET_OUTPUT_FILENAMES = {row[0]: row[2] for row in _PRESETS}
_ALIASES = {alias: row[0] for row in _PRESETS for alias in (row[0], "builtin/" + row[0], *row[4])}


def preset_id(name: str) -> str:
    if not isinstance(name, str) or name not in _ALIASES:
        raise ValueError(f"Unknown preset: {name!r}. Expected one of: {', '.join(PRESET_STYLE_FILENAMES)}")
    return _ALIASES[name]


def preset_path(name: str) -> Path:
    return Path(__file__).with_name(PRESET_STYLE_FILENAMES[preset_id(name)])


def required_capabilities(style: dict) -> list[str]:
    """Requirements of this JSON, not a claim about a running worker's runtime."""
    required = {"representation." + entry["style"] for entry in style["styles"]}
    required.update("material.dried_paint" if "paint" in material else "material.clay" for material in style["materials"].values())
    if style.get("geometry", {}).get("edge_rounding", {}).get("radius_angstrom", 0) > 0:
        required.add("geometry.edge_rounding")
    return sorted(required)


def catalog() -> list[dict]:
    entries = []
    for identifier, filename, _, description, aliases, family in _PRESETS:
        style = json.loads(preset_path(identifier).read_text())
        entries.append({
            "id": identifier, "qualified_id": "builtin/" + identifier, "revision": "1",
            "source": "builtin", "source_json": filename, "aliases": list(aliases),
            "description": description, "material_family": family,
            "representations": sorted({entry["style"] for entry in style["styles"]}),
            "schema": "style.schema.json", "schema_version": style["version"],
            "required_capabilities": required_capabilities(style),
            "defaults": {"preserve_view": True, "save_blend": False},
        })
    return copy.deepcopy(entries)
