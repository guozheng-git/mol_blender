"""Molecular classifications and colors shared by Python and the viewer.

The data and ligand rules live in co-pymol (co_pymol/scene/ligands.py), read from
this plugin's bundled wheel, so Studio and co-pymol classify ligands identically.
"""
import studio_env

studio_env.use_bundled_helpers()
from co_pymol.scene.ligands import (  # noqa: E402
    ADDITIVES, COVALENT_MIN_HEAVY, DATA, MODIFIED, STANDARD, ligand_key,
)

WATER = frozenset(DATA["WATER"])
ION_ELEMENTS = frozenset(DATA["ION_ELEMENTS"])
NUCLEIC = frozenset(DATA["NUCLEIC"])
PALETTE = tuple(DATA["PALETTE"])
LIGAND_CARBON = DATA["LIGAND_CARBON"]
HIGHLIGHT_CARBON = DATA["HIGHLIGHT_CARBON"]
SS_COLORS = DATA["SS_COLORS"]
PLDDT = DATA["PLDDT"]

__all__ = ["ADDITIVES", "COVALENT_MIN_HEAVY", "DATA", "MODIFIED", "STANDARD", "ligand_key", "WATER",
           "ION_ELEMENTS", "NUCLEIC", "PALETTE", "LIGAND_CARBON", "HIGHLIGHT_CARBON", "SS_COLORS", "PLDDT"]
