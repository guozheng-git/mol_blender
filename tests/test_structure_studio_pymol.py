"""Opt-in real PyMOL checks: set STRUCTURE_STUDIO_TEST_PYMOL to its Python."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYMOL_PYTHON = os.environ.get("STRUCTURE_STUDIO_TEST_PYMOL")


@pytest.mark.skipif(not PYMOL_PYTHON, reason="Set STRUCTURE_STUDIO_TEST_PYMOL for real PyMOL checks")
def test_pymol_preservation_blank_chains_and_styling(tmp_path):
    result = subprocess.run(
        [PYMOL_PYTHON, str(ROOT / "tests/structure_studio_pymol_checks.py"),
         str(ROOT / "plugins/structure-studio/skills/structure-studio/scripts"), str(tmp_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
