from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import numpy as np


def _load_tuner_module():
    module_path = Path("tests/target_styles/tune_target_style.py").resolve()
    spec = importlib.util.spec_from_file_location("target_style_tuner", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tune_target_style module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _style_doc() -> dict:
    return {
        "camera": {
            "distance_scale": 3.0,
            "yaw_offset_deg": 0.0,
            "pitch_offset_deg": 0.0,
            "lens_mm": 60.0,
        },
        "lights": [
            {"energy": 10.0},
            {"energy": 5.0},
        ],
        "materials": {
            "surface_chain_a": {"alpha": 0.9, "roughness": 0.7},
            "surface_chain_b": {"alpha": 0.9, "roughness": 0.7},
            "ligand_main": {"alpha": 1.0, "roughness": 0.3},
        },
        "styles": [
            {
                "name": "chain_a_surface",
                "ligand_cutout_radius": 3.0,
                "probe_radius": 1.0,
            },
            {
                "name": "chain_b_surface",
                "ligand_cutout_radius": 3.0,
                "probe_radius": 1.0,
            },
            {
                "name": "chain_a_cartoon_local",
                "selection": {"within_ligand_radius": 6.0},
                "thickness": 0.4,
                "width": 1.0,
                "loop_radius": 0.2,
            },
            {
                "name": "chain_b_cartoon_local",
                "selection": {"within_ligand_radius": 6.0},
                "thickness": 0.4,
                "width": 1.0,
                "loop_radius": 0.2,
            },
            {
                "name": "ligand_sticks",
                "sticks_radius": 0.45,
                "sticks_quality": 6,
            },
        ],
    }


def test_ssim_fallback_identical_and_different():
    tuner = _load_tuner_module()
    left = np.full((16, 16, 3), 120.0, dtype=np.float32)
    right = left.copy()
    different = np.full((16, 16, 3), 20.0, dtype=np.float32)

    identical_score = tuner._ssim_fallback(left, right)
    different_score = tuner._ssim_fallback(left, different)

    assert identical_score == 1.0
    assert different_score < 1.0


def test_mutate_style_doc_is_deterministic_and_bounded():
    tuner = _load_tuner_module()
    seed_doc = _style_doc()
    rng_a = random.Random(11)
    rng_b = random.Random(11)

    mutated_a = tuner._mutate_style_doc(seed_doc, rng_a)
    mutated_b = tuner._mutate_style_doc(seed_doc, rng_b)

    assert mutated_a == mutated_b

    styles_by_name = {entry["name"]: entry for entry in mutated_a["styles"]}
    assert 1.5 <= float(styles_by_name["chain_a_surface"]["ligand_cutout_radius"]) <= 5.0
    assert 3 <= int(styles_by_name["ligand_sticks"]["sticks_quality"]) <= 10
