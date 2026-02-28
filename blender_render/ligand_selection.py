"""Ligand-aware selection helpers used by Blender rendering styles."""

from __future__ import annotations

from typing import Any

import numpy as np


SOLVENT_RES_NAMES = ("HOH", "WAT", "DOD", "SOL", "TIP3", "TIP3P")


def _as_upper(values: Any, count: int) -> np.ndarray:
    arr = np.asarray(values, dtype=object)
    if arr.shape[0] != count:
        arr = np.resize(arr, count)
    return np.char.upper(arr.astype(str))


def ligand_reference_mask(
    *,
    atom_array: Any,
    polymer_mask: np.ndarray,
    solvent_res_names: tuple[str, ...] = SOLVENT_RES_NAMES,
) -> np.ndarray:
    """Return a strict ligand reference mask: non-polymer and non-solvent."""

    count = int(polymer_mask.shape[0])
    res_name_upper = _as_upper(getattr(atom_array, "res_name", np.array([""] * count, dtype=object)), count)
    solvent_mask = np.isin(res_name_upper, [item.upper() for item in solvent_res_names])
    return (~np.asarray(polymer_mask, dtype=bool)) & (~solvent_mask)


def coordinates_from_atom_array(atom_array: Any) -> np.ndarray | None:
    raw = getattr(atom_array, "coord", None)
    if raw is None:
        return None

    candidates: list[Any] = [raw]
    try:
        if hasattr(raw, "__getitem__"):
            candidates.append(raw[0])
    except Exception:
        pass

    for candidate in candidates:
        try:
            coords = np.asarray(candidate)
        except Exception:
            continue

        if coords.ndim == 1 and coords.dtype == object and coords.size == 1:
            try:
                coords = np.asarray(coords[0])
            except Exception:
                continue

        if coords.ndim == 3 and coords.shape[-1] == 3 and coords.shape[0] >= 1:
            coords = coords[0]

        if coords.ndim == 1 and coords.size % 3 == 0 and coords.size > 0:
            try:
                coords = coords.reshape((-1, 3))
            except Exception:
                continue

        if coords.ndim != 2 or coords.shape[1] != 3:
            continue

        try:
            return np.asarray(coords, dtype=float)
        except Exception:
            try:
                return np.asarray(coords.tolist(), dtype=float)
            except Exception:
                continue

    return None


def proximity_mask_within_radius(
    coords: np.ndarray,
    reference_mask: np.ndarray,
    *,
    radius: float,
    query_mask: np.ndarray | None = None,
    chunk_size: int = 512,
) -> np.ndarray:
    """Return atoms within radius (Angstrom) of any reference atom."""

    count = int(coords.shape[0])
    output = np.zeros(count, dtype=bool)
    radius_value = max(0.0, float(radius))
    radius_sq = radius_value * radius_value
    reference_indices = np.where(np.asarray(reference_mask, dtype=bool))[0]
    if reference_indices.size == 0:
        return output

    finite_mask = np.isfinite(coords).all(axis=1)
    if query_mask is None:
        work_indices = np.where(finite_mask)[0]
    else:
        work_indices = np.where(np.asarray(query_mask, dtype=bool) & finite_mask)[0]
    if work_indices.size == 0:
        return output

    ref_indices = np.where(np.asarray(reference_mask, dtype=bool) & finite_mask)[0]
    if ref_indices.size == 0:
        return output

    safe_chunk = max(1, int(chunk_size))
    for query_start in range(0, work_indices.size, safe_chunk):
        query_chunk_idx = work_indices[query_start : query_start + safe_chunk]
        query_chunk = coords[query_chunk_idx]
        best_dist_sq = np.full(query_chunk.shape[0], np.inf, dtype=float)

        for ref_start in range(0, ref_indices.size, safe_chunk):
            ref_chunk_idx = ref_indices[ref_start : ref_start + safe_chunk]
            ref_chunk = coords[ref_chunk_idx]
            deltas = query_chunk[:, None, :] - ref_chunk[None, :, :]
            dist_sq = np.einsum("ijk,ijk->ij", deltas, deltas, optimize=True)
            best_dist_sq = np.minimum(best_dist_sq, dist_sq.min(axis=1))

        output[query_chunk_idx] = best_dist_sq <= radius_sq

    return output


def expand_mask_to_residues(mask: np.ndarray, chain_upper: np.ndarray, res_ids: np.ndarray) -> np.ndarray:
    """Expand atom mask to full residue membership by chain + residue id."""

    atom_mask = np.asarray(mask, dtype=bool)
    if atom_mask.size == 0 or not atom_mask.any():
        return atom_mask

    chain = np.asarray(chain_upper, dtype=object).astype(str)
    residues = np.asarray(res_ids, dtype=int).astype(str)
    keys = np.char.add(np.char.add(chain, ":"), residues)
    selected_keys = np.unique(keys[atom_mask])
    return np.isin(keys, selected_keys)
