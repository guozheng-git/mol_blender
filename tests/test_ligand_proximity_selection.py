from __future__ import annotations

import numpy as np

from mol_blender.ligand_selection import (
    expand_mask_to_residues,
    ligand_reference_mask,
    proximity_mask_within_radius,
)


class _AtomArray:
    def __init__(self, *, coord: np.ndarray, res_name: np.ndarray):
        self.coord = coord
        self.res_name = res_name


def test_within_ligand_radius_selects_local_atoms():
    atom_array = _AtomArray(
        coord=np.asarray(
            [
                [0.0, 0.0, 0.0],   # ligand atom
                [1.8, 0.0, 0.0],   # near ligand
                [5.0, 0.0, 0.0],   # far
                [0.0, 0.0, 0.0],   # solvent at ligand center, excluded as reference
            ],
            dtype=float,
        ),
        res_name=np.asarray(["LIG", "ALA", "GLY", "HOH"], dtype=object),
    )
    polymer_mask = np.asarray([False, True, True, False], dtype=bool)
    ref = ligand_reference_mask(atom_array=atom_array, polymer_mask=polymer_mask)
    within = proximity_mask_within_radius(atom_array.coord, ref, radius=2.0)

    assert ref.tolist() == [True, False, False, False]
    assert within.tolist() == [True, True, False, True]


def test_exclude_within_ligand_radius_carves_surface_shell():
    atom_array = _AtomArray(
        coord=np.asarray(
            [
                [0.0, 0.0, 0.0],   # ligand
                [1.5, 0.0, 0.0],   # near polymer
                [4.2, 0.0, 0.0],   # far polymer
            ],
            dtype=float,
        ),
        res_name=np.asarray(["LIG", "ALA", "SER"], dtype=object),
    )
    polymer_mask = np.asarray([False, True, True], dtype=bool)
    ref = ligand_reference_mask(atom_array=atom_array, polymer_mask=polymer_mask)
    near = proximity_mask_within_radius(atom_array.coord, ref, radius=2.0)
    full = np.asarray([True, True, True], dtype=bool)
    carved = full & ~near

    assert carved.tolist() == [False, False, True]


def test_expand_to_residue_promotes_partial_selection():
    # Atoms 0/1 are same residue A:10, atom 2 is A:11, atom 3 is B:10.
    initial = np.asarray([False, True, False, False], dtype=bool)
    chain_upper = np.asarray(["A", "A", "A", "B"], dtype=object)
    res_ids = np.asarray([10, 10, 11, 10], dtype=int)

    expanded = expand_mask_to_residues(initial, chain_upper, res_ids)

    assert expanded.tolist() == [True, True, False, False]
