from __future__ import annotations

from blender_render.chain_palette import (
    expand_polymer_styles_for_multichain_palette,
    ordered_chain_ids_for_palette,
)


def test_ordered_chain_ids_prioritizes_a_then_b():
    ordered = ordered_chain_ids_for_palette(["C", "A", "B", "D", "C", ""])
    assert ordered == ["A", "B", "C", "D"]


def test_expand_polymer_styles_splits_generic_polymer_style_per_chain():
    style_doc = {
        "styles": [
            {
                "name": "polymer_cartoon",
                "style": "cartoon",
                "material": "polymer_main",
                "selection": {"query": "polymer and not nucleic"},
            },
            {
                "name": "ligand_sticks",
                "style": "sticks",
                "material": "ligand_main",
                "selection": {"query": "ligand"},
            },
        ]
    }
    materials = {
        "polymer_main": {"base_color": "#4EAAD9", "roughness": 0.6},
        "ligand_main": {"base_color": "#F97BB4", "roughness": 0.4},
    }

    styles, runtime_materials, chain_colors = expand_polymer_styles_for_multichain_palette(
        style_doc=style_doc,
        materials_cfg=materials,
        protein_chain_ids=["C", "A", "B", "D"],
    )

    polymer_styles = [entry for entry in styles if entry["name"].startswith("polymer_cartoon__chain_")]
    assert [entry["selection"]["chain"] for entry in polymer_styles] == ["A", "B", "C", "D"]
    assert len(set(chain_colors.keys())) == 4
    assert list(chain_colors.keys()) == ["A", "B", "C", "D"]
    polymer_base_colors = [runtime_materials[entry["material"]]["base_color"] for entry in polymer_styles]
    assert len(set(polymer_base_colors)) == 4
    ligand_styles = [entry for entry in styles if entry["name"] == "ligand_sticks"]
    assert len(ligand_styles) == 1


def test_expand_respects_explicit_chain_styles_and_splits_polymer_other():
    style_doc = {
        "styles": [
            {
                "name": "chain_a_cartoon",
                "style": "cartoon",
                "material": "inner_chain_a",
                "selection": {"query": "polymer and not nucleic and chain A"},
            },
            {
                "name": "chain_b_cartoon",
                "style": "cartoon",
                "material": "inner_chain_b",
                "selection": {"query": "polymer and not nucleic and chain B"},
            },
            {
                "name": "polymer_other_cartoon",
                "style": "cartoon",
                "material": "inner_chain_a",
                "selection": {"query": "polymer and not nucleic and not chain A and not chain B"},
            },
        ]
    }
    materials = {
        "inner_chain_a": {"base_color": "#4EAAD9", "roughness": 0.6},
        "inner_chain_b": {"base_color": "#66DAB6", "roughness": 0.6},
    }

    styles, runtime_materials, chain_colors = expand_polymer_styles_for_multichain_palette(
        style_doc=style_doc,
        materials_cfg=materials,
        protein_chain_ids=["A", "B", "C"],
    )

    assert any(entry["name"] == "chain_a_cartoon" for entry in styles)
    assert any(entry["name"] == "chain_b_cartoon" for entry in styles)
    polymer_other = [entry for entry in styles if entry["name"] == "polymer_other_cartoon__chain_C"]
    assert len(polymer_other) == 1
    assert polymer_other[0]["selection"]["chain"] == "C"
    assert chain_colors["A"] == "#4EAAD9"
    assert chain_colors["B"] == "#66DAB6"
    c_material_name = polymer_other[0]["material"]
    assert c_material_name in runtime_materials
    assert runtime_materials[c_material_name]["base_color"] == chain_colors["C"]
