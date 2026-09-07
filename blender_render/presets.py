"""Small built-in catalog; IDs and filenames retain the existing public contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path

# Existing preset identifiers stay stable; reference styles use distinct identifiers.
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
    ('powder_blue_rounded', 'style_preset_powder_blue_rounded.json', 'reference_01.png',
     'Powder blue rounded protein with pink nucleic acid on black.', ('reference_01',), 'clay'),
    ('cyan_mint_cutaway', 'style_preset_cyan_mint_cutaway.json', 'reference_02.png',
     'Cyan and mint surfaces with clean pocket cuts and intact interior ribbons.', ('reference_02',), 'clay'),
    ('pastel_duplex_rounded', 'style_preset_pastel_duplex_rounded.json', 'reference_03.png',
     'Rounded green and blue proteins with pink DNA on black.', ('reference_03',), 'clay'),
    ('pastel_duplex_cartoon', 'style_preset_pastel_duplex_cartoon.json', 'reference_04.png',
     'Thin blue and green secondary-structure ribbons with pink DNA.', ('reference_04',), 'clay'),
    ('vivid_binding_site', 'style_preset_vivid_binding_site.json', 'reference_05.png',
     'Macro ligand view with vivid element colors and teal cartoon context.', ('reference_05',), 'clay'),
    ('ivory_lime_frosted', 'style_preset_ivory_lime_frosted.json', 'reference_06.png',
     'Ivory frosted grid shell, lime core and cyan nucleic acids.', ('reference_06',), 'frosted'),
    ('mint_sky_assembly', 'style_preset_mint_sky_assembly.json', 'reference_07.png',
     'Mint and sky protein assembly with purple and pink nucleic acids.', ('reference_07',), 'clay'),
    ('neon_duplex_hero', 'style_preset_neon_duplex_hero.json', 'reference_08.png',
     'Green protein and gradient nucleic acids against a pink-cyan background.', ('reference_08',), 'clay'),
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
    for material in style["materials"].values():
        for field, capability in (("frosted", "material.frosted_grid"), ("color_gradient", "material.color_gradient"), ("color_attribute", "material.attribute_color"), ("glow", "material.fog_glow"), ("edge_highlight", "material.edge_highlight")):
            if field in material:
                required.add(capability)
    if "element_palette" in style.get("geometry", {}):
        required.add("geometry.element_palette")
    world = style.get("world", {})
    if "gradient_direction" in world:
        required.add("world.directional_gradient")
    if "ambient_color" in world or "ambient_strength" in world:
        required.add("world.separate_illumination")
    camera = style.get("camera", {})
    if any(key in camera for key in ("fit", "roll_deg", "ortho_scale_angstrom", "projection")):
        required.add("camera.reference_framing")
    if any("surface_scale_radius" in entry or "surface_relaxation_steps" in entry for entry in style["styles"]):
        required.add("representation.surface_smoothing")
    if any("wireframe_width_angstrom" in entry for entry in style["styles"]):
        required.add("representation.wire_mesh")
    if any(entry.get("color_by_chain") is False for entry in style["styles"]):
        required.add("material.uniform_polymer_color")
    if any("nucleic_base_scale" in entry for entry in style["styles"]):
        required.add("representation.nucleic_base_thickness")
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
