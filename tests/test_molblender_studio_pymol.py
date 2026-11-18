"""Opt-in real PyMOL checks: set MOLBLENDER_STUDIO_TEST_PYMOL to its Python."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYMOL_PYTHON = os.environ.get("MOLBLENDER_STUDIO_TEST_PYMOL")


@pytest.mark.skipif(not PYMOL_PYTHON, reason="Set MOLBLENDER_STUDIO_TEST_PYMOL for real PyMOL checks")
def test_pymol_preservation_blank_chains_and_styling(tmp_path):
    result = subprocess.run(
        [PYMOL_PYTHON, str(ROOT / "tests/molblender_studio_pymol_checks.py"),
         str(ROOT / "plugins/molblender_studio/skills/molblender-studio/scripts"), str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not PYMOL_PYTHON, reason="Set MOLBLENDER_STUDIO_TEST_PYMOL for real PyMOL checks")
def test_animation_recipes_preserve_session_and_export_states():
    skill = ROOT / 'plugins/molblender_studio/skills/molblender-studio'
    result = subprocess.run([
        PYMOL_PYTHON, str(ROOT / 'tests/molblender_studio_animation_checks.py'),
        str(skill / 'scripts'), os.environ.get('MOLBLENDER_STUDIO_TEST_WHEEL', str(ROOT)),
        str(ROOT / 'examples/1stp.cif'),
    ], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
