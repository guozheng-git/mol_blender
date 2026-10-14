"""Molecular classifications and colors shared by Python and the viewer."""
import json
from pathlib import Path

DATA = json.loads((Path(__file__).resolve().parent.parent / "assets" / "molecular-data.json").read_text())

WATER = frozenset(DATA["WATER"])
ION_ELEMENTS = frozenset(DATA["ION_ELEMENTS"])
ADDITIVES = tuple(DATA["ADDITIVES"])
NUCLEIC = frozenset(DATA["NUCLEIC"])
STANDARD = tuple(DATA["STANDARD"])
MODIFIED = tuple(DATA["MODIFIED"])
COVALENT_MIN_HEAVY = DATA["COVALENT_MIN_HEAVY"]
PALETTE = tuple(DATA["PALETTE"])
LIGAND_CARBON = DATA["LIGAND_CARBON"]
HIGHLIGHT_CARBON = DATA["HIGHLIGHT_CARBON"]
SS_COLORS = DATA["SS_COLORS"]
PLDDT = DATA["PLDDT"]


def ligand_key(comp, chain, seq, ins=""):
    """The colon marks a blank chain without conflating it with a numeric chain ID."""
    return f"{comp} {chain or ':'}{seq}{ins}"
