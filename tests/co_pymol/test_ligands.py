"""co-pymol and Structure Studio choose the same default animation focus."""
from pathlib import Path

import pytest

from co_pymol.scene.ligands import ADDITIVES, default_focus, ligand_records

STREPTAVIDIN = Path(__file__).resolve().parents[2] / "examples/1stp.cif"


@pytest.fixture
def cmd():
    pymol2 = pytest.importorskip("pymol2")
    with pymol2.PyMOL() as instance:
        instance.cmd.load(str(STREPTAVIDIN), "strep")
        yield instance.cmd


def test_the_real_ligand_is_the_default_focus(cmd, capfd):
    records = ligand_records(cmd, "enabled")
    assert [(r["key"], r["atoms"], r["additive"]) for r in records] == [("BTN A300", 16, False)]
    focus = default_focus(cmd)
    assert cmd.count_atoms(focus) == 16
    assert capfd.readouterr().out == ""  # Counting must not echo into the PyMOL console.


def test_an_additive_is_never_the_default_focus(cmd):
    assert "GOL" in ADDITIVES
    cmd.alter("resn BTN", "resn='GOL'")
    assert ligand_records(cmd, "enabled")[0]["additive"] is True
    assert default_focus(cmd) is None
