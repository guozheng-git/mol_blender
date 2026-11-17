# SPDX-License-Identifier: MIT
"""Ligand classification shared by co-pymol and Structure Studio.

The data (additives, standard and modified residues, colours) lives beside this
module and is read with importlib.resources, so it also loads from Studio's
bundled wheel. Stdlib only and Python 3.9+, like Studio's viewer scripts.
"""
from __future__ import annotations

import json
import re
from importlib.resources import files

# Spelled out: Python 3.9's zipimport (Studio's bundled wheel) leaves __package__ unset.
DATA = json.loads(files("co_pymol.scene").joinpath("molecular-data.json").read_text())
ADDITIVES = tuple(DATA["ADDITIVES"])
STANDARD = tuple(DATA["STANDARD"])
MODIFIED = tuple(DATA["MODIFIED"])
COVALENT_MIN_HEAVY = DATA["COVALENT_MIN_HEAVY"]


def ligand_key(comp, chain, seq, ins=""):
    """The colon marks a blank chain without conflating it with a numeric chain ID."""
    return f"{comp} {chain or ':'}{seq}{ins}"


def chain_selection(chains) -> str:
    """Select author chain IDs, including the empty ID used by unlabelled PDBs."""
    if isinstance(chains, str):
        chains = [chains]
    return "chain " + "+".join(c if re.fullmatch(r"[A-Za-z0-9_]+", c) else json.dumps(c) for c in chains)


def ligand_records(cmd, selection):
    """Ligands in a selection: real ligands first, then crystallization additives, largest first.

    Counts heavy atoms only. Non-standard polymer residues large enough to be a
    covalently attached ligand count too; ordinary modified residues do not.
    """
    records, covalent = {}, {}
    # An assignment, not a bare expression: PyMOL's iterate echoes expression values.
    count = "store[(resn, chain, resi)] = store.get((resn, chain, resi), 0) + 1"
    cmd.iterate(f"({selection}) and organic and not hydro", count, space={"store": records})
    cmd.iterate(f"({selection}) and polymer and not hydro and not resn {'+'.join(STANDARD + MODIFIED)}", count,
                space={"store": covalent})
    records.update({key: n for key, n in covalent.items() if n >= COVALENT_MIN_HEAVY})
    out = [{"key": ligand_key(resn, chain, resi), "resn": resn, "chain": chain, "resi": resi, "atoms": n,
            "additive": resn in ADDITIVES, "covalent": (resn, chain, resi) in covalent}
           for (resn, chain, resi), n in records.items()]
    return sorted(out, key=lambda r: (r["additive"], -r["atoms"]))


def residue_selection(selection, record) -> str:
    return f"({selection} and {chain_selection(record['chain'])} and resi \\{record['resi']} and resn {record['resn']})"


def default_focus(cmd, selection="enabled"):
    """The largest real (non-additive) ligand, as a PyMOL selection; None when there is none."""
    ligands = [ligand for ligand in ligand_records(cmd, selection) if not ligand["additive"]]
    return residue_selection(selection, ligands[0]) if ligands else None
