#!/usr/bin/env python3
"""Custom Blender render pipeline driven by style JSON."""

from __future__ import annotations

import argparse
import colorsys
import importlib.resources
import json
import math
import os
import re
import shlex
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any

import bpy
import numpy as np
from mathutils import Matrix, Vector

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from chain_palette import expand_polymer_styles_for_multichain_palette
except Exception:  # pragma: no cover - package import fallback
    from blender_render.chain_palette import expand_polymer_styles_for_multichain_palette

try:
    from ligand_selection import (
        coordinates_from_atom_array,
        expand_mask_to_residues,
        ligand_reference_mask,
        proximity_mask_within_radius,
    )
except Exception:  # pragma: no cover - package import fallback
    from blender_render.ligand_selection import (
        coordinates_from_atom_array,
        expand_mask_to_residues,
        ligand_reference_mask,
        proximity_mask_within_radius,
    )


def _configure_molecularnodes_import_path() -> None:
    raw = str(os.getenv("PRESS_MN_SITE_PACKAGES", "") or "").strip()
    if not raw:
        return
    paths = [item.strip() for item in raw.split(os.pathsep) if item.strip()]
    if not paths:
        return
    inserted = False
    for path_text in reversed(paths):
        path = str(Path(path_text).expanduser())
        if not path:
            continue
        if path in sys.path:
            try:
                sys.path.remove(path)
            except Exception:
                pass
        sys.path.insert(0, path)
        inserted = True
    if not inserted:
        return
    # Ensure a preloaded addon copy does not shadow an explicit override path.
    for prefix in ("molecularnodes", "databpy"):
        for module_name in list(sys.modules.keys()):
            if module_name == prefix or module_name.startswith(prefix + "."):
                sys.modules.pop(module_name, None)


def _ensure_databpy_compat() -> None:
    """
    Molecular Nodes 4.2.x expects databpy.Domains, but some databpy builds only
    expose AttributeDomains/AttributeDomain.
    """
    try:
        import databpy  # type: ignore
    except Exception:
        return
    if hasattr(databpy, "Domains"):
        return
    alias = getattr(databpy, "AttributeDomains", None)
    if alias is None:
        alias = getattr(databpy, "AttributeDomain", None)
    if alias is not None:
        setattr(databpy, "Domains", alias)

try:
    from bridge_runner import StyleValidationError, validate_style_document
except Exception:  # pragma: no cover - blender-only fallback
    class StyleValidationError(RuntimeError):
        pass

    def validate_style_document(style_doc: dict[str, Any]) -> dict[str, Any]:
        required = {"version", "output", "renderer", "camera", "world", "lights", "materials", "styles"}
        missing = sorted(required - set(style_doc))
        if missing:
            raise StyleValidationError(f"Missing style keys: {', '.join(missing)}")
        return style_doc


NUCLEIC_RES_NAMES = ("DA", "DT", "DG", "DC", "A", "U", "G", "C")
SOLVENT_RES_NAMES = ("HOH", "WAT", "DOD", "SOL", "TIP3", "TIP3P")


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    if "--" in argv:
        tail = argv[argv.index("--") + 1 :]
    else:
        tail = argv[1:]

    parser = argparse.ArgumentParser(description="Custom Blender renderer for CIF + PyMOL camera payload.")
    parser.add_argument("--structure", default="")
    parser.add_argument("--cif", default="")
    parser.add_argument("--view-json", required=True)
    parser.add_argument("--style-json", required=True)
    parser.add_argument("--output-png", required=True)
    parser.add_argument("--run-manifest", default="")
    parsed = parser.parse_args(tail)
    if not str(parsed.structure).strip():
        parsed.structure = str(parsed.cif or "").strip()
    if not str(parsed.structure).strip():
        parser.error("Expected --structure <path> (or legacy --cif <path>).")
    return parsed


def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    raw = hex_color.strip().lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"Expected #RRGGBB color, got {hex_color!r}")
    return (
        int(raw[0:2], 16) / 255.0,
        int(raw[2:4], 16) / 255.0,
        int(raw[4:6], 16) / 255.0,
        max(0.0, min(1.0, alpha)),
    )


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    r, g, b, _ = _hex_to_rgba(hex_color, alpha=1.0)
    return (r, g, b)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r = max(0.0, min(1.0, float(rgb[0])))
    g = max(0.0, min(1.0, float(rgb[1])))
    b = max(0.0, min(1.0, float(rgb[2])))
    return f"#{int(round(r * 255.0)):02X}{int(round(g * 255.0)):02X}{int(round(b * 255.0)):02X}"


def _mix_rgb(left: tuple[float, float, float], right: tuple[float, float, float], mix: float) -> tuple[float, float, float]:
    w = max(0.0, min(1.0, float(mix)))
    return (
        left[0] * (1.0 - w) + right[0] * w,
        left[1] * (1.0 - w) + right[1] * w,
        left[2] * (1.0 - w) + right[2] * w,
    )


def _adjust_hsv(rgb: tuple[float, float, float], sat_delta: float, value_delta: float) -> tuple[float, float, float]:
    h, s, v = colorsys.rgb_to_hsv(*rgb)
    s = max(0.0, min(1.0, s + float(sat_delta)))
    v = max(0.0, min(1.0, v + float(value_delta)))
    return colorsys.hsv_to_rgb(h, s, v)


def _shade_from_base(rgb: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    f = max(0.0, min(1.0, float(factor)))
    return (rgb[0] * f, rgb[1] * f, rgb[2] * f)


def _highlight_from_base(rgb: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    f = max(0.0, float(factor))
    return (
        min(1.0, rgb[0] + (1.0 - rgb[0]) * f),
        min(1.0, rgb[1] + (1.0 - rgb[1]) * f),
        min(1.0, rgb[2] + (1.0 - rgb[2]) * f),
    )


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected object JSON at {path}, got {type(data).__name__}")
    return data


def _strip_wrapping_quotes(value: str) -> str:
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _split_cif_fields(line: str) -> list[str]:
    text = str(line).strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True)
    except Exception:
        return text.split()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except Exception:
        return default


def _ensure_molecularnodes_ready(mn: Any) -> None:
    if hasattr(bpy.types.Object, "mn"):
        return
    register_fn = getattr(mn, "register", None)
    if callable(register_fn):
        register_fn()
    if not hasattr(bpy.types.Object, "mn"):
        raise RuntimeError(
            "Molecular Nodes did not initialize correctly: bpy.types.Object is missing 'mn' property."
        )


class _CompatSelection:
    def reset(self):
        return self

    def __getattr__(self, _name: str):
        def _chain(*_args, **_kwargs):
            return self

        return _chain


class _CompatMolecule:
    def __init__(self, loaded_obj: Any):
        self._loaded_obj = loaded_obj
        self.object = getattr(loaded_obj, "object", None) or loaded_obj
        self.select = _CompatSelection()
        self.styles: list[Any] = []

    def add_style(self, *_args, **_kwargs):
        style = type("CompatStyle", (), {})()
        self.styles.append(style)
        return style

    def __getattr__(self, name: str):
        return getattr(self._loaded_obj, name)


def _array_as_upper(values: Any, count: int) -> np.ndarray:
    raw = np.asarray(values, dtype=object).astype(str)
    if raw.shape[0] != count:
        return np.char.upper(raw)
    cleaned = np.array([_strip_wrapping_quotes(item) for item in raw.tolist()], dtype=object)
    return np.char.upper(cleaned.astype(str))


def _safe_bool_mask(values: Any, count: int) -> np.ndarray:
    try:
        mask = np.asarray(values, dtype=bool)
        if mask.shape[0] != count:
            raise ValueError("mask length mismatch")
        return mask
    except Exception:
        return np.zeros(count, dtype=bool)


def _query_term_flags(lowered_text: str, token: str) -> tuple[bool, bool]:
    pattern = re.compile(rf"(?:(\bnot\b|!)\s*)?\b{re.escape(token)}\b", flags=re.IGNORECASE)
    positive = False
    negative = False
    for match in pattern.finditer(lowered_text):
        if match.group(1):
            negative = True
        else:
            positive = True
    return positive, negative


def _semantic_masks(atom_array: Any, count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hetero = _safe_bool_mask(getattr(atom_array, "hetero", np.zeros(count, dtype=bool)), count)
    res_name_upper = _array_as_upper(getattr(atom_array, "res_name", np.array([""] * count, dtype=object)), count)
    solvent_mask = np.isin(res_name_upper, [item.upper() for item in SOLVENT_RES_NAMES])

    amino_mask = np.zeros(count, dtype=bool)
    nucleic_mask = np.isin(res_name_upper, [item.upper() for item in NUCLEIC_RES_NAMES])

    try:
        import biotite.structure as bs  # type: ignore

        amino_mask = _safe_bool_mask(bs.filter_amino_acids(atom_array), count)
        nucleic_mask = nucleic_mask | _safe_bool_mask(bs.filter_nucleotides(atom_array), count)
    except Exception:
        pass

    polymer_mask = amino_mask | nucleic_mask | (~hetero)
    if not polymer_mask.any():
        polymer_mask = amino_mask | nucleic_mask
    if not polymer_mask.any():
        polymer_mask = ~solvent_mask

    ligand_mask = hetero & ~polymer_mask
    if not ligand_mask.any():
        ligand_mask = (~(amino_mask | nucleic_mask)) & ~solvent_mask
    if not ligand_mask.any() and hetero.any():
        ligand_mask = hetero & ~solvent_mask

    return polymer_mask, nucleic_mask, ligand_mask


def _query_to_indices(
    atom_array: Any,
    query: str,
    *,
    chain_upper_override: np.ndarray | None = None,
) -> list[int] | None:
    text = str(query or "").strip()
    if not text:
        return None

    count = int(atom_array.array_length()) if hasattr(atom_array, "array_length") else len(atom_array)
    mask = np.ones(count, dtype=bool)
    polymer_mask, nucleic_mask, ligand_mask = _semantic_masks(atom_array, count)
    res_name_upper = _array_as_upper(getattr(atom_array, "res_name", np.array([""] * count, dtype=object)), count)
    if chain_upper_override is not None and len(chain_upper_override) == count:
        chain_upper = chain_upper_override
    else:
        chain_upper = _array_as_upper(getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)), count)
    res_ids = np.asarray(getattr(atom_array, "res_id", np.full(count, -1, dtype=int))).astype(int)

    lowered = text.lower()
    polymer_pos, polymer_neg = _query_term_flags(lowered, "polymer")
    ligand_pos, ligand_neg = _query_term_flags(lowered, "ligand")
    nucleic_pos, nucleic_neg = _query_term_flags(lowered, "nucleic")

    if polymer_pos:
        mask &= polymer_mask
    if polymer_neg:
        mask &= ~polymer_mask
    if ligand_pos:
        mask &= ligand_mask
    if ligand_neg:
        mask &= ~ligand_mask
    if nucleic_pos:
        mask &= nucleic_mask
    if nucleic_neg:
        mask &= ~nucleic_mask

    chain_include: list[str] = []
    chain_exclude: list[str] = []
    for match in re.finditer(r"(?:(\bnot\b|!)\s*)?\bchain\s*=?\s*([A-Za-z0-9_,]+)", text, flags=re.IGNORECASE):
        values = [item.strip().upper() for item in match.group(2).split(",") if item.strip()]
        if match.group(1):
            chain_exclude.extend(values)
        else:
            chain_include.extend(values)
    if chain_include:
        mask &= np.isin(chain_upper, chain_include)
    if chain_exclude:
        mask &= ~np.isin(chain_upper, chain_exclude)

    resn_include: list[str] = []
    resn_exclude: list[str] = []
    for match in re.finditer(r"(?:(\bnot\b|!)\s*)?\bresn\s*=?\s*([A-Za-z0-9_,]+)", text, flags=re.IGNORECASE):
        values = [item.strip().upper() for item in match.group(2).split(",") if item.strip()]
        if match.group(1):
            resn_exclude.extend(values)
        else:
            resn_include.extend(values)
    if resn_include:
        mask &= np.isin(res_name_upper, resn_include)
    if resn_exclude:
        mask &= ~np.isin(res_name_upper, resn_exclude)

    resi_match = re.search(r"\bresi\s*=?\s*([0-9]+)\s*-\s*([0-9]+)", text, flags=re.IGNORECASE)
    if resi_match:
        start = int(resi_match.group(1))
        end = int(resi_match.group(2))
        lo, hi = (start, end) if start <= end else (end, start)
        mask &= (res_ids >= lo) & (res_ids <= hi)
    else:
        resi_single = re.search(r"\bresi\s*=?\s*([0-9]+)\b", text, flags=re.IGNORECASE)
        if resi_single:
            target = int(resi_single.group(1))
            mask &= res_ids == target

    return np.where(mask)[0].astype(int).tolist()


def _atom_array_count(atom_array: Any) -> int:
    try:
        return int(atom_array.array_length()) if hasattr(atom_array, "array_length") else len(atom_array)
    except Exception:
        return 0


def _get_atom_array(source: Any) -> Any:
    array = getattr(source, "atom_array", None)
    if array is None:
        array = getattr(source, "array", None)
    return array


def _auth_chain_override_for_structure(structure_path: Path, count: int) -> np.ndarray | None:
    if count <= 0:
        return None
    if str(structure_path.suffix).strip().lower() != ".cif":
        return None
    auth_chain_ids = _extract_auth_chain_ids_from_cif(structure_path)
    if not auth_chain_ids:
        return None
    auth_upper = np.asarray([str(item).strip().upper() for item in auth_chain_ids], dtype=object).astype(str)
    if auth_upper.shape[0] != count:
        return None
    if not bool(np.any(np.array([item not in {"", ".", "?"} for item in auth_upper], dtype=bool))):
        return None
    return auth_upper


def _chain_upper_for_selection(load_result: Any, atom_array: Any, count: int, structure_path: Path) -> np.ndarray:
    chain_getter = getattr(load_result, "_chain_upper", None)
    if callable(chain_getter):
        try:
            chain_values = np.asarray(chain_getter(count), dtype=object).astype(str)
            if chain_values.shape[0] == count:
                return chain_values
        except Exception:
            pass

    bridge_override = getattr(load_result, "_bridge_chain_override_upper", None)
    if isinstance(bridge_override, np.ndarray) and bridge_override.shape[0] == count:
        return bridge_override

    chain_upper = _array_as_upper(
        getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)),
        count,
    )
    current_values = {str(item).strip().upper() for item in chain_upper.tolist() if str(item).strip()}
    if current_values.issubset({"", ".", "?"}):
        auth_override = _auth_chain_override_for_structure(structure_path, count)
        if auth_override is not None:
            return auth_override
    return chain_upper


def _selection_numeric_radius(selection_cfg: dict[str, Any], key: str) -> float | None:
    if key not in selection_cfg:
        return None
    try:
        return max(0.0, float(selection_cfg.get(key)))
    except Exception:
        return None


def _ligand_reference_mask_for_features(
    *,
    atom_array: Any,
    polymer_mask: np.ndarray,
    semantic_ligand_mask: np.ndarray,
) -> np.ndarray:
    strict_mask = ligand_reference_mask(
        atom_array=atom_array,
        polymer_mask=polymer_mask,
        solvent_res_names=SOLVENT_RES_NAMES,
    )
    if bool(np.any(strict_mask)):
        return strict_mask
    if bool(np.any(semantic_ligand_mask)):
        return np.asarray(semantic_ligand_mask, dtype=bool)
    return strict_mask


def _ligand_proximity_mask(
    atom_array: Any,
    *,
    polymer_mask: np.ndarray,
    semantic_ligand_mask: np.ndarray,
    radius: float,
    query_mask: np.ndarray | None = None,
) -> np.ndarray:
    count = int(polymer_mask.shape[0])
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.shape[0] != count:
        return np.zeros(count, dtype=bool)
    ref_mask = _ligand_reference_mask_for_features(
        atom_array=atom_array,
        polymer_mask=polymer_mask,
        semantic_ligand_mask=semantic_ligand_mask,
    )
    return proximity_mask_within_radius(
        coords,
        ref_mask,
        radius=max(0.0, float(radius)),
        query_mask=query_mask,
        chunk_size=512,
    )


def _selection_indices_from_cfg(
    load_result: Any,
    selection_cfg: dict[str, Any],
    *,
    structure_path: Path,
) -> list[int] | None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return None

    count = _atom_array_count(atom_array)
    if count <= 0:
        return []

    chain_upper = _chain_upper_for_selection(load_result, atom_array, count, structure_path)
    query_value = selection_cfg.get("query")
    mask = np.ones(count, dtype=bool)
    has_filter = False
    if isinstance(query_value, str) and query_value.strip():
        query_indices = _query_to_indices(
            atom_array,
            query_value,
            chain_upper_override=chain_upper,
        )
        if query_indices is not None:
            query_mask = np.zeros(count, dtype=bool)
            for idx in query_indices:
                if 0 <= int(idx) < count:
                    query_mask[int(idx)] = True
            mask &= query_mask
            has_filter = True

    polymer_mask, nucleic_mask, ligand_mask = _semantic_masks(atom_array, count)
    if bool(selection_cfg.get("polymer", False)):
        mask &= polymer_mask
        has_filter = True
    if bool(selection_cfg.get("nucleic", False)):
        mask &= nucleic_mask
        has_filter = True
    if bool(selection_cfg.get("ligand", False)):
        mask &= ligand_mask
        has_filter = True

    chain_value = selection_cfg.get("chain")
    chain_ids: list[str] = []
    if isinstance(chain_value, str):
        chain_ids = [item.strip().upper() for item in chain_value.split(",") if item.strip()]
    elif isinstance(chain_value, (list, tuple)):
        chain_ids = [str(item).strip().upper() for item in chain_value if str(item).strip()]
    if chain_ids:
        mask &= np.isin(chain_upper, chain_ids)
        has_filter = True

    residue_range = selection_cfg.get("residue_range")
    res_ids = np.asarray(getattr(atom_array, "res_id", np.full(count, -1, dtype=int))).astype(int)
    if isinstance(residue_range, (list, tuple)) and len(residue_range) == 2:
        try:
            start = int(residue_range[0])
            end = int(residue_range[1])
            lo, hi = (start, end) if start <= end else (end, start)
            mask &= (res_ids >= lo) & (res_ids <= hi)
            has_filter = True
        except Exception:
            pass

    within_ligand_radius = _selection_numeric_radius(selection_cfg, "within_ligand_radius")
    if within_ligand_radius is not None:
        mask &= _ligand_proximity_mask(
            atom_array,
            polymer_mask=polymer_mask,
            semantic_ligand_mask=ligand_mask,
            radius=within_ligand_radius,
        )
        has_filter = True

    exclude_within_ligand_radius = _selection_numeric_radius(selection_cfg, "exclude_within_ligand_radius")
    if exclude_within_ligand_radius is not None:
        mask &= ~_ligand_proximity_mask(
            atom_array,
            polymer_mask=polymer_mask,
            semantic_ligand_mask=ligand_mask,
            radius=exclude_within_ligand_radius,
        )
        has_filter = True

    if bool(selection_cfg.get("expand_to_residue", False)):
        mask = expand_mask_to_residues(mask, chain_upper, res_ids)
        has_filter = True

    if not has_filter:
        return None
    return np.where(mask)[0].astype(int).tolist()


def _selection_to_named_attribute(
    load_result: Any,
    indices: list[int] | None,
    *,
    style_name: str,
) -> Any:
    if indices is None:
        return None
    if isinstance(load_result, _ParserMolecule):
        return indices
    if not hasattr(load_result, "store_named_attribute"):
        return indices

    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return indices
    count = _atom_array_count(atom_array)
    if count <= 0:
        return indices

    mask = np.zeros(count, dtype=bool)
    for idx in indices:
        if 0 <= int(idx) < count:
            mask[int(idx)] = True

    style_token = re.sub(r"[^A-Za-z0-9_]+", "_", str(style_name or "style")).strip("_") or "style"
    attr_name = f"bridge_sel_{style_token.lower()}_{uuid.uuid4().hex[:8]}"
    try:
        load_result.store_named_attribute(mask, name=attr_name, atype="BOOLEAN", domain="POINT")
        return attr_name
    except Exception:
        pass
    try:
        load_result.store_named_attribute(mask, name=attr_name)
        return attr_name
    except Exception:
        return indices


def _normalized_amino_mask(atom_array: Any, count: int) -> np.ndarray:
    try:
        import biotite.structure as bs  # type: ignore

        return _safe_bool_mask(bs.filter_amino_acids(atom_array), count)
    except Exception:
        res_name_upper = _array_as_upper(getattr(atom_array, "res_name", np.array([""] * count, dtype=object)), count)
        return np.isin(
            res_name_upper,
            [
                "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE",
                "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL",
                "PTR", "TPO", "SEP",
            ],
        )


def _normalize_sec_struct_values(values: Any, amino_mask: np.ndarray) -> np.ndarray:
    count = int(amino_mask.shape[0])
    sec = np.zeros(count, dtype=int)
    raw = np.asarray(values, dtype=object)
    if raw.shape[0] != count:
        return sec

    parsed_numeric = False
    try:
        numeric = raw.astype(float)
        finite = np.isfinite(numeric)
        if bool(np.any(finite)):
            clipped = np.clip(np.rint(numeric), 0, 3).astype(int)
            sec[finite] = clipped[finite]
            parsed_numeric = True
    except Exception:
        parsed_numeric = False

    if not parsed_numeric:
        text = np.char.upper(raw.astype(str))
        helix_mask = np.isin(text, ["1", "H", "A", "G", "I"])
        sheet_mask = np.isin(text, ["2", "S", "E", "B"])
        loop_mask = np.isin(text, ["3", "L", "C", "T"])
        sec[helix_mask] = 1
        sec[sheet_mask] = 2
        sec[loop_mask] = 3

    sec[~amino_mask] = 0
    sec[np.asarray(amino_mask, dtype=bool) & (sec == 0)] = 3
    return sec


class _ParserSelection:
    def __init__(self, owner: "_ParserMolecule"):
        self._owner = owner
        self.reset()

    def reset(self):
        self._polymer = False
        self._nucleic = False
        self._ligand = False
        self._chain_ids: list[str] = []
        self._res_ids: list[int] = []
        return self

    def is_polymer(self):
        self._polymer = True
        return self

    def is_nucleic(self):
        self._nucleic = True
        return self

    def is_nucleotide(self):
        self._nucleic = True
        return self

    def is_canonical_nucleotide(self):
        self._nucleic = True
        return self

    def is_ligand(self):
        self._ligand = True
        return self

    def chain_id(self, value: Any):
        if isinstance(value, (list, tuple)):
            chain_values = [str(item).strip() for item in value]
        else:
            chain_values = [str(value).strip()]
        self._chain_ids = [item for item in chain_values if item]
        return self

    def res_id(self, value: Any):
        if isinstance(value, (list, tuple)):
            values = list(value)
        else:
            values = [value]
        parsed: list[int] = []
        for item in values:
            try:
                parsed.append(int(item))
            except Exception:
                continue
        self._res_ids = parsed
        return self

    def to_indices(self) -> list[int]:
        array = self._owner.atom_array
        count = int(array.array_length()) if hasattr(array, "array_length") else len(array)
        mask = np.ones(count, dtype=bool)
        polymer_mask, nucleic_mask, ligand_mask = _semantic_masks(array, count)
        chain_upper = self._owner._chain_upper(count)
        res_ids = np.asarray(getattr(array, "res_id", np.full(count, -1, dtype=int))).astype(int)

        if self._polymer:
            mask &= polymer_mask
        if self._nucleic:
            mask &= nucleic_mask
        if self._ligand:
            mask &= ligand_mask
        if self._chain_ids:
            wanted = [item.upper() for item in self._chain_ids]
            mask &= np.isin(chain_upper, wanted)
        if self._res_ids:
            mask &= np.isin(res_ids, np.asarray(self._res_ids, dtype=int))

        return np.where(mask)[0].astype(int).tolist()

    def __getattr__(self, _name: str):
        def _chain(*_args, **_kwargs):
            return self

        return _chain


class _ParserMolecule:
    def __init__(self, parser_obj: Any, name: str, auth_chain_ids: list[str] | None = None):
        self._parser = parser_obj
        self._base_name = name
        self.styles: list[Any] = []
        self.object: bpy.types.Object | None = None
        self._style_counter = 0
        self._auth_chain_ids = [str(item).strip().upper() for item in (auth_chain_ids or [])]
        self._chain_override_upper: np.ndarray | None = None
        self._atom_array = self._coerce_atom_array()
        self.select = _ParserSelection(self)

        initial_object = getattr(self._parser, "object", None)
        if initial_object is not None:
            try:
                initial_object.hide_render = True
            except Exception:
                pass
            try:
                initial_object.hide_set(True)
            except Exception:
                pass

    def _coerce_atom_array(self):
        array = getattr(self._parser, "array", None)
        if array is None:
            raise RuntimeError("MolecularNodes parser object is missing atom array data.")
        try:
            if array.__class__.__name__ == "AtomArrayStack" and len(array) > 0:
                array = array[0]
                self._parser.array = array
        except Exception:
            pass

        # PyMOL CIF exports often store real chain IDs in auth_asym_id while
        # label_asym_id is ".". MN parser may expose only label_asym_id via
        # atom_array.chain_id, which breaks chain-based styles.
        try:
            count = int(array.array_length()) if hasattr(array, "array_length") else len(array)
            current_chain_upper = _array_as_upper(
                getattr(array, "chain_id", np.array([""] * count, dtype=object)),
                count,
            )
            current_values = {item for item in current_chain_upper.tolist() if item}
            auth_values = np.asarray(self._auth_chain_ids, dtype=object).astype(str)
            if auth_values.size == count:
                auth_upper = np.char.upper(auth_values)
                auth_nonempty = np.array([item not in {"", ".", "?"} for item in auth_upper], dtype=bool)
                if current_values.issubset({"", ".", "?"}) and bool(np.any(auth_nonempty)):
                    patched = False
                    try:
                        array.chain_id = auth_upper
                        patched = True
                    except Exception:
                        patched = False
                    if not patched:
                        try:
                            categories = list(array.get_annotation_categories()) if hasattr(array, "get_annotation_categories") else []
                            if "chain_id" in categories:
                                try:
                                    array.del_annotation("chain_id")
                                except Exception:
                                    pass
                            array.set_annotation("chain_id", auth_upper)
                            patched = True
                        except Exception:
                            patched = False
                    # Even if direct patch fails, keep a trusted override for selection logic.
                    self._chain_override_upper = auth_upper
                    if patched and _env_bool("PRESS_DEBUG_CHAIN_PATCH", False):
                        print("Bridge Custom: patched parser chain_id from auth_asym_id")
                elif bool(np.any(auth_nonempty)):
                    # Preserve auth chain IDs as a higher-quality selector source.
                    self._chain_override_upper = auth_upper
        except Exception:
            pass

        # CIF payloads from PyMOL often omit secondary-structure annotation.
        # Populate it so cartoon styles can produce full geometry.
        try:
            categories = []
            if hasattr(array, "get_annotation_categories"):
                categories = list(array.get_annotation_categories())
            has_sse = "sec_struct" in categories
            needs_sse = True
            count = int(array.array_length()) if hasattr(array, "array_length") else len(array)
            amino_mask = _normalized_amino_mask(array, count)
            if has_sse:
                existing_raw = np.asarray(getattr(array, "sec_struct", []), dtype=object)
                if existing_raw.size == count:
                    existing_numeric = _normalize_sec_struct_values(existing_raw, amino_mask)
                    if bool(np.any(existing_numeric > 0)):
                        needs_sse = False
                    if has_sse:
                        try:
                            array.del_annotation("sec_struct")
                        except Exception:
                            pass
                    array.set_annotation("sec_struct", existing_numeric.astype(int))
            if needs_sse:
                import biotite.structure as bs  # type: ignore

                sec_struct = bs.annotate_sse(array)
                if sec_struct is not None and len(sec_struct) == len(array):
                    sec_struct = _normalize_sec_struct_values(sec_struct, amino_mask)
                    if has_sse:
                        try:
                            array.del_annotation("sec_struct")
                        except Exception:
                            pass
                    array.set_annotation("sec_struct", sec_struct.astype(int))
        except Exception:
            pass
        return array

    @property
    def atom_array(self):
        return self._atom_array

    def _chain_upper(self, count: int) -> np.ndarray:
        if self._chain_override_upper is not None and len(self._chain_override_upper) == count:
            return self._chain_override_upper
        return _array_as_upper(getattr(self._atom_array, "chain_id", np.array([""] * count, dtype=object)), count)

    def _selection_indices(self, selection: Any) -> list[int] | None:
        if isinstance(selection, _ParserSelection):
            return selection.to_indices()
        if isinstance(selection, str):
            count = int(self._atom_array.array_length()) if hasattr(self._atom_array, "array_length") else len(self._atom_array)
            return _query_to_indices(
                self._atom_array,
                selection,
                chain_upper_override=self._chain_upper(count),
            )
        if isinstance(selection, np.ndarray):
            if selection.dtype == bool:
                return np.where(selection)[0].astype(int).tolist()
            return selection.astype(int).tolist()
        if isinstance(selection, (list, tuple)):
            if not selection:
                return []
            if all(isinstance(item, bool) for item in selection):
                return [idx for idx, flag in enumerate(selection) if flag]
            parsed: list[int] = []
            for item in selection:
                try:
                    parsed.append(int(item))
                except Exception:
                    continue
            return parsed
        return None

    @staticmethod
    def _object_dimension_max(obj: Any) -> float:
        if obj is None:
            return 0.0
        try:
            dims = getattr(obj, "dimensions", None)
            if dims is None:
                return 0.0
            return float(max(float(dims[0]), float(dims[1]), float(dims[2])))
        except Exception:
            return 0.0

    @staticmethod
    def _evaluated_mesh_stats(obj: Any) -> tuple[int, float]:
        if obj is None or str(getattr(obj, "type", "")).upper() != "MESH":
            return 0, 0.0
        eval_obj = None
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            eval_obj = obj.evaluated_get(depsgraph)
            eval_mesh = eval_obj.to_mesh()
        except Exception:
            return 0, 0.0

        try:
            vertices = getattr(eval_mesh, "vertices", None)
            if vertices is None:
                return 0, 0.0
            count = int(len(vertices))
            if count == 0:
                return 0, 0.0
            min_x = min(float(v.co.x) for v in vertices)
            max_x = max(float(v.co.x) for v in vertices)
            min_y = min(float(v.co.y) for v in vertices)
            max_y = max(float(v.co.y) for v in vertices)
            min_z = min(float(v.co.z) for v in vertices)
            max_z = max(float(v.co.z) for v in vertices)
            extent = max(max_x - min_x, max_y - min_y, max_z - min_z)
            return count, float(extent)
        except Exception:
            return 0, 0.0
        finally:
            if eval_obj is not None:
                try:
                    eval_obj.to_mesh_clear()
                except Exception:
                    pass

    @staticmethod
    def _replace_object(old_obj: Any, new_obj: Any) -> Any:
        if old_obj is None or new_obj is None:
            return new_obj
        try:
            bpy.data.objects.remove(old_obj, do_unlink=True)
        except Exception:
            pass
        return new_obj

    @staticmethod
    def _remove_created_object_if_present(name: str) -> None:
        if not name:
            return
        obj = bpy.data.objects.get(name)
        if obj is None:
            return
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:
            pass

    def add_style(self, style_name: str, selection: Any = None, material: Any = None, **kwargs: Any):
        style_aliases = {
            "sphere": "spheres",
        }
        resolved_style = style_aliases.get(style_name, style_name)
        style_candidates = [resolved_style]
        indices = self._selection_indices(selection)

        if indices is not None and len(indices) == 0:
            style = type("CompatStyle", (), {})()
            setattr(style, "_object", None)
            setattr(style, "_selection_indices", [])
            setattr(style, "_material", material)
            self.styles.append(style)
            self._style_counter += 1
            return style

        created = None
        creation_errors: list[str] = []

        def _create_style_object(create_kwargs: dict[str, Any]) -> Any:
            create_fn = getattr(self._parser, "create_model", None)
            if callable(create_fn):
                return create_fn(**create_kwargs)
            create_fn = getattr(self._parser, "create_object", None)
            if callable(create_fn):
                return create_fn(**create_kwargs)
            raise RuntimeError("Molecular Nodes loader did not expose create_model/create_object.")

        for style_candidate in style_candidates:
            create_kwargs: dict[str, Any] = {
                "name": f"{self._base_name}_{style_candidate}_{self._style_counter}",
                "style": style_candidate,
                "centre": False,
                "del_solvent": bool(kwargs.get("del_solvent", False)),
            }
            candidate_object_name = str(create_kwargs["name"])
            if indices is not None:
                create_kwargs["selection"] = indices
            for key in (
                "radius",
                "probe_radius",
                "quality",
                "subdivisions",
                "thickness",
                "width",
                "loop_radius",
                "shade_smooth",
                "color",
                "peptide_cylinders",
                "peptide_rounded",
                "peptide_smoothing",
                "nucleic_thickness",
                "nucleic_width",
                "nucleic_radius",
                "backbone_shape",
                "base_shape",
                "base_realize",
                "color_blur",
            ):
                if key in kwargs:
                    create_kwargs[key] = kwargs[key]

            try:
                created = _create_style_object(create_kwargs)
                resolved_style = style_candidate
                break
            except TypeError:
                self._remove_created_object_if_present(candidate_object_name)
                fallback_kwargs = {
                    "name": create_kwargs["name"],
                    "style": create_kwargs["style"],
                    "centre": create_kwargs["centre"],
                    "del_solvent": create_kwargs["del_solvent"],
                }
                if "selection" in create_kwargs:
                    fallback_kwargs["selection"] = create_kwargs["selection"]
                try:
                    created = _create_style_object(fallback_kwargs)
                    resolved_style = style_candidate
                    break
                except Exception as exc:
                    self._remove_created_object_if_present(candidate_object_name)
                    if _env_bool("PRESS_DEBUG_STYLE_CREATE", False):
                        print(
                            "Bridge Custom style create fallback failed: "
                            f"base={self._base_name} style_name={style_name} "
                            f"candidate={style_candidate} error={type(exc).__name__}: {exc}"
                        )
                    creation_errors.append(str(exc))
                    continue
            except Exception as exc:
                self._remove_created_object_if_present(candidate_object_name)
                if _env_bool("PRESS_DEBUG_STYLE_CREATE", False):
                    print(
                        "Bridge Custom style create failed: "
                        f"base={self._base_name} style_name={style_name} "
                        f"candidate={style_candidate} error={type(exc).__name__}: {exc}"
                    )
                creation_errors.append(str(exc))
                continue

        if created is None:
            raise RuntimeError(
                f"Could not create style {style_name!r} (candidates={style_candidates}). "
                f"Recent errors: {' | '.join(creation_errors[-2:])}"
            )

        if style_name == "sticks" and resolved_style == "sticks":
            desired_radius = kwargs.get("sticks_radius", kwargs.get("radius"))
            desired_quality = kwargs.get("sticks_quality", kwargs.get("quality"))
            desired_shade_smooth = kwargs.get("sticks_shade_smooth", kwargs.get("shade_smooth"))
            desired_color_blur = kwargs.get("color_blur")
            tuned = _tune_native_sticks_style(
                created,
                float(desired_radius) if desired_radius is not None else None,
                desired_quality,
                desired_shade_smooth,
                desired_color_blur,
                disable_atom_spheres=True,
            )
            if tuned > 0:
                print(
                    "Bridge Custom: tuned native sticks style "
                    f"(sockets={tuned}) for {self._base_name}"
                )

        try:
            created.hide_render = False
        except Exception:
            pass
        try:
            created.hide_set(False)
        except Exception:
            pass

        if material is not None:
            _apply_material_fallback(created, material)

        if self.object is None:
            self.object = created
        else:
            try:
                created.parent = self.object
                created.matrix_parent_inverse = self.object.matrix_world.inverted()
            except Exception:
                pass

        style = type("CompatStyle", (), {})()
        setattr(style, "_object", created)
        setattr(style, "_selection_indices", indices if indices is not None else [])
        setattr(style, "_material", material)
        self.styles.append(style)
        self._style_counter += 1
        return style

    def __getattr__(self, name: str):
        return getattr(self._parser, name)


def _extract_auth_chain_ids_from_cif(structure_path: Path) -> list[str] | None:
    # Prefer a lightweight text parse so chain recovery works even when
    # optional parser APIs differ across MolecularNodes/biotite versions.
    try:
        lines = structure_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        lines = []

    headers: list[str] = []
    reading_atom_loop = False
    parsed_chain_ids: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.lower() == "loop_":
            headers = []
            reading_atom_loop = False
            continue

        if line.startswith("_atom_site."):
            reading_atom_loop = True
            headers.append(line.split(".", 1)[1].strip())
            continue

        if reading_atom_loop and headers and line.startswith("_"):
            reading_atom_loop = False
            headers = []

        if reading_atom_loop and headers:
            parts = _split_cif_fields(line)
            if not parts or parts[0].upper() not in {"ATOM", "HETATM"}:
                continue
            header_idx = {name: idx for idx, name in enumerate(headers)}
            auth_idx = header_idx.get("auth_asym_id")
            label_idx = header_idx.get("label_asym_id")
            chain_idx = auth_idx if auth_idx is not None else label_idx
            if chain_idx is None or chain_idx >= len(parts):
                continue
            chain_id = _strip_wrapping_quotes(str(parts[chain_idx])).strip().upper()
            if chain_id and chain_id not in {".", "?"}:
                parsed_chain_ids.append(chain_id)

    if parsed_chain_ids:
        return parsed_chain_ids

    try:
        import biotite.structure.io.pdbx as pdbx  # type: ignore
    except Exception:
        return None

    try:
        cif_file = pdbx.CIFFile.read(str(structure_path))
        keys = list(cif_file.keys())
        if not keys:
            return None
        block = cif_file[keys[0]]
        if "atom_site" not in block:
            return None
        atom_site = block["atom_site"]
        if "auth_asym_id" not in atom_site:
            return None
        values = atom_site["auth_asym_id"].as_array(str)
        return [_strip_wrapping_quotes(str(item)).strip().upper() for item in values]
    except Exception:
        return None


def _as_style_molecule(loaded: Any, name: str, structure_path: Path):
    auth_chain_ids = _extract_auth_chain_ids_from_cif(structure_path)
    if hasattr(loaded, "select") and hasattr(loaded, "add_style"):
        return loaded
    if (hasattr(loaded, "create_model") or hasattr(loaded, "create_object")) and hasattr(loaded, "array"):
        return _ParserMolecule(loaded, name=name, auth_chain_ids=auth_chain_ids)
    return _CompatMolecule(loaded)


def _apply_auth_chain_override(load_result: Any, structure_path: Path) -> None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return
    count = _atom_array_count(atom_array)
    if count <= 0:
        return

    chain_upper = _array_as_upper(
        getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)),
        count,
    )
    current_values = {str(item).strip().upper() for item in chain_upper.tolist() if str(item).strip()}
    if not current_values.issubset({"", ".", "?"}):
        return

    auth_override = _auth_chain_override_for_structure(structure_path, count)
    if auth_override is None:
        return
    try:
        setattr(load_result, "_bridge_chain_override_upper", auth_override)
    except Exception:
        pass

    patched = False
    try:
        atom_array.chain_id = auth_override
        patched = True
    except Exception:
        patched = False
    if not patched:
        try:
            categories = list(atom_array.get_annotation_categories()) if hasattr(atom_array, "get_annotation_categories") else []
            if "chain_id" in categories:
                try:
                    atom_array.del_annotation("chain_id")
                except Exception:
                    pass
            atom_array.set_annotation("chain_id", auth_override)
            patched = True
        except Exception:
            patched = False
    if patched and _env_bool("PRESS_DEBUG_CHAIN_PATCH", False):
        print("Bridge Custom: patched molecule chain_id from CIF auth_asym_id")


def _cycles_compute_options(cycles_prefs) -> list[str]:
    try:
        prop = cycles_prefs.bl_rna.properties.get("compute_device_type")
        if prop is None:
            return []
        return [str(item.identifier).upper() for item in prop.enum_items]
    except Exception:
        return []


def _configure_cycles_gpu_for_custom(scene: bpy.types.Scene, renderer: dict[str, Any]) -> None:
    requested = str(renderer.get("compute_device_type", "")).strip().upper()
    if not requested:
        requested = os.getenv("PRESS_CYCLES_COMPUTE_TYPE", "AUTO").strip().upper() or "AUTO"

    try:
        addon = bpy.context.preferences.addons.get("cycles")
    except Exception:
        addon = None
    if addon is None:
        print("Bridge Custom Cycles GPU: cycles addon preferences unavailable")
        return

    prefs = addon.preferences
    options = _cycles_compute_options(prefs)
    if requested == "AUTO":
        candidates = ["OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"]
    else:
        candidates = [requested]

    selected = None
    for candidate in candidates:
        if options and candidate not in options:
            continue
        try:
            prefs.compute_device_type = candidate
            selected = candidate
            break
        except Exception:
            continue

    try:
        prefs.get_devices()
    except Exception:
        pass

    infos: list[str] = []
    gpu_enabled = 0
    for dev in list(getattr(prefs, "devices", [])):
        dev_type = str(getattr(dev, "type", "")).upper()
        dev_name = str(getattr(dev, "name", "unknown"))
        is_gpu = dev_type not in {"", "CPU"}
        use_flag = bool(getattr(dev, "use", False))
        if getattr(scene.cycles, "device", "CPU") == "GPU":
            try:
                dev.use = is_gpu
                use_flag = bool(getattr(dev, "use", False))
            except Exception:
                pass
        if is_gpu and use_flag:
            gpu_enabled += 1
        infos.append(f"{dev_name}:{dev_type}:use={int(use_flag)}")

    require_gpu = bool(renderer.get("require_gpu", _env_bool("PRESS_REQUIRE_GPU", False)))
    if getattr(scene.cycles, "device", "CPU") == "GPU" and gpu_enabled == 0:
        message = (
            "Bridge Custom Cycles GPU: no GPU device enabled "
            f"(requested={requested}, selected={selected}, options={options}, devices={infos})"
        )
        if require_gpu:
            raise RuntimeError(message)
        print(message)

    if _env_bool("PRESS_PRINT_DEVICE_INFO", True):
        print(
            "Bridge Custom Cycles GPU: "
            f"scene_device={getattr(scene.cycles, 'device', 'CPU')} "
            f"requested={requested} selected={selected} options={options} devices={infos}"
        )


def _ensure_biotite_pdbx_compat() -> None:
    """Provide compatibility aliases for Molecular Nodes on newer Biotite builds.

    Molecular Nodes releases used in this project may still import
    ``biotite.structure.io.pdbx.PDBxFile``. Biotite 1.x replaced this symbol
    with ``CIFFile``.
    """
    try:
        import biotite.structure.io.pdbx as pdbx  # type: ignore
    except Exception:
        return

    if hasattr(pdbx, "PDBxFile") or not hasattr(pdbx, "CIFFile"):
        return

    def _legacy_category(category: Any) -> dict[str, np.ndarray] | None:
        if category is None:
            return None
        try:
            keys = list(category.keys())
        except Exception:
            return None
        converted: dict[str, np.ndarray] = {}
        for key in keys:
            try:
                column = category[key]
            except Exception:
                continue
            if hasattr(column, "as_array"):
                try:
                    values = column.as_array(str)
                except Exception:
                    try:
                        values = column.as_array()
                    except Exception:
                        continue
            else:
                try:
                    values = np.asarray(column)
                except Exception:
                    continue
            converted[str(key)] = np.asarray(values)
        return converted

    class _CompatPDBxFile:
        def __init__(self, cif_file: Any):
            self._cif_file = cif_file
            self.block = self._resolve_block(cif_file)

        @staticmethod
        def _resolve_block(cif_file: Any) -> Any:
            try:
                keys = list(cif_file.keys())
                if keys:
                    return cif_file[keys[0]]
            except Exception:
                pass
            return getattr(cif_file, "block", cif_file)

        @classmethod
        def read(cls, file_path: str | Path):
            cif_file = pdbx.CIFFile.read(file_path)
            return cls(cif_file)

        def get_category(self, name: str, **_kwargs: Any):
            category = None
            try:
                category = self.block.get(name)
            except Exception:
                try:
                    category = self.block[name]
                except Exception:
                    category = None
            return _legacy_category(category)

        def __getitem__(self, key: str):
            return self.get_category(key)

        def get(self, key: str, default: Any = None):
            value = self.get_category(key)
            return default if value is None else value

    try:
        setattr(pdbx, "PDBxFile", _CompatPDBxFile)
        if _env_bool("PRESS_DEBUG_LOADER", False):
            print("Bridge Custom loader: installed compat pdbx.PDBxFile wrapper")
    except Exception:
        pass


def _activate_biotite_ccd_path(ccd_path: Path) -> None:
    try:
        from biotite.structure.info import ccd as ccd_module  # type: ignore
    except Exception:
        return
    setter = getattr(ccd_module, "set_ccd_path", None)
    if callable(setter):
        try:
            setter(str(ccd_path))
        except Exception:
            pass


def _biotite_ccd_target_paths() -> list[Path]:
    targets: list[Path] = []
    override = str(os.getenv("PRESS_BIOTITE_CCD_PATH", "") or "").strip()
    if override:
        targets.append(Path(override).expanduser())

    try:
        from biotite.structure.info import ccd as ccd_module  # type: ignore

        raw_path = getattr(ccd_module, "_CCD_FILE", None)
        if raw_path:
            canonical = Path(raw_path)
            targets.append(canonical)
            if canonical.name == "components.bcif":
                # Support both layouts seen in Biotite/MN combinations:
                #   .../structure/info/components.bcif
                #   .../structure/info/ccd/components.bcif
                if canonical.parent.name == "ccd":
                    targets.append(canonical.parent.parent / "components.bcif")
                else:
                    targets.append(canonical.parent / "ccd" / "components.bcif")
    except Exception:
        pass

    # Fallback targets even when _CCD_FILE is unavailable.
    try:
        import biotite.structure.info as info_module  # type: ignore

        info_dir = Path(getattr(info_module, "__file__", "")).expanduser().resolve().parent
        if info_dir.is_dir():
            targets.append(info_dir / "components.bcif")
            targets.append(info_dir / "ccd" / "components.bcif")
    except Exception:
        pass

    targets.append(Path.home() / ".cache" / "pymol_bridge" / "components.bcif")
    targets.append(Path.home() / ".cache" / "pymol_bridge" / "ccd" / "components.bcif")

    seen: set[str] = set()
    unique: list[Path] = []
    for raw in targets:
        path = Path(raw).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _read_packaged_biotite_ccd_bytes() -> tuple[bytes | None, str]:
    try:
        package_root = importlib.resources.files("biotite.structure.info")
    except Exception as exc:
        return (None, f"biotite.structure.info resources unavailable: {type(exc).__name__}: {exc}")

    resource_candidates = [
        ("components.bcif", package_root.joinpath("components.bcif")),
        ("ccd/components.bcif", package_root.joinpath("ccd").joinpath("components.bcif")),
    ]
    errors: list[str] = []
    for label, resource in resource_candidates:
        try:
            with importlib.resources.as_file(resource) as local_path:
                path = Path(local_path)
                if path.is_file():
                    return (path.read_bytes(), f"resource:{path}")
        except Exception:
            pass
        try:
            return (resource.read_bytes(), f"resource:biotite.structure.info/{label}")
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            continue
    return (None, "biotite packaged CCD unreadable: " + " | ".join(errors))


def _biotite_wheel_candidates() -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        expanded = Path(path).expanduser()
        key = str(expanded)
        if key in seen:
            return
        seen.add(key)
        if expanded.is_file():
            candidates.append(expanded)

    mn_module = sys.modules.get("molecularnodes")
    mn_file = str(getattr(mn_module, "__file__", "") or "").strip()
    if mn_file:
        try:
            mn_path = Path(mn_file).expanduser().resolve()
            for parent in (mn_path.parent, *mn_path.parents):
                wheels_dir = parent / "wheels"
                if not wheels_dir.is_dir():
                    continue
                for wheel in sorted(wheels_dir.glob("biotite-*.whl")):
                    _add(wheel)
                break
        except Exception:
            pass

    common_roots = [
        Path.home() / "Library" / "Application Support" / "Blender",
        Path.home() / ".config" / "blender",
        Path.home() / "AppData" / "Roaming" / "Blender Foundation" / "Blender",
    ]
    for root in common_roots:
        if not root.exists():
            continue
        try:
            for wheel in root.glob("**/extensions/*/molecularnodes/wheels/biotite-*.whl"):
                _add(wheel)
                if len(candidates) >= 8:
                    return candidates
        except Exception:
            continue
    return candidates


def _read_biotite_ccd_bytes_from_wheels() -> tuple[bytes | None, str]:
    members = [
        "biotite/structure/info/components.bcif",
        "biotite/structure/info/ccd/components.bcif",
    ]
    wheel_errors: list[str] = []
    for wheel in _biotite_wheel_candidates():
        try:
            with zipfile.ZipFile(wheel) as archive:
                for member in members:
                    try:
                        data = archive.read(member)
                    except KeyError:
                        continue
                    return (data, f"wheel:{wheel}::{member}")
        except Exception as exc:
            wheel_errors.append(f"{wheel}: {type(exc).__name__}: {exc}")
            continue
    if wheel_errors:
        return (None, "wheel lookup failed: " + " | ".join(wheel_errors[:4]))
    return (None, "no Molecular Nodes biotite wheel with CCD payload found")


def _write_ccd_bytes(ccd_bytes: bytes, target_paths: list[Path], source_label: str) -> Path | None:
    if not ccd_bytes:
        return None
    written_path: Path | None = None
    for target in target_paths:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(ccd_bytes)
            if target.is_file():
                if written_path is None:
                    written_path = target
                if _env_bool("PRESS_DEBUG_LOADER", False):
                    print(f"Bridge Custom loader: restored CCD from {source_label} -> {target}")
        except Exception:
            continue
    if written_path is not None:
        _activate_biotite_ccd_path(written_path)
    return written_path


def _ensure_biotite_ccd_database() -> None:
    target_paths = _biotite_ccd_target_paths()
    existing_path: Path | None = None
    for path in target_paths:
        if path.is_file():
            existing_path = path
            break
    if existing_path is not None:
        try:
            # Mirror an existing CCD to all known paths so both legacy and
            # current Biotite/MN loaders succeed.
            _write_ccd_bytes(existing_path.read_bytes(), target_paths, f"existing:{existing_path}")
        except Exception:
            _activate_biotite_ccd_path(existing_path)
        return

    debug_notes: list[str] = []

    ccd_bytes, note = _read_packaged_biotite_ccd_bytes()
    if ccd_bytes:
        written = _write_ccd_bytes(ccd_bytes, target_paths, note)
        if written is not None:
            return
        debug_notes.append(f"packaged CCD read succeeded but write failed (targets={target_paths})")
    elif note:
        debug_notes.append(note)

    wheel_bytes, wheel_note = _read_biotite_ccd_bytes_from_wheels()
    if wheel_bytes:
        written = _write_ccd_bytes(wheel_bytes, target_paths, wheel_note)
        if written is not None:
            return
        debug_notes.append(f"wheel CCD read succeeded but write failed (targets={target_paths})")
    elif wheel_note:
        debug_notes.append(wheel_note)

    setup_exc: Exception | None = None
    try:
        from biotite.setup_ccd import main as setup_ccd_main  # type: ignore
    except Exception as exc:
        setup_exc = exc
    else:
        setup_ccd_main()
        for path in target_paths:
            if path.is_file():
                _activate_biotite_ccd_path(path)
                return
        debug_notes.append("biotite.setup_ccd executed but CCD file still not found at expected targets")

    details = ""
    if debug_notes:
        details = "\nRecovery attempts:\n- " + "\n- ".join(debug_notes[:8])
    if setup_exc is not None:
        details += f"\nsetup_ccd import error: {type(setup_exc).__name__}: {setup_exc}"
    raise RuntimeError(
        "Molecular Nodes requested CCD data but automatic CCD recovery failed."
        f"{details}"
    )


def _load_molecule_with_fallback(structure_path: Path, object_name: str):
    _configure_molecularnodes_import_path()
    _ensure_databpy_compat()
    _ensure_biotite_pdbx_compat()
    try:
        import molecularnodes as mn
    except Exception as exc:
        raise RuntimeError(f"Molecular Nodes import failed: {exc}") from exc

    _ensure_molecularnodes_ready(mn)

    loader_attempts = []
    loader_errors: list[str] = []

    def _record_loader_error(loader_name: str, exc: Exception) -> None:
        message = f"{loader_name}: {type(exc).__name__}: {exc}"
        loader_errors.append(message)
        if _env_bool("PRESS_DEBUG_LOADER", False):
            print(f"Bridge Custom loader error: {message}")

    def _finalize_loaded(loader_name: str, loaded_obj: Any):
        if _env_bool("PRESS_DEBUG_LOADER", False):
            print(
                "Bridge Custom loader: "
                f"{loader_name} "
                f"type={type(loaded_obj).__name__} "
                f"select={int(bool(hasattr(loaded_obj, 'select')))} "
                f"add_style={int(bool(hasattr(loaded_obj, 'add_style')))} "
                f"create_model={int(bool(hasattr(loaded_obj, 'create_model')))}"
            )
        return _as_style_molecule(loaded_obj, object_name, structure_path)

    if hasattr(mn, "entities") and hasattr(mn.entities, "Molecule"):
        molecule_cls = mn.entities.Molecule
        if hasattr(molecule_cls, "load"):
            loader_attempts.append("mn.entities.Molecule.load")
            try:
                return _finalize_loaded(
                    "mn.entities.Molecule.load",
                    molecule_cls.load(str(structure_path), name=object_name),
                )
            except Exception as exc:
                _record_loader_error("mn.entities.Molecule.load", exc)
                pass

    try:
        from molecularnodes.entities import Molecule as legacy_molecule_cls  # type: ignore

        if hasattr(legacy_molecule_cls, "load"):
            loader_attempts.append("molecularnodes.entities.Molecule.load")
            try:
                return _finalize_loaded(
                    "molecularnodes.entities.Molecule.load",
                    legacy_molecule_cls.load(str(structure_path), name=object_name),
                )
            except Exception as exc:
                _record_loader_error("molecularnodes.entities.Molecule.load", exc)
                pass
    except Exception:
        pass

    entities_ns = getattr(mn, "entities", None)
    entities_load_local = getattr(entities_ns, "load_local", None) if entities_ns is not None else None
    if callable(entities_load_local):
        loader_attempts.append("mn.entities.load_local")
        for kwargs in (
            {"style": "spheres"},
            {"style": "cartoon"},
            {},
        ):
            try:
                return _finalize_loaded("mn.entities.load_local", entities_load_local(str(structure_path), **kwargs))
            except TypeError:
                continue
            except Exception as exc:
                _record_loader_error("mn.entities.load_local", exc)
                break

    io_ns = getattr(mn, "io", None)
    if io_ns is not None:
        loader = getattr(io_ns, "load", None)
        if callable(loader):
            loader_attempts.append("mn.io.load")
            for kwargs in ({"name": object_name, "style": "spheres"}, {"name": object_name}, {}):
                try:
                    return _finalize_loaded("mn.io.load", loader(str(structure_path), **kwargs))
                except TypeError:
                    continue
                except Exception as exc:
                    _record_loader_error("mn.io.load", exc)
                    break

        local_ns = getattr(io_ns, "local", None)
        local_loader = getattr(local_ns, "load", None) if local_ns is not None else None
        if callable(local_loader):
            loader_attempts.append("mn.io.local.load")
            for kwargs in ({"name": object_name, "style": "spheres"}, {"name": object_name}, {}):
                try:
                    return _finalize_loaded("mn.io.local.load", local_loader(str(structure_path), **kwargs))
                except TypeError:
                    continue
                except Exception as exc:
                    _record_loader_error("mn.io.local.load", exc)
                    break

    load_ns = getattr(mn, "load", None)
    if load_ns is not None:
        for attr in ("molecule_local", "load_molecule_local"):
            loader = getattr(load_ns, attr, None)
            if loader is None:
                continue
            loader_attempts.append(f"mn.load.{attr}")
            for kwargs in ({"name": object_name, "starting_style": 1}, {"name": object_name}):
                try:
                    return _finalize_loaded(f"mn.load.{attr}", loader(str(structure_path), **kwargs))
                except TypeError:
                    continue
                except Exception as exc:
                    _record_loader_error(f"mn.load.{attr}", exc)
                    break

    top_level_load_local = getattr(mn, "load_local", None)
    if callable(top_level_load_local):
        loader_attempts.append("mn.load_local")
        for kwargs in ({"name": object_name, "style": "spheres"}, {"name": object_name}, {}):
            try:
                return _finalize_loaded("mn.load_local", top_level_load_local(str(structure_path), **kwargs))
            except TypeError:
                continue
            except Exception as exc:
                _record_loader_error("mn.load_local", exc)
                break

    # Lowest-priority fallback: parser APIs. These tend to require compatibility
    # shims and may degrade style fidelity compared with entity/load APIs.
    if io_ns is not None:
        parse_ns = getattr(io_ns, "parse", None)
        if parse_ns is not None:
            for parser_name in ("MMCIF", "BCIF", "CIF", "PDB"):
                parser_cls = getattr(parse_ns, parser_name, None)
                if parser_cls is None:
                    continue
                loader_attempts.append(f"mn.io.parse.{parser_name}")
                try:
                    return _finalize_loaded(f"mn.io.parse.{parser_name}", parser_cls(str(structure_path)))
                except Exception as exc:
                    _record_loader_error(f"mn.io.parse.{parser_name}", exc)
                    continue

    details = ""
    if loader_errors:
        details = "\nLoader errors:\n- " + "\n- ".join(loader_errors[-8:])
    raise RuntimeError(
        f"No compatible Molecular Nodes loader found. Attempted: {', '.join(loader_attempts)}{details}"
    )


def _resolve_loaded_object(load_result: Any, object_name: str, *, allow_missing: bool = False) -> bpy.types.Object | None:
    if load_result is not None:
        if hasattr(load_result, "type") and hasattr(load_result, "name"):
            return load_result
        if hasattr(load_result, "object") and getattr(load_result, "object") is not None:
            return getattr(load_result, "object")

    obj = bpy.data.objects.get(object_name)
    if obj is None and not allow_missing:
        raise RuntimeError(f"Molecule object {object_name!r} was not created.")
    return obj


def _clear_scene_lights() -> None:
    for obj in list(bpy.data.objects):
        if obj.type == "LIGHT":
            bpy.data.objects.remove(obj, do_unlink=True)


def _configure_renderer(scene: bpy.types.Scene, style_doc: dict[str, Any]) -> None:
    output = style_doc["output"]
    renderer = style_doc["renderer"]

    engine = str(renderer.get("engine", "CYCLES")).upper()
    if engine == "EEVEE":
        engine = "BLENDER_EEVEE"

    scene.render.engine = engine
    scene.render.resolution_x = int(output["width"])
    scene.render.resolution_y = int(output["height"])
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = str(output.get("format", "PNG")).upper()
    scene.render.film_transparent = bool(renderer.get("transparent_background", False))
    if isinstance(renderer.get("exposure"), (int, float)):
        scene.view_settings.exposure = float(renderer["exposure"])
    if isinstance(renderer.get("gamma"), (int, float)):
        scene.view_settings.gamma = float(renderer["gamma"])
    if isinstance(renderer.get("view_transform"), str):
        scene.view_settings.view_transform = str(renderer["view_transform"])
    if isinstance(renderer.get("look"), str):
        scene.view_settings.look = str(renderer["look"])

    if engine == "CYCLES" and hasattr(scene, "cycles"):
        scene.cycles.samples = int(output.get("samples", 128))
        scene.cycles.device = str(renderer.get("device", "GPU")).upper()
        scene.cycles.use_adaptive_sampling = bool(renderer.get("adaptive_sampling", True))
        try:
            scene.cycles.seed = int(renderer.get("seed", 0))
        except Exception:
            pass
        if hasattr(scene.cycles, "use_animated_seed"):
            try:
                scene.cycles.use_animated_seed = False
            except Exception:
                pass
        # Some host Blender builds are compiled without OIDN/OptiX denoiser.
        # Keep denoising disabled by default unless style explicitly asks for it.
        denoise_enabled = bool(renderer.get("denoise", False))
        if hasattr(scene.cycles, "use_denoising"):
            try:
                scene.cycles.use_denoising = denoise_enabled
            except Exception:
                pass
        if hasattr(scene.cycles, "use_preview_denoising"):
            try:
                scene.cycles.use_preview_denoising = denoise_enabled
            except Exception:
                pass
        for view_layer in getattr(scene, "view_layers", []):
            cycles_layer = getattr(view_layer, "cycles", None)
            if cycles_layer is None:
                continue
            if hasattr(cycles_layer, "use_denoising"):
                try:
                    cycles_layer.use_denoising = denoise_enabled
                except Exception:
                    pass
        _configure_cycles_gpu_for_custom(scene, renderer)


def _configure_raw_viewport_mode(scene: bpy.types.Scene) -> None:
    # Emulate Blender's live viewport appearance instead of Cycles/EEVEE ray tracing.
    scene.render.engine = "BLENDER_WORKBENCH"
    display = getattr(scene, "display", None)
    shading = getattr(display, "shading", None) if display is not None else None
    if shading is None:
        return
    try:
        shading.type = "SOLID"
    except Exception:
        pass
    try:
        shading.light = "STUDIO"
    except Exception:
        pass
    try:
        shading.color_type = "MATERIAL"
    except Exception:
        pass
    try:
        shading.show_cavity = True
    except Exception:
        pass
    try:
        shading.show_object_outline = False
    except Exception:
        pass


def _render_raw_view(scene: bpy.types.Scene) -> None:
    _configure_raw_viewport_mode(scene)
    try:
        bpy.ops.render.opengl(animation=False, write_still=True, view_context=False)
        return
    except TypeError:
        # Blender API signatures vary slightly by release.
        try:
            bpy.ops.render.opengl(write_still=True)
            return
        except Exception:
            pass
    except Exception:
        pass
    # Fallback: still non-raytraced output via Workbench render pipeline.
    bpy.ops.render.render(animation=False, write_still=True)


def _set_world(scene: bpy.types.Scene, world_cfg: dict[str, Any]) -> None:
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    world = scene.world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (480, 0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (280, 0)
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bg.inputs["Strength"].default_value = float(world_cfg.get("strength", 1.0))

    background_type = str(world_cfg.get("background_type", "solid")).lower()
    if background_type == "gradient":
        top_color = _hex_to_rgba(str(world_cfg.get("top_color", "#DDEAF5")))
        bottom_color = _hex_to_rgba(str(world_cfg.get("bottom_color", "#C9D9E8")))

        coord = nt.nodes.new("ShaderNodeTexCoord")
        coord.location = (-540, 0)
        separate = nt.nodes.new("ShaderNodeSeparateXYZ")
        separate.location = (-320, 0)
        ramp = nt.nodes.new("ShaderNodeValToRGB")
        ramp.location = (-80, 0)

        ramp.color_ramp.elements[0].position = 0.0
        ramp.color_ramp.elements[0].color = bottom_color
        ramp.color_ramp.elements[1].position = 1.0
        ramp.color_ramp.elements[1].color = top_color

        nt.links.new(coord.outputs["Window"], separate.inputs["Vector"])
        nt.links.new(separate.outputs["Y"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    else:
        color = _hex_to_rgba(str(world_cfg.get("color", "#DDEAF5")))
        bg.inputs["Color"].default_value = color


def _add_lights(
    scene: bpy.types.Scene,
    lights_cfg: list[dict[str, Any]],
    *,
    target: Vector | None = None,
    radius: float | None = None,
    pymol_up_axis: Vector | None = None,
    yaw_offset_deg: float = 0.0,
) -> None:
    _clear_scene_lights()
    if target is None:
        target = Vector((0.0, 0.0, 0.0))
    effective_radius = max(1e-6, float(radius if radius is not None else 1.0))

    for idx, cfg in enumerate(lights_cfg):
        light_type = str(cfg.get("type", "AREA")).upper()
        light_name = str(cfg.get("name", f"BridgeLight_{idx + 1}"))
        light_data = bpy.data.lights.new(name=light_name, type=light_type)
        light_obj = bpy.data.objects.new(light_name, light_data)
        scene.collection.objects.link(light_obj)

        light_data.energy = float(cfg.get("energy", 1000.0))
        if hasattr(light_data, "size"):
            if "size_scale" in cfg:
                size_scale = float(cfg.get("size_scale", 1.0))
                min_size = float(cfg.get("min_size", 0.0))
                light_data.size = max(min_size, effective_radius * size_scale)
            elif "size" in cfg:
                light_data.size = float(cfg["size"])
        if "color" in cfg:
            light_data.color = _hex_to_rgba(str(cfg["color"]), alpha=1.0)[:3]

        relative_vec = cfg.get("relative_vector")
        if isinstance(relative_vec, (list, tuple)) and len(relative_vec) == 3:
            dist_scale = float(cfg.get("distance_scale", 1.0))
            rel = Vector((float(relative_vec[0]), float(relative_vec[1]), float(relative_vec[2])))
            rotate_with_pymol_yaw = bool(cfg.get("rotate_with_pymol_yaw", False))
            if (
                rotate_with_pymol_yaw
                and pymol_up_axis is not None
                and pymol_up_axis.length > 1e-8
                and abs(float(yaw_offset_deg)) > 1e-8
            ):
                rel = Matrix.Rotation(
                    math.radians(float(yaw_offset_deg)),
                    3,
                    pymol_up_axis.normalized(),
                ) @ rel
            light_obj.location = target + (rel * (effective_radius * dist_scale))
            look_target = bool(cfg.get("look_at_target", True))
        else:
            location = cfg.get("location", [0.0, 0.0, 5.0])
            light_obj.location = Vector((float(location[0]), float(location[1]), float(location[2])))
            look_target = bool(cfg.get("look_at_target", False))

        rotation = cfg.get("rotation_deg")
        if isinstance(rotation, (list, tuple)) and len(rotation) == 3:
            light_obj.rotation_euler = Vector(
                (
                    math.radians(float(rotation[0])),
                    math.radians(float(rotation[1])),
                    math.radians(float(rotation[2])),
                )
            )
        elif look_target:
            delta = target - light_obj.location
            if delta.length > 1e-8:
                light_obj.rotation_euler = delta.to_track_quat("-Z", "Y").to_euler()


def _set_bsdf_input(node: bpy.types.ShaderNodeBsdfPrincipled, names: tuple[str, ...], value: Any) -> None:
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            sock.default_value = value
            return


def _create_material(name: str, cfg: dict[str, Any]) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nt = material.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (380, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (120, 0)

    base_color = str(cfg.get("base_color", "#A9C7E7"))
    shadow_color = str(cfg.get("shadow_color", base_color))
    highlight_color = str(cfg.get("highlight_color", base_color))
    ao_strength = float(cfg.get("ao_strength", 0.0))
    rim_strength = float(cfg.get("rim_strength", 0.0))
    alpha = float(cfg.get("alpha", 1.0))
    use_clay = (
        "shadow_color" in cfg
        or "highlight_color" in cfg
        or "ao_strength" in cfg
        or "rim_strength" in cfg
    )
    if use_clay:
        ao = nt.nodes.new("ShaderNodeAmbientOcclusion")
        ao.location = (-500, -140)
        if ao.inputs.get("Distance") is not None:
            ao.inputs["Distance"].default_value = float(cfg.get("ao_distance", 0.95))

        one_minus_ao = nt.nodes.new("ShaderNodeMath")
        one_minus_ao.location = (-320, -140)
        one_minus_ao.operation = "SUBTRACT"
        one_minus_ao.inputs[0].default_value = 1.0
        nt.links.new(ao.outputs["AO"], one_minus_ao.inputs[1])

        ao_mul = nt.nodes.new("ShaderNodeMath")
        ao_mul.location = (-140, -140)
        ao_mul.operation = "MULTIPLY"
        ao_mul.inputs[1].default_value = ao_strength
        nt.links.new(one_minus_ao.outputs["Value"], ao_mul.inputs[0])

        mix_shadow = nt.nodes.new("ShaderNodeMixRGB")
        mix_shadow.location = (20, 80)
        mix_shadow.blend_type = "MIX"
        mix_shadow.inputs["Color1"].default_value = _hex_to_rgba(base_color)
        mix_shadow.inputs["Color2"].default_value = _hex_to_rgba(shadow_color)
        nt.links.new(ao_mul.outputs["Value"], mix_shadow.inputs["Fac"])

        layer_weight = nt.nodes.new("ShaderNodeLayerWeight")
        layer_weight.location = (-500, 120)
        if layer_weight.inputs.get("Blend") is not None:
            layer_weight.inputs["Blend"].default_value = float(cfg.get("rim_blend", 0.25))

        inv_facing = nt.nodes.new("ShaderNodeMath")
        inv_facing.location = (-320, 120)
        inv_facing.operation = "SUBTRACT"
        inv_facing.inputs[0].default_value = 1.0
        nt.links.new(layer_weight.outputs["Facing"], inv_facing.inputs[1])

        rim_pow = nt.nodes.new("ShaderNodeMath")
        rim_pow.location = (-140, 120)
        rim_pow.operation = "POWER"
        rim_pow.inputs[1].default_value = float(cfg.get("rim_power", 2.8))
        nt.links.new(inv_facing.outputs["Value"], rim_pow.inputs[0])

        rim_mul = nt.nodes.new("ShaderNodeMath")
        rim_mul.location = (20, 120)
        rim_mul.operation = "MULTIPLY"
        rim_mul.inputs[1].default_value = rim_strength
        nt.links.new(rim_pow.outputs["Value"], rim_mul.inputs[0])

        mix_rim = nt.nodes.new("ShaderNodeMixRGB")
        mix_rim.location = (180, 20)
        mix_rim.blend_type = "MIX"
        mix_rim.inputs["Color2"].default_value = _hex_to_rgba(highlight_color)
        nt.links.new(mix_shadow.outputs["Color"], mix_rim.inputs["Color1"])
        nt.links.new(rim_mul.outputs["Value"], mix_rim.inputs["Fac"])
        nt.links.new(mix_rim.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = _hex_to_rgba(base_color, alpha=1.0)

    emission_color = cfg.get("emission_color")
    emission_strength = float(cfg.get("emission_strength", 0.0))
    if not isinstance(emission_color, str):
        emission_color = highlight_color

    _set_bsdf_input(bsdf, ("Roughness",), float(cfg.get("roughness", 0.45)))
    _set_bsdf_input(bsdf, ("Specular IOR Level", "Specular"), float(cfg.get("specular", 0.25)))
    _set_bsdf_input(bsdf, ("Transmission Weight", "Transmission"), float(cfg.get("transmission", 0.0)))
    _set_bsdf_input(bsdf, ("Coat Weight", "Coat"), float(cfg.get("coat", 0.0)))
    _set_bsdf_input(bsdf, ("Alpha",), alpha)
    if emission_strength > 0:
        _set_bsdf_input(bsdf, ("Emission Color", "Emission"), _hex_to_rgba(str(emission_color), 1.0))
        _set_bsdf_input(bsdf, ("Emission Strength",), emission_strength)

    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    if hasattr(material, "blend_method"):
        material.blend_method = "HASHED" if alpha < 0.999 else "OPAQUE"
    if hasattr(material, "shadow_method"):
        material.shadow_method = "NONE" if alpha < 0.999 else "OPAQUE"

    return material


def _build_materials(materials_cfg: dict[str, dict[str, Any]]) -> dict[str, bpy.types.Material]:
    return {name: _create_material(name, cfg) for name, cfg in materials_cfg.items()}


def _apply_material_fallback(obj: bpy.types.Object, material: bpy.types.Material) -> None:
    data = getattr(obj, "data", None)
    if data is None:
        return
    materials = getattr(data, "materials", None)
    if materials is None:
        return
    if len(materials) == 0:
        materials.append(material)
    else:
        for idx in range(len(materials)):
            materials[idx] = material


def _object_modifier_names(obj: bpy.types.Object | None) -> list[str]:
    if obj is None:
        return []
    names: list[str] = []
    for modifier in getattr(obj, "modifiers", []):
        name = str(getattr(modifier, "name", "")).strip()
        if name:
            names.append(name)
    return names


def _set_numeric_socket_default(socket: Any, value: float) -> bool:
    try:
        current = socket.default_value
    except Exception:
        return False
    try:
        if isinstance(current, (int, float)):
            socket.default_value = float(value)
            return True
        if isinstance(current, (list, tuple)) and current:
            updated = list(current)
            updated[0] = float(value)
            socket.default_value = type(current)(updated)
            return True
    except Exception:
        return False
    return False


def _is_sticks_atom_sphere_socket_name(socket_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(socket_name).strip().lower()).strip()
    if not normalized:
        return False
    tokens = set(normalized.split())
    if tokens.intersection({"sphere", "spheres", "ball", "balls", "vdw"}):
        return True
    if tokens.intersection({"atom", "atoms"}):
        if tokens.intersection({"radius", "scale", "size"}):
            return True
        if tokens.intersection({"show", "display", "enable", "enabled", "visible", "visibility"}):
            return True
    return False


def _tune_native_sticks_style(
    obj: bpy.types.Object,
    stick_radius: float | None,
    stick_quality: Any = None,
    stick_shade_smooth: Any = None,
    stick_color_blur: Any = None,
    disable_atom_spheres: bool = True,
    modifier_names: list[str] | None = None,
) -> int:
    """
    Molecular Nodes native sticks do not always honor radius/quality kwargs at
    create time. Apply the requested values directly on the GN style node.
    """
    changed = 0
    desired_quality = None
    if stick_quality is not None:
        try:
            quality = float(stick_quality)
            if math.isfinite(quality):
                desired_quality = max(1.0, quality)
        except Exception:
            desired_quality = None
    desired_shade_smooth = bool(stick_shade_smooth) if stick_shade_smooth is not None else None
    desired_color_blur = bool(stick_color_blur) if stick_color_blur is not None else None

    modifier_filter = {str(item) for item in modifier_names} if modifier_names else None
    debug_stick_sockets = _env_bool("PRESS_DEBUG_STICK_SOCKETS", False)

    for modifier in getattr(obj, "modifiers", []):
        if modifier_filter is not None and str(getattr(modifier, "name", "")) not in modifier_filter:
            continue
        if modifier.type != "NODES" or modifier.node_group is None:
            continue
        for node in modifier.node_group.nodes:
            node_name = str(getattr(node, "name", "")).strip().lower()
            if "stick" not in node_name:
                continue
            for socket in node.inputs:
                socket_name = str(getattr(socket, "name", "")).strip().lower()
                sphere_socket = _is_sticks_atom_sphere_socket_name(socket_name)
                if debug_stick_sockets:
                    print(
                        "Bridge Custom sticks socket: "
                        f"node={node_name} socket={socket_name} sphere_socket={int(sphere_socket)} "
                        f"value={getattr(socket, 'default_value', '<unreadable>')!r}"
                    )
                if disable_atom_spheres and sphere_socket:
                    default_value = getattr(socket, "default_value", None)
                    if isinstance(default_value, bool):
                        try:
                            socket.default_value = False
                            changed += 1
                        except Exception:
                            pass
                    elif isinstance(default_value, (int, float)):
                        if _set_numeric_socket_default(socket, 0.0):
                            changed += 1
                    continue
                if stick_radius is not None and "radius" in socket_name:
                    if _set_numeric_socket_default(socket, float(stick_radius)):
                        changed += 1
                    continue
                if desired_quality is not None and "quality" in socket_name:
                    if _set_numeric_socket_default(socket, float(desired_quality)):
                        changed += 1
                    continue
                if desired_shade_smooth is not None and "shade smooth" in socket_name:
                    try:
                        socket.default_value = desired_shade_smooth
                        changed += 1
                    except Exception:
                        pass
                    continue
                if desired_color_blur is not None and "color blur" in socket_name:
                    try:
                        socket.default_value = desired_color_blur
                        changed += 1
                    except Exception:
                        pass
                    continue
    return changed


def _socket_enables_feature(default_value: Any) -> bool:
    if isinstance(default_value, bool):
        return bool(default_value)
    if isinstance(default_value, (int, float)):
        try:
            return float(default_value) > 1e-6
        except Exception:
            return False
    if isinstance(default_value, (list, tuple)) and default_value:
        first = default_value[0]
        if isinstance(first, bool):
            return bool(first)
        if isinstance(first, (int, float)):
            try:
                return float(first) > 1e-6
            except Exception:
                return False
    return False


def _native_sticks_atom_spheres_enabled(
    obj: bpy.types.Object,
    *,
    modifier_names: list[str] | None = None,
) -> bool:
    modifier_filter = {str(item) for item in modifier_names} if modifier_names else None
    for modifier in getattr(obj, "modifiers", []):
        if modifier_filter is not None and str(getattr(modifier, "name", "")) not in modifier_filter:
            continue
        if modifier.type != "NODES" or modifier.node_group is None:
            continue
        for node in modifier.node_group.nodes:
            node_name = str(getattr(node, "name", "")).strip().lower()
            if "stick" not in node_name:
                continue
            for socket in node.inputs:
                socket_name = str(getattr(socket, "name", "")).strip().lower()
                if not _is_sticks_atom_sphere_socket_name(socket_name):
                    continue
                default_value = getattr(socket, "default_value", None)
                if _socket_enables_feature(default_value):
                    return True
    return False


def _assign_material_to_style_nodes(
    obj: bpy.types.Object,
    material: bpy.types.Material,
    *,
    modifier_names: list[str] | None = None,
) -> bool:
    assigned = 0
    modifier_filter = {str(item) for item in modifier_names} if modifier_names else None
    for modifier in obj.modifiers:
        if modifier_filter is not None and str(getattr(modifier, "name", "")) not in modifier_filter:
            continue
        if modifier.type != "NODES" or modifier.node_group is None:
            continue
        for node in modifier.node_group.nodes:
            for socket in getattr(node, "inputs", []):
                try:
                    socket_name = str(getattr(socket, "name", "")).strip().lower()
                    socket_type = str(getattr(socket, "type", "")).upper()
                    socket_id = str(getattr(socket, "bl_idname", "")).upper()
                    if "material" in socket_name or "MATERIAL" in socket_type or "MATERIAL" in socket_id:
                        socket.default_value = material
                        assigned += 1
                except Exception:
                    continue
        try:
            interface = getattr(modifier.node_group, "interface", None)
            items = list(getattr(interface, "items_tree", [])) if interface is not None else []
            for item in items:
                if str(getattr(item, "item_type", "")).upper() != "SOCKET":
                    continue
                if str(getattr(item, "in_out", "")).upper() not in {"INPUT", ""}:
                    continue
                socket_type = str(getattr(item, "socket_type", "")).upper()
                if "MATERIAL" not in socket_type:
                    continue
                identifier = str(getattr(item, "identifier", "")).strip()
                if not identifier:
                    continue
                modifier[identifier] = material
                assigned += 1
        except Exception:
            pass
    return assigned > 0


def _selector_call(selector: Any, names: tuple[str, ...], *args: Any) -> tuple[Any, bool]:
    for name in names:
        fn = getattr(selector, name, None)
        if not callable(fn):
            continue
        try:
            return fn(*args), True
        except Exception:
            continue
    return selector, False


def _build_selection(
    load_result: Any,
    selection_cfg: dict[str, Any],
    *,
    structure_path: Path,
    style_name: str,
) -> tuple[Any, list[int] | None]:
    indices = _selection_indices_from_cfg(
        load_result,
        selection_cfg,
        structure_path=structure_path,
    )
    if indices is not None:
        return _selection_to_named_attribute(
            load_result,
            indices,
            style_name=style_name,
        ), indices

    selector = getattr(load_result, "select", None)
    if selector is None:
        return None, None

    reset_fn = getattr(selector, "reset", None)
    if callable(reset_fn):
        selector = reset_fn()

    if selection_cfg.get("polymer"):
        selector, _ = _selector_call(selector, ("is_polymer", "polymer"))
    if selection_cfg.get("nucleic"):
        selector, _ = _selector_call(selector, ("is_nucleic", "is_nucleic_acid", "nucleic"))
    if selection_cfg.get("ligand"):
        selector, _ = _selector_call(selector, ("is_ligand", "ligand"))

    chain_value = selection_cfg.get("chain")
    if chain_value:
        chain_arg: Any = chain_value
        if isinstance(chain_value, str) and "," in chain_value:
            chain_arg = [item.strip() for item in chain_value.split(",") if item.strip()]
        selector, _ = _selector_call(selector, ("chain", "chain_id", "is_chain"), chain_arg)

    residue_range = selection_cfg.get("residue_range")
    if residue_range:
        start, end = int(residue_range[0]), int(residue_range[1])
        selector, ok = _selector_call(selector, ("residue_range", "res_id_range", "residue_id_range"), start, end)
        if not ok:
            values = list(range(start, end + 1 if end >= start else start + 1))
            selector, _ = _selector_call(selector, ("res_id", "residue_id"), values)

    return selector, None


def _is_native_mn_molecule(load_result: Any) -> bool:
    if isinstance(load_result, (_ParserMolecule, _CompatMolecule)):
        return False
    return bool(hasattr(load_result, "add_style") and hasattr(load_result, "tree"))


def _native_style_from_cfg(style_cfg: dict[str, Any]) -> Any:
    try:
        import molecularnodes.nodes.styles as mn_styles  # type: ignore
    except Exception:
        return str(style_cfg.get("style", "spheres"))

    style_kind = str(style_cfg.get("style", "spheres")).strip().lower()
    if style_kind == "cartoon":
        return mn_styles.StyleCartoon(
            quality=int(style_cfg.get("quality", 2)),
            peptide_dssp=True,
            peptide_cylinders=bool(style_cfg.get("peptide_cylinders", False)),
            peptide_arrows=False,
            peptide_rounded=bool(style_cfg.get("peptide_rounded", False)),
            peptide_smoothing=float(style_cfg.get("peptide_smoothing", 0.5)),
            peptide_thickness=float(style_cfg.get("thickness", 0.6)),
            peptide_width=float(style_cfg.get("width", 2.2)),
            peptide_loop_radius=float(style_cfg.get("loop_radius", 0.3)),
            nucleic_thickness=float(style_cfg.get("nucleic_thickness", 1.0)),
            nucleic_width=float(style_cfg.get("nucleic_width", 3.0)),
            nucleic_radius=float(style_cfg.get("nucleic_radius", 2.0)),
            backbone_shape=style_cfg.get("backbone_shape", "Cylinder"),
            base_shape=style_cfg.get("base_shape", "Rectangle"),
            base_realize=bool(style_cfg.get("base_realize", False)),
            color_blur=bool(style_cfg.get("color_blur", False)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
        )
    if style_kind == "sticks":
        sticks_radius = style_cfg.get("sticks_radius", style_cfg.get("radius", 0.2))
        sticks_quality = style_cfg.get("sticks_quality", style_cfg.get("quality", 3))
        return mn_styles.StyleSticks(
            quality=int(sticks_quality),
            radius=float(sticks_radius),
            color_blur=bool(style_cfg.get("color_blur", False)),
            shade_smooth=bool(style_cfg.get("sticks_shade_smooth", style_cfg.get("shade_smooth", True))),
        )
    if style_kind == "spheres":
        return mn_styles.StyleSpheres(
            radius=float(style_cfg.get("radius", 0.8)),
            subdivisions=int(style_cfg.get("subdivisions", 2)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
        )
    if style_kind == "ribbon":
        return mn_styles.StyleRibbon(
            quality=int(style_cfg.get("quality", 3)),
            backbone_radius=float(style_cfg.get("radius", 1.6)),
            backbone_width=float(style_cfg.get("width", 3.0)),
            backbone_thickness=float(style_cfg.get("thickness", 1.0)),
            color_blur=bool(style_cfg.get("color_blur", False)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
        )
    if style_kind == "surface":
        return mn_styles.StyleSurface(
            quality=int(style_cfg.get("quality", 3)),
            probe_size=float(style_cfg.get("probe_radius", 1.0)),
            shade_smooth=bool(style_cfg.get("shade_smooth", True)),
            color_blur=int(2 if bool(style_cfg.get("color_blur", False)) else 0),
        )
    if style_kind == "wire":
        return mn_styles.StyleDensityWire(
            wire_radius=float(style_cfg.get("radius", 1.0)),
            wire_resolution=int(style_cfg.get("quality", 3)),
        )
    return style_kind


def _add_style(
    load_result: Any,
    style_cfg: dict[str, Any],
    selection: Any,
    material: bpy.types.Material | None = None,
) -> Any:
    if not hasattr(load_result, "add_style"):
        return None

    style_kind = str(style_cfg["style"])
    if _is_native_mn_molecule(load_result):
        native_style = _native_style_from_cfg(style_cfg)
        attempts = []
        base_kwargs: dict[str, Any] = {"color": None}
        style_name = str(style_cfg.get("name", "")).strip()
        if style_name:
            base_kwargs["name"] = style_name
        if material is not None:
            base_kwargs["material"] = material
        if selection is not None:
            attempts.append({"selection": selection, **base_kwargs})
            attempts.append({"selection": selection})
        attempts.append(base_kwargs)
        attempts.append({})

        errors: list[str] = []
        for kwargs in attempts:
            try:
                return load_result.add_style(native_style, **kwargs)
            except TypeError as exc:
                errors.append(str(exc))
                continue
            except Exception as exc:
                errors.append(str(exc))
                continue
        raise RuntimeError(
            f"Could not add native style {style_cfg.get('name', style_kind)!r}. "
            f"Last errors: {' | '.join(errors[-2:])}"
        )

    extra_kwargs: dict[str, Any] = {}
    for key in (
        "color",
        "radius",
        "probe_radius",
        "quality",
        "subdivisions",
        "thickness",
        "width",
        "loop_radius",
        "shade_smooth",
        "peptide_cylinders",
        "peptide_rounded",
        "peptide_smoothing",
        "nucleic_thickness",
        "nucleic_width",
        "nucleic_radius",
        "backbone_shape",
        "base_shape",
        "base_realize",
        "color_blur",
        "sticks_radius",
        "sticks_quality",
        "sticks_shade_smooth",
    ):
        if key in style_cfg:
            extra_kwargs[key] = style_cfg[key]
    if material is not None:
        extra_kwargs["material"] = material

    attempts = []
    if selection is not None:
        attempts.append({"selection": selection, **extra_kwargs})
        attempts.append({"selection": selection})
    attempts.append(extra_kwargs)
    attempts.append({})

    errors: list[str] = []
    for kwargs in attempts:
        try:
            return load_result.add_style(style_kind, **kwargs)
        except TypeError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:
            errors.append(str(exc))
            continue

    raise RuntimeError(
        f"Could not add style {style_cfg.get('name', style_kind)!r}. "
        f"Tried kwargs variants; last errors: {' | '.join(errors[-2:])}"
    )


def _assign_style_material(
    *,
    load_result: Any,
    base_object: bpy.types.Object | None,
    style_result: Any,
    material: bpy.types.Material,
    modifier_names: list[str] | None = None,
) -> None:
    style_obj = getattr(style_result, "_object", None) if style_result is not None else None
    if style_obj is not None:
        if _assign_material_to_style_nodes(style_obj, material):
            return
        _apply_material_fallback(style_obj, material)
        return

    target = base_object or _resolve_loaded_object(load_result, "BridgeCustom", allow_missing=True)
    if target is None:
        return
    if modifier_names is not None:
        if modifier_names:
            _assign_material_to_style_nodes(target, material, modifier_names=modifier_names)
        return
    if not _assign_material_to_style_nodes(target, material):
        _apply_material_fallback(target, material)


def _style_category(style_cfg: dict[str, Any]) -> str:
    selection = style_cfg.get("selection")
    if isinstance(selection, dict):
        if bool(selection.get("ligand")):
            return "ligand"
        if bool(selection.get("polymer")):
            return "polymer"
        if bool(selection.get("nucleic")):
            return "nucleic"
        query = str(selection.get("query", "")).lower()
        if "ligand" in query:
            return "ligand"
        if "polymer" in query:
            return "polymer"
        if "nucleic" in query and "not nucleic" not in query:
            return "nucleic"
    return "polymer"


def _style_chain_candidates(style_cfg: dict[str, Any]) -> list[str]:
    selection = style_cfg.get("selection")
    if not isinstance(selection, dict):
        return []
    chain_value = selection.get("chain")
    chain_ids: list[str] = []
    if isinstance(chain_value, str):
        chain_ids.extend([item.strip() for item in chain_value.split(",") if item.strip()])
    elif isinstance(chain_value, (list, tuple)):
        chain_ids.extend([str(item).strip() for item in chain_value if str(item).strip()])
    query = str(selection.get("query", ""))
    chain_ids.extend(re.findall(r"\bchain\s*=?\s*([A-Za-z0-9_]+)", query, flags=re.IGNORECASE))
    return [item.upper() for item in chain_ids if item]


def _is_polymer_cartoon_style(style_cfg: dict[str, Any]) -> bool:
    if str(style_cfg.get("style", "")).strip().lower() != "cartoon":
        return False
    selection = style_cfg.get("selection")
    if isinstance(selection, dict):
        if bool(selection.get("polymer")):
            return True
        query = str(selection.get("query", "")).strip().lower()
        if "polymer" in query and "not polymer" not in query:
            return True
    style_name = str(style_cfg.get("name", "")).strip().lower()
    return style_name in {"chain_a_cartoon", "chain_b_cartoon", "polymer_other_cartoon"}


def _is_ligand_style(style_cfg: dict[str, Any]) -> bool:
    style_name = str(style_cfg.get("name", "")).strip().lower()
    if "ligand" in style_name:
        return True
    selection = style_cfg.get("selection")
    if not isinstance(selection, dict):
        return False
    if bool(selection.get("ligand")):
        return True
    query = str(selection.get("query", "")).strip().lower()
    if "ligand" in query:
        return True
    if "resn ptr" in query:
        return True
    return False


def _protein_chain_ids(load_result: Any) -> list[str]:
    array = _get_atom_array(load_result)
    if array is None:
        return []
    try:
        count = int(array.array_length()) if hasattr(array, "array_length") else len(array)
    except Exception:
        return []
    if count <= 0:
        return []

    try:
        polymer_mask, nucleic_mask, _ = _semantic_masks(array, count)
        protein_mask = np.asarray(polymer_mask, dtype=bool) & ~np.asarray(nucleic_mask, dtype=bool)
    except Exception:
        return []

    if not bool(np.any(protein_mask)):
        return []

    chain_getter = getattr(load_result, "_chain_upper", None)
    bridge_override = getattr(load_result, "_bridge_chain_override_upper", None)
    if isinstance(bridge_override, np.ndarray) and bridge_override.shape[0] == count:
        chain_upper = np.asarray(bridge_override, dtype=object).astype(str)
    elif callable(chain_getter):
        try:
            chain_upper = np.asarray(chain_getter(count), dtype=object).astype(str)
        except Exception:
            chain_upper = _array_as_upper(getattr(array, "chain_id", np.array([""] * count, dtype=object)), count)
    else:
        chain_upper = _array_as_upper(getattr(array, "chain_id", np.array([""] * count, dtype=object)), count)

    seen: set[str] = set()
    ordered: list[str] = []
    for chain_id in chain_upper[protein_mask]:
        value = _strip_wrapping_quotes(str(chain_id)).strip().upper()
        if not value or value in {".", "?"}:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _auto_chain_fallback_query(style_cfg: dict[str, Any], chain_ids: list[str]) -> str | None:
    if not chain_ids:
        return None

    style_name = str(style_cfg.get("name", "")).strip().lower()
    if not style_name:
        return None

    if "chain_a" in style_name:
        if len(chain_ids) >= 1:
            return f"polymer and not nucleic and chain {chain_ids[0]}"
        return None
    if "chain_b" in style_name:
        if len(chain_ids) >= 2:
            return f"polymer and not nucleic and chain {chain_ids[1]}"
        return None
    if "polymer_other" in style_name:
        remaining = chain_ids[2:]
        if remaining:
            joined = ",".join(remaining)
            return f"polymer and not nucleic and chain {joined}"
        return None
    return None


def _apply_color_follow_to_materials(style_doc: dict[str, Any], view_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    materials_cfg = json.loads(json.dumps(style_doc.get("materials", {})))
    color_follow_cfg = style_doc.get("color_follow")
    if not isinstance(color_follow_cfg, dict):
        return materials_cfg
    if not bool(color_follow_cfg.get("enabled", False)):
        return materials_cfg

    color_hints = view_doc.get("color_hints")
    if not isinstance(color_hints, dict):
        return materials_cfg

    chain_hints = color_hints.get("chain")
    class_hints = color_hints.get("classes")
    if not isinstance(chain_hints, dict):
        chain_hints = {}
    if not isinstance(class_hints, dict):
        class_hints = {}

    mode = str(color_follow_cfg.get("mode", "auto")).strip().lower()
    mix = float(color_follow_cfg.get("mix", 0.65))
    sat_delta = float(color_follow_cfg.get("saturation_boost", 0.08))
    value_delta = float(color_follow_cfg.get("value_boost", 0.03))

    styles = style_doc.get("styles", [])
    if not isinstance(styles, list):
        return materials_cfg

    for style_cfg in styles:
        if not isinstance(style_cfg, dict):
            continue
        material_name = str(style_cfg.get("material", "")).strip()
        if not material_name or material_name not in materials_cfg:
            continue

        target_hex: str | None = None
        if mode in {"auto", "chain"}:
            for chain_id in _style_chain_candidates(style_cfg):
                value = chain_hints.get(chain_id) or chain_hints.get(chain_id.upper()) or chain_hints.get(chain_id.lower())
                if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                    target_hex = value
                    break

        if target_hex is None and mode in {"auto", "class"}:
            category = _style_category(style_cfg)
            value = class_hints.get(category)
            if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                target_hex = value

        if target_hex is None:
            continue

        material_cfg = materials_cfg.get(material_name)
        if not isinstance(material_cfg, dict):
            continue
        current_hex = str(material_cfg.get("base_color", "#A9C7E7"))
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", current_hex):
            current_hex = "#A9C7E7"

        base_rgb = _hex_to_rgb(current_hex)
        target_rgb = _hex_to_rgb(target_hex)
        mixed_rgb = _mix_rgb(base_rgb, target_rgb, mix=mix)
        tuned_rgb = _adjust_hsv(mixed_rgb, sat_delta=sat_delta, value_delta=value_delta)
        shadow_rgb = _shade_from_base(tuned_rgb, factor=0.72)
        highlight_rgb = _highlight_from_base(tuned_rgb, factor=0.20)

        material_cfg["base_color"] = _rgb_to_hex(tuned_rgb)
        material_cfg["shadow_color"] = _rgb_to_hex(shadow_rgb)
        material_cfg["highlight_color"] = _rgb_to_hex(highlight_rgb)
        if _env_bool("PRESS_COLOR_FOLLOW_LOG", False):
            print(
                "Bridge Custom: color-follow "
                f"material={material_name} target={target_hex} base={material_cfg['base_color']}"
            )

    return materials_cfg


def _camera_axis_map_matrix(axis_map: str, sx: float, sy: float, sz: float) -> Matrix:
    candidate = (axis_map or "XYZ").strip().upper()
    if len(candidate) != 3 or set(candidate) != {"X", "Y", "Z"}:
        candidate = "XYZ"
    axis_idx = {"X": 0, "Y": 1, "Z": 2}
    signs = (sx, sy, sz)
    rows = []
    for row_idx, axis_name in enumerate(candidate):
        row = [0.0, 0.0, 0.0]
        row[axis_idx[axis_name]] = signs[row_idx]
        rows.append(tuple(row))
    return Matrix(rows)


def _compute_camera_spec(view_doc: dict[str, Any]) -> tuple[list[list[float]], str, float, float | None, float, float]:
    pymol_view = view_doc.get("pymol_view")
    if not isinstance(pymol_view, list) or len(pymol_view) < 18:
        raise RuntimeError("view.json is missing a valid pymol_view list of 18 values.")

    values = [float(item) for item in pymol_view[:18]]
    if _env_bool("PRESS_PYMOL_VIEW_COLUMN_MAJOR", True):
        rot_world_to_cam = Matrix(
            (
                (values[0], values[3], values[6]),
                (values[1], values[4], values[7]),
                (values[2], values[5], values[8]),
            )
        )
    else:
        rot_world_to_cam = Matrix(
            (
                (values[0], values[1], values[2]),
                (values[3], values[4], values[5]),
                (values[6], values[7], values[8]),
            )
        )

    sx = -1.0 if _env_bool("PRESS_PYMOL_FLIP_X", False) else 1.0
    sy = -1.0 if _env_bool("PRESS_PYMOL_FLIP_Y", False) else 1.0
    sz = -1.0 if _env_bool("PRESS_PYMOL_FLIP_Z", False) else 1.0
    axis_map = os.getenv("PRESS_PYMOL_AXIS_MAP", "XYZ")
    cam_space_to_blender = _camera_axis_map_matrix(axis_map, sx, sy, sz)
    cam_to_world = rot_world_to_cam.transposed() @ cam_space_to_blender

    o_cam = Vector((values[9], values[10], values[11]))
    o_model = Vector((values[12], values[13], values[14]))
    cam_pos = o_model - (cam_to_world @ o_cam)

    matrix_world = [
        [cam_to_world[0][0], cam_to_world[0][1], cam_to_world[0][2], cam_pos.x],
        [cam_to_world[1][0], cam_to_world[1][1], cam_to_world[1][2], cam_pos.y],
        [cam_to_world[2][0], cam_to_world[2][1], cam_to_world[2][2], cam_pos.z],
        [0.0, 0.0, 0.0, 1.0],
    ]

    fov_raw = view_doc.get("fov_deg")
    try:
        fov_value = float(fov_raw) if fov_raw is not None else float(abs(values[17]))
    except Exception:
        fov_value = float(abs(values[17]))
    if not (5.0 <= fov_value <= 120.0):
        fov_value = 50.0

    front = max(1e-3, values[15])
    rear = max(front + 1e-3, values[16])
    ortho_raw = view_doc.get("orthoscopic")
    is_ortho = bool(ortho_raw) if ortho_raw is not None else values[17] <= 0.0
    ortho_scale = None
    if is_ortho:
        distance = max(1e-6, abs(values[11]))
        ortho_scale = 2.0 * distance * math.tan(math.radians(fov_value) * 0.5)

    return (matrix_world, "ORTHO" if is_ortho else "PERSP", fov_value, ortho_scale, front, rear)


def _ensure_camera(scene: bpy.types.Scene, style_doc: dict[str, Any], view_doc: dict[str, Any]) -> bpy.types.Object:
    matrix_world, camera_type, fov_deg, ortho_scale, clip_start, clip_end = _compute_camera_spec(view_doc)

    camera = scene.camera
    if camera is None or camera.type != "CAMERA":
        camera_data = bpy.data.cameras.new("PyMOL_Camera")
        camera = bpy.data.objects.new("PyMOL_Camera", camera_data)
        scene.collection.objects.link(camera)
        scene.camera = camera

    camera_cfg = style_doc.get("camera", {})
    world_scale = float(camera_cfg.get("world_scale", 0.0108))
    world_scale = max(1e-6, world_scale)

    matrix = Matrix(matrix_world)
    matrix.translation *= world_scale
    camera.matrix_world = matrix
    camera_data = camera.data
    camera_data.clip_start = max(1e-6, clip_start * world_scale)
    camera_data.clip_end = max(camera_data.clip_start + 1e-3, clip_end * world_scale)

    if camera_type == "ORTHO":
        camera_data.type = "ORTHO"
        if ortho_scale is not None:
            camera_data.ortho_scale = max(1e-6, ortho_scale * world_scale)
    else:
        camera_data.type = "PERSP"
        camera_data.angle = math.radians(fov_deg)

    camera_mode = str(camera_cfg.get("mode", "pymol")).strip().lower()
    override_intrinsics = bool(camera_cfg.get("override_intrinsics", False))
    if camera_mode == "override" or override_intrinsics:
        if isinstance(camera_cfg.get("lens_mm"), (int, float)):
            camera_data.lens = float(camera_cfg["lens_mm"])
        if isinstance(camera_cfg.get("shift_x"), (int, float)):
            camera_data.shift_x = float(camera_cfg["shift_x"])
        if isinstance(camera_cfg.get("shift_y"), (int, float)):
            camera_data.shift_y = float(camera_cfg["shift_y"])

    return camera


def _point_camera_at(camera_obj: bpy.types.Object, target: Vector) -> None:
    direction = target - camera_obj.location
    if direction.length < 1e-8:
        return
    camera_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _orient_camera_with_up(camera_obj: bpy.types.Object, forward: Vector, up_hint: Vector) -> bool:
    if forward.length < 1e-8 or up_hint.length < 1e-8:
        return False
    fwd = forward.normalized()
    up = up_hint.normalized()
    z_axis = -fwd
    x_axis = up.cross(z_axis)
    if x_axis.length < 1e-8:
        return False
    x_axis.normalize()
    y_axis = z_axis.cross(x_axis)
    if y_axis.length < 1e-8:
        return False
    y_axis.normalize()
    rot = Matrix((x_axis, y_axis, z_axis)).transposed()
    camera_obj.matrix_world = Matrix.Translation(camera_obj.location) @ rot.to_4x4()
    return True


def _object_has_renderable_geometry(obj: bpy.types.Object) -> bool:
    if obj is None:
        return False
    if getattr(obj, "hide_render", False):
        return False
    try:
        if obj.hide_get():
            return False
    except Exception:
        pass
    obj_type = getattr(obj, "type", "")
    if obj_type not in {"MESH", "CURVE", "SURFACE"}:
        return False
    if obj_type == "MESH":
        eval_count, eval_extent = _ParserMolecule._evaluated_mesh_stats(obj)
        if eval_count <= 0 or eval_extent < 1e-8:
            return False
    return True


def _scene_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, float]:
    all_corners: list[Vector] = []
    valid = [obj for obj in objects if _object_has_renderable_geometry(obj)]
    if not valid:
        valid = [obj for obj in bpy.data.objects if _object_has_renderable_geometry(obj)]
    if not valid:
        return Vector((0.0, 0.0, 0.0)), 1.0
    for obj in valid:
        try:
            all_corners.extend([obj.matrix_world @ Vector(corner) for corner in obj.bound_box])
        except Exception:
            continue
    if not all_corners:
        return Vector((0.0, 0.0, 0.0)), 1.0
    min_v = Vector((min(v.x for v in all_corners), min(v.y for v in all_corners), min(v.z for v in all_corners)))
    max_v = Vector((max(v.x for v in all_corners), max(v.y for v in all_corners), max(v.z for v in all_corners)))
    center = (min_v + max_v) * 0.5
    radius = max(1e-3, (max_v - min_v).length * 0.5)
    return center, radius


def _ligand_anchor_from_load_result(load_result: Any) -> Vector | None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return None
    count = _atom_array_count(atom_array)
    if count <= 0:
        return None
    polymer_mask, _, semantic_ligand_mask = _semantic_masks(atom_array, count)
    ligand_mask = _ligand_reference_mask_for_features(
        atom_array=atom_array,
        polymer_mask=polymer_mask,
        semantic_ligand_mask=semantic_ligand_mask,
    )
    if not bool(np.any(ligand_mask)):
        if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
            print("Bridge Custom: ligand anchor fallback found no ligand mask entries.")
        return None
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.shape[0] != count:
        if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
            raw_coord = getattr(atom_array, "coord", None)
            raw_shape = getattr(raw_coord, "shape", None)
            print(
                "Bridge Custom: ligand anchor missing coordinate array "
                f"coord_type={type(raw_coord).__name__} "
                f"coord_shape={raw_shape} count={count}"
            )
        return None
    ligand_coords = coords[ligand_mask]
    finite = np.isfinite(ligand_coords).all(axis=1)
    if not bool(np.any(finite)):
        return None
    center = ligand_coords[finite].mean(axis=0)
    return Vector((float(center[0]), float(center[1]), float(center[2])))


def _ligand_cutout_axis(
    ligand_anchor: Vector,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    *,
    prefer_camera: bool = False,
    flatten_z: bool = True,
) -> Vector:
    axis = Vector((0.0, 1.0, 0.0))
    ligand_axis = None
    if interface_center is not None:
        try:
            candidate_axis = ligand_anchor - interface_center
            if candidate_axis.length > 1e-8:
                ligand_axis = candidate_axis.normalized()
        except Exception:
            pass
    camera_norm = None
    if camera_axis is not None and camera_axis.length > 1e-8:
        try:
            camera_norm = camera_axis.normalized()
        except Exception:
            camera_norm = None

    if prefer_camera:
        if camera_norm is not None:
            axis = camera_norm
        elif ligand_axis is not None:
            axis = ligand_axis
    else:
        # Prefer interface-driven axis so cut direction tracks pocket geometry.
        # Camera-forward fallback can flip to an exterior-facing carve.
        if ligand_axis is not None:
            axis = ligand_axis
        elif camera_norm is not None:
            axis = camera_norm

    # Optional planar locking avoids front/back punches on near-front camera setups.
    if flatten_z:
        flat_axis = Vector((axis.x, axis.y, 0.0))
        if flat_axis.length > 1e-8:
            axis = flat_axis.normalized()
    return axis


def _indices_centroid_vector(load_result: Any, indices: list[int] | None) -> Vector | None:
    if not indices:
        return None
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return None
    coords = coordinates_from_atom_array(atom_array)
    if coords is None:
        return None
    valid: list[int] = []
    count = int(coords.shape[0])
    for idx in indices:
        i = int(idx)
        if 0 <= i < count:
            valid.append(i)
    if not valid:
        return None
    subset = coords[np.asarray(valid, dtype=int)]
    finite = np.isfinite(subset).all(axis=1)
    if not bool(np.any(finite)):
        return None
    center = subset[finite].mean(axis=0)
    return Vector((float(center[0]), float(center[1]), float(center[2])))


def _map_atom_point_to_object_bounds(
    load_result: Any,
    target_obj: bpy.types.Object | None,
    point: Vector,
) -> Vector:
    if target_obj is None:
        return point.copy()
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return point.copy()
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.size == 0:
        return point.copy()
    finite_rows = np.isfinite(coords).all(axis=1)
    if not bool(np.any(finite_rows)):
        return point.copy()
    finite_coords = coords[finite_rows]
    atom_min = finite_coords.min(axis=0)
    atom_max = finite_coords.max(axis=0)
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = target_obj.evaluated_get(depsgraph)
    except Exception:
        eval_obj = target_obj
    try:
        bbox_corners = list(getattr(eval_obj, "bound_box", []) or [])
    except Exception:
        bbox_corners = []
    if not bbox_corners:
        return point.copy()
    try:
        world_corners = [eval_obj.matrix_world @ Vector(corner) for corner in bbox_corners]
    except Exception:
        world_corners = []
    if not world_corners:
        return point.copy()

    obj_min = np.array(
        [
            float(min(c.x for c in world_corners)),
            float(min(c.y for c in world_corners)),
            float(min(c.z for c in world_corners)),
        ],
        dtype=float,
    )
    obj_max = np.array(
        [
            float(max(c.x for c in world_corners)),
            float(max(c.y for c in world_corners)),
            float(max(c.z for c in world_corners)),
        ],
        dtype=float,
    )

    p = np.array([float(point.x), float(point.y), float(point.z)], dtype=float)
    mapped_np = np.zeros(3, dtype=float)
    for axis in range(3):
        atom_span = float(atom_max[axis] - atom_min[axis])
        obj_span = float(obj_max[axis] - obj_min[axis])
        if abs(atom_span) <= 1e-8:
            mapped_np[axis] = float(obj_min[axis])
        else:
            scale = obj_span / atom_span
            mapped_np[axis] = float(obj_min[axis]) + ((float(p[axis]) - float(atom_min[axis])) * scale)
    mapped = Vector((float(mapped_np[0]), float(mapped_np[1]), float(mapped_np[2])))
    if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
        print(
            "Bridge Custom atom->object map: "
            f"target={getattr(target_obj, 'name', '<unknown>')} "
            f"atom_min=({atom_min[0]:.3f},{atom_min[1]:.3f},{atom_min[2]:.3f}) "
            f"atom_max=({atom_max[0]:.3f},{atom_max[1]:.3f},{atom_max[2]:.3f}) "
            f"obj_min=({obj_min[0]:.3f},{obj_min[1]:.3f},{obj_min[2]:.3f}) "
            f"obj_max=({obj_max[0]:.3f},{obj_max[1]:.3f},{obj_max[2]:.3f}) "
            f"point=({p[0]:.3f},{p[1]:.3f},{p[2]:.3f}) "
            f"mapped=({mapped_np[0]:.3f},{mapped_np[1]:.3f},{mapped_np[2]:.3f})"
        )
    return mapped


def _estimate_atom_to_object_scale(load_result: Any, target_obj: bpy.types.Object | None) -> float:
    if target_obj is None:
        return 1.0
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return 1.0
    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.size == 0:
        return 1.0
    finite_rows = np.isfinite(coords).all(axis=1)
    if not bool(np.any(finite_rows)):
        return 1.0
    finite_coords = coords[finite_rows]
    atom_min = finite_coords.min(axis=0)
    atom_max = finite_coords.max(axis=0)

    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = target_obj.evaluated_get(depsgraph)
    except Exception:
        eval_obj = target_obj
    try:
        bbox_corners = list(getattr(eval_obj, "bound_box", []) or [])
    except Exception:
        bbox_corners = []
    if not bbox_corners:
        return 1.0
    try:
        world_corners = [eval_obj.matrix_world @ Vector(corner) for corner in bbox_corners]
    except Exception:
        return 1.0
    if not world_corners:
        return 1.0

    obj_min = np.array(
        [
            float(min(c.x for c in world_corners)),
            float(min(c.y for c in world_corners)),
            float(min(c.z for c in world_corners)),
        ],
        dtype=float,
    )
    obj_max = np.array(
        [
            float(max(c.x for c in world_corners)),
            float(max(c.y for c in world_corners)),
            float(max(c.z for c in world_corners)),
        ],
        dtype=float,
    )

    scales: list[float] = []
    for axis in range(3):
        atom_span = float(atom_max[axis] - atom_min[axis])
        obj_span = float(obj_max[axis] - obj_min[axis])
        if abs(atom_span) <= 1e-8:
            continue
        scales.append(obj_span / atom_span)
    if not scales:
        return 1.0
    scales_sorted = sorted(scales)
    return float(scales_sorted[len(scales_sorted) // 2])


def _ligand_cutout_box_params(
    *,
    ligand_anchor: Vector,
    radius: float,
    offset: float,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    width_scale: float = 1.15,
    depth_scale: float = 0.45,
    height_scale: float = 1.15,
    prefer_camera: bool = False,
    flatten_z: bool = True,
) -> tuple[Vector, Vector, Vector, Vector, float, float, float] | None:
    axis_y = _ligand_cutout_axis(
        ligand_anchor,
        interface_center,
        camera_axis=camera_axis,
        prefer_camera=prefer_camera,
        flatten_z=flatten_z,
    )
    up_hint = Vector((0.0, 0.0, 1.0))
    if abs(axis_y.dot(up_hint)) > 0.95:
        up_hint = Vector((1.0, 0.0, 0.0))
    axis_x = up_hint.cross(axis_y)
    if axis_x.length < 1e-8:
        return None
    axis_x.normalize()
    axis_z = axis_y.cross(axis_x)
    if axis_z.length < 1e-8:
        return None
    axis_z.normalize()

    width_scale = max(0.05, float(width_scale))
    depth_scale = max(0.05, float(depth_scale))
    height_scale = max(0.05, float(height_scale))
    # Rectangular clip window around ligand; y-axis is the clip-depth direction.
    cut_half_width = max(0.01, float(radius) * width_scale)
    cut_half_depth = max(0.01, float(radius) * depth_scale)
    cut_half_height = max(0.01, float(radius) * height_scale)
    cut_center = ligand_anchor.copy()
    if abs(float(offset)) > 1e-6:
        cut_center += axis_y * float(offset)

    return (cut_center, axis_x, axis_y, axis_z, cut_half_width, cut_half_depth, cut_half_height)


def _selection_indices_with_ligand_box_cutout(
    *,
    load_result: Any,
    base_indices: list[int] | None,
    ligand_anchor: Vector,
    radius: float,
    offset: float,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    width_scale: float = 1.15,
    depth_scale: float = 0.45,
    height_scale: float = 1.15,
    one_sided: bool = False,
    prefer_camera: bool = False,
    flatten_z: bool = True,
) -> list[int] | None:
    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return base_indices

    count = _atom_array_count(atom_array)
    if count <= 0:
        return base_indices

    coords = coordinates_from_atom_array(atom_array)
    if coords is None or coords.shape[0] != count:
        return base_indices

    box = _ligand_cutout_box_params(
        ligand_anchor=ligand_anchor,
        radius=radius,
        offset=offset,
        interface_center=interface_center,
        camera_axis=camera_axis,
        width_scale=width_scale,
        depth_scale=depth_scale,
        height_scale=height_scale,
        prefer_camera=prefer_camera,
        flatten_z=flatten_z,
    )
    if box is None:
        return base_indices
    cut_center, axis_x, axis_y, axis_z, cut_half_width, cut_half_depth, cut_half_height = box

    center_np = np.array([cut_center.x, cut_center.y, cut_center.z], dtype=float)
    axis_x_np = np.array([axis_x.x, axis_x.y, axis_x.z], dtype=float)
    axis_y_np = np.array([axis_y.x, axis_y.y, axis_y.z], dtype=float)
    axis_z_np = np.array([axis_z.x, axis_z.y, axis_z.z], dtype=float)
    deltas = np.asarray(coords, dtype=float) - center_np[None, :]
    local_x = np.einsum("ij,j->i", deltas, axis_x_np, optimize=True)
    local_y = np.einsum("ij,j->i", deltas, axis_y_np, optimize=True)
    local_z = np.einsum("ij,j->i", deltas, axis_z_np, optimize=True)
    window_mask = (
        (np.abs(local_x) <= cut_half_width)
        & (np.abs(local_z) <= cut_half_height)
    )
    # Full prism removal gives a stronger rectangular cut than a shallow cavity.
    clip_depth = max(float(radius) * 6.0, cut_half_depth * 10.0)
    if one_sided:
        side_sign = 1.0 if float(offset) >= 0.0 else -1.0
        if side_sign >= 0.0:
            depth_mask = (local_y >= -1e-6) & (local_y <= clip_depth)
        else:
            depth_mask = (local_y <= 1e-6) & (local_y >= (-clip_depth))
    else:
        depth_mask = np.abs(local_y) <= clip_depth
    box_mask = window_mask & depth_mask

    if base_indices is None:
        base_mask = np.ones(count, dtype=bool)
    else:
        base_mask = np.zeros(count, dtype=bool)
        for idx in base_indices:
            if 0 <= int(idx) < count:
                base_mask[int(idx)] = True

    selected = np.where(base_mask & (~box_mask))[0].astype(int).tolist()
    return selected


def _apply_surface_ligand_cutout(
    *,
    load_result: Any,
    target_obj: bpy.types.Object | None,
    cap_material: bpy.types.Material | None = None,
    cap_material_cfg: dict[str, Any] | None = None,
    anchor_load_result: Any | None = None,
    anchor_object: bpy.types.Object | None = None,
    ligand_anchor: Vector,
    radius: float,
    offset: float,
    mirror: bool,
    interface_center: Vector | None,
    camera_axis: Vector | None = None,
    width_scale: float = 1.15,
    depth_scale: float = 0.45,
    height_scale: float = 1.15,
    one_sided: bool = True,
    prefer_camera: bool = False,
    flatten_z: bool = True,
) -> bool:
    if target_obj is None or str(getattr(target_obj, "type", "")).upper() != "MESH":
        return False
    if float(radius) <= 1e-6:
        return False

    anchor_result = anchor_load_result if anchor_load_result is not None else load_result
    anchor_target = anchor_object if anchor_object is not None else target_obj

    ligand_anchor_world = _map_atom_point_to_object_bounds(
        anchor_result,
        anchor_target,
        ligand_anchor,
    )
    scale = _estimate_atom_to_object_scale(anchor_result, anchor_target)
    radius_world = max(0.02, float(radius) * max(scale, 1e-6))
    offset_world = float(offset) * max(scale, 1e-6)

    box = _ligand_cutout_box_params(
        ligand_anchor=ligand_anchor_world,
        radius=radius_world,
        offset=offset_world,
        interface_center=interface_center,
        camera_axis=camera_axis,
        width_scale=width_scale,
        depth_scale=depth_scale,
        height_scale=height_scale,
        prefer_camera=prefer_camera,
        flatten_z=flatten_z,
    )
    if box is None:
        return False
    cut_center, _, axis_y, _, cut_half_width, cut_half_depth, cut_half_height = box

    def _ensure_cutout_cap_material(cfg: dict[str, Any] | None = None) -> bpy.types.Material:
        cfg_dict = cfg if isinstance(cfg, dict) else {}
        base_hex = str(cfg_dict.get("base_color", "#AAB6C2"))
        roughness = float(cfg_dict.get("roughness", 0.62))
        specular = float(cfg_dict.get("specular", 0.11))
        name = f"BridgeCutoutCap_{base_hex.replace('#', '').upper()}"
        existing = bpy.data.materials.get(name)
        if existing is not None:
            return existing
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        nt = material.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (280, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (40, 0)
        _set_bsdf_input(bsdf, ("Base Color",), _hex_to_rgba(base_hex, alpha=1.0))
        _set_bsdf_input(bsdf, ("Roughness",), roughness)
        _set_bsdf_input(bsdf, ("Specular IOR Level", "Specular"), specular)
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        if hasattr(material, "blend_method"):
            material.blend_method = "OPAQUE"
        if hasattr(material, "shadow_method"):
            material.shadow_method = "OPAQUE"
        return material

    def _add_box_cut(center: Vector, half_depth: float) -> bool:
        try:
            bpy.ops.mesh.primitive_cube_add(
                size=2.0,
                location=center,
            )
        except Exception:
            return False

        cutter = bpy.context.active_object
        if cutter is None or str(getattr(cutter, "type", "")).upper() != "MESH":
            return False

        cutter.name = f"BridgeLigandCutout_{uuid.uuid4().hex[:8]}"
        try:
            cutter.scale = (cut_half_width, half_depth, cut_half_height)
        except Exception:
            pass
        if axis_y.length > 1e-8:
            try:
                cutter.rotation_euler = axis_y.to_track_quat("Y", "Z").to_euler()
            except Exception:
                pass
        try:
            cutter.hide_render = True
        except Exception:
            pass
        try:
            cutter.hide_viewport = True
        except Exception:
            pass
        try:
            cutter.display_type = "WIRE"
        except Exception:
            pass

        try:
            modifier = target_obj.modifiers.new(name=f"BridgeLigandCutout_{uuid.uuid4().hex[:6]}", type="BOOLEAN")
            modifier.operation = "DIFFERENCE"
            modifier.object = cutter
            if hasattr(modifier, "solver"):
                modifier.solver = "EXACT"
            if hasattr(modifier, "use_hole_tolerant"):
                try:
                    modifier.use_hole_tolerant = True
                except Exception:
                    pass
            if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
                try:
                    stack = [str(mod.name) for mod in list(target_obj.modifiers)]
                except Exception:
                    stack = []
                print(
                    "Bridge Custom cutout modifier attached: "
                    f"target={getattr(target_obj, 'name', '<unknown>')} "
                    f"modifier={getattr(modifier, 'name', '<unknown>')} "
                    f"stack={stack}"
                )
            return True
        except Exception:
            return False

    def _add_cut_cap(center: Vector, side_sign: float) -> bool:
        try:
            bpy.ops.mesh.primitive_cube_add(size=2.0, location=center + (axis_y * (-0.003 * side_sign)))
        except Exception as exc:
            if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
                print(f"Bridge Custom cutout cap: cube_add failed: {exc}")
            return False
        panel = bpy.context.active_object
        if panel is None or str(getattr(panel, "type", "")).upper() != "MESH":
            if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
                print("Bridge Custom cutout cap: active object missing/non-mesh")
            return False
        panel.name = f"BridgeCutoutCap_{uuid.uuid4().hex[:8]}"
        try:
            panel.scale = (
                max(0.006, cut_half_width * 1.18),
                max(0.003, min(cut_half_depth * 0.12, 0.025)),
                max(0.006, cut_half_height * 1.18),
            )
        except Exception:
            pass
        if axis_y.length > 1e-8:
            try:
                panel.rotation_euler = axis_y.to_track_quat("Y", "Z").to_euler()
            except Exception:
                pass
        try:
            mat = cap_material
            if mat is None:
                mat = _ensure_cutout_cap_material(cap_material_cfg)
            data = getattr(panel, "data", None)
            materials = getattr(data, "materials", None) if data is not None else None
            if materials is not None:
                materials.clear()
                materials.append(mat)
        except Exception as exc:
            if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
                print(f"Bridge Custom cutout cap: material assignment failed: {exc}")
            pass
        if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
            print(
                "Bridge Custom cutout cap created: "
                f"name={getattr(panel, 'name', '<unknown>')} "
                f"center=({center.x:.3f},{center.y:.3f},{center.z:.3f})"
            )
        return True


    if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
        print(
            "Bridge Custom cutout box: "
            f"center=({cut_center.x:.3f},{cut_center.y:.3f},{cut_center.z:.3f}) "
            f"axis=({axis_y.x:.3f},{axis_y.y:.3f},{axis_y.z:.3f}) "
            f"half=({cut_half_width:.3f},{cut_half_depth:.3f},{cut_half_height:.3f}) "
            f"scale={scale:.6f} radius_world={radius_world:.3f} offset_world={offset_world:.3f}"
        )

    # Default to one-sided clipping so the far/exterior side stays intact.
    clip_center = cut_center.copy()
    clip_half_depth = cut_half_depth
    side_sign = 1.0
    if abs(offset_world) > 1e-6:
        side_sign = 1.0 if offset_world >= 0.0 else -1.0
    if one_sided:
        clip_center = cut_center + (axis_y * (clip_half_depth * side_sign))
    applied_primary = _add_box_cut(clip_center, clip_half_depth)
    if not applied_primary:
        return False
    if abs(offset_world) > 1e-6:
        cap_center = cut_center.copy()
        cap_added = _add_cut_cap(cap_center, side_sign)
        if _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
            print(f"Bridge Custom cutout cap added={int(bool(cap_added))}")

    if mirror and abs(float(offset)) > 1e-6:
        mirrored = Vector(
            (
                (ligand_anchor_world.x * 2.0) - clip_center.x,
                (ligand_anchor_world.y * 2.0) - clip_center.y,
                (ligand_anchor_world.z * 2.0) - clip_center.z,
            )
        )
        _add_box_cut(mirrored, clip_half_depth)

    return True


def _apply_override_camera(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    camera_cfg: dict[str, Any],
    objects: list[bpy.types.Object],
    style_objects_by_name: dict[str, list[bpy.types.Object]] | None = None,
) -> None:
    center, radius = _scene_bounds(objects)
    focus_style = str(camera_cfg.get("focus_style", "")).strip()
    if focus_style and style_objects_by_name:
        focus_objects = [
            obj for obj in style_objects_by_name.get(focus_style, []) if _object_has_renderable_geometry(obj)
        ]
        if focus_objects:
            focus_center, _ = _scene_bounds(focus_objects)
            center = focus_center

    distance_scale = float(camera_cfg.get("distance_scale", 3.1))
    distance = max(0.05, radius * distance_scale)
    direction_source = str(camera_cfg.get("direction_source", "preset")).strip().lower()

    up_hint: Vector | None = None
    if direction_source == "pymol":
        quat = camera.matrix_world.to_quaternion()
        direction = quat @ Vector((0.0, 0.0, -1.0))
        up_hint = quat @ Vector((0.0, 1.0, 0.0))

        yaw_offset_deg = float(camera_cfg.get("yaw_offset_deg", 0.0))
        pitch_offset_deg = float(camera_cfg.get("pitch_offset_deg", 0.0))
        if up_hint.length > 1e-8 and abs(yaw_offset_deg) > 1e-6:
            direction = (Matrix.Rotation(math.radians(yaw_offset_deg), 3, up_hint.normalized()) @ direction).normalized()
        right_axis = direction.cross(up_hint) if up_hint.length > 1e-8 else Vector((0.0, 0.0, 0.0))
        if right_axis.length > 1e-8 and abs(pitch_offset_deg) > 1e-6:
            right_axis.normalize()
            rot = Matrix.Rotation(math.radians(pitch_offset_deg), 3, right_axis)
            direction = (rot @ direction).normalized()
            up_hint = (rot @ up_hint).normalized()
    else:
        yaw_deg = float(camera_cfg.get("yaw_deg", -88.0))
        pitch_deg = float(camera_cfg.get("pitch_deg", 8.0))
        yaw = math.radians(yaw_deg)
        pitch = math.radians(pitch_deg)
        direction = Vector((math.cos(pitch) * math.cos(yaw), math.cos(pitch) * math.sin(yaw), math.sin(pitch)))

    if direction.length < 1e-8:
        direction = Vector((0.0, -1.0, 0.0))
    direction.normalize()
    camera.location = center - direction * distance
    if up_hint is not None and not _orient_camera_with_up(camera, direction, up_hint):
        _point_camera_at(camera, center)
    elif up_hint is None:
        _point_camera_at(camera, center)

    data = camera.data
    if isinstance(camera_cfg.get("lens_mm"), (int, float)):
        data.lens = float(camera_cfg["lens_mm"])
    if isinstance(camera_cfg.get("shift_x"), (int, float)):
        data.shift_x = float(camera_cfg["shift_x"])
    if isinstance(camera_cfg.get("shift_y"), (int, float)):
        data.shift_y = float(camera_cfg["shift_y"])
    if isinstance(camera_cfg.get("clip_start"), (int, float)):
        data.clip_start = max(1e-6, float(camera_cfg["clip_start"]))
    else:
        data.clip_start = max(1e-4, distance - radius * 3.0)
    if isinstance(camera_cfg.get("clip_end"), (int, float)):
        data.clip_end = max(data.clip_start + 1e-3, float(camera_cfg["clip_end"]))
    else:
        data.clip_end = max(data.clip_start + 1.0, distance + radius * 6.0)


def _style_result_is_empty(style_result: Any) -> bool:
    if style_result is None:
        return True
    selection_indices = getattr(style_result, "_selection_indices", None)
    style_object = getattr(style_result, "_object", None)
    if isinstance(selection_indices, list) and not selection_indices and style_object is None:
        return True
    return False


def _add_style_with_tracking(
    *,
    load_result: Any,
    style_cfg: dict[str, Any],
    selection: Any,
    material: bpy.types.Material,
    base_object: bpy.types.Object | None,
) -> tuple[Any, list[str], bool]:
    tree = getattr(load_result, "tree", None)
    before_tree_nodes = -1
    if tree is not None:
        try:
            before_tree_nodes = len(getattr(tree, "nodes", []))
        except Exception:
            before_tree_nodes = -1
    before_modifiers = _object_modifier_names(base_object)
    style_result = _add_style(load_result, style_cfg, selection, material=material)
    after_modifiers = _object_modifier_names(base_object)
    added = [name for name in after_modifiers if name not in before_modifiers]
    tree_changed = False
    if before_tree_nodes >= 0:
        try:
            after_tree_nodes = len(getattr(tree, "nodes", []))
            tree_changed = after_tree_nodes > before_tree_nodes
        except Exception:
            tree_changed = False
    return style_result, added, tree_changed


def _resolve_blend_output_path(style_doc: dict[str, Any], output_png: Path) -> Path | None:
    output_cfg = style_doc.get("output")
    if not isinstance(output_cfg, dict):
        return None
    if not bool(output_cfg.get("save_blend", False)):
        return None

    filename = str(output_cfg.get("blend_filename", "") or "").strip()
    if not filename:
        return output_png.with_suffix(".blend")
    configured = Path(filename).expanduser()
    if configured.is_absolute():
        return configured
    return output_png.parent / configured


def _apply_pymol_ss_hints(load_result: Any, view_doc: dict[str, Any]) -> int:
    hints = view_doc.get("ss_hints")
    if not isinstance(hints, dict):
        return 0
    by_chain_resi = hints.get("by_chain_resi")
    if not isinstance(by_chain_resi, dict) or not by_chain_resi:
        return 0

    atom_array = _get_atom_array(load_result)
    if atom_array is None:
        return 0

    try:
        count = int(atom_array.array_length()) if hasattr(atom_array, "array_length") else len(atom_array)
    except Exception:
        return 0
    if count <= 0:
        return 0

    chain_upper_getter = getattr(load_result, "_chain_upper", None)
    bridge_override = getattr(load_result, "_bridge_chain_override_upper", None)
    if isinstance(bridge_override, np.ndarray) and bridge_override.shape[0] == count:
        chain_upper = np.asarray(bridge_override, dtype=object).astype(str)
    elif callable(chain_upper_getter):
        try:
            chain_upper = np.asarray(chain_upper_getter(count), dtype=object).astype(str)
        except Exception:
            chain_upper = _array_as_upper(getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)), count)
    else:
        chain_upper = _array_as_upper(getattr(atom_array, "chain_id", np.array([""] * count, dtype=object)), count)

    try:
        res_ids = np.asarray(getattr(atom_array, "res_id", np.full(count, -1, dtype=int))).astype(int)
    except Exception:
        return 0

    amino_mask = _normalized_amino_mask(atom_array, count)
    try:
        existing_raw = np.asarray(getattr(atom_array, "sec_struct", np.zeros(count, dtype=int)), dtype=object)
    except Exception:
        existing_raw = np.zeros(count, dtype=int)
    sec_struct = _normalize_sec_struct_values(existing_raw, amino_mask)

    applied = 0
    for idx in range(count):
        if not bool(amino_mask[idx]):
            continue
        chain_id = _strip_wrapping_quotes(str(chain_upper[idx])).strip().upper()
        if not chain_id or chain_id in {".", "?"}:
            continue
        key = f"{chain_id}:{int(res_ids[idx])}"
        ss_code = str(by_chain_resi.get(key, "")).strip().upper()
        if ss_code == "H":
            sec_struct[idx] = 1
            applied += 1
        elif ss_code == "S":
            sec_struct[idx] = 2
            applied += 1
        elif ss_code == "L":
            sec_struct[idx] = 3
            applied += 1

    if applied <= 0:
        return 0
    try:
        categories = list(atom_array.get_annotation_categories()) if hasattr(atom_array, "get_annotation_categories") else []
        if "sec_struct" in categories:
            try:
                atom_array.del_annotation("sec_struct")
            except Exception:
                pass
        atom_array.set_annotation("sec_struct", sec_struct.astype(int))
        return applied
    except Exception:
        return 0


def _render(args: argparse.Namespace) -> None:
    style_path = Path(args.style_json).expanduser()
    if not style_path.is_absolute():
        style_path = (Path.cwd() / style_path).absolute()
    view_path = Path(args.view_json).expanduser()
    if not view_path.is_absolute():
        view_path = (Path.cwd() / view_path).absolute()
    structure_path = Path(args.structure).expanduser()
    if not structure_path.is_absolute():
        structure_path = (Path.cwd() / structure_path).absolute()
    output_png = Path(args.output_png).expanduser()
    if not output_png.is_absolute():
        output_png = (Path.cwd() / output_png).absolute()

    style_doc = validate_style_document(_load_json(style_path))
    view_doc = _load_json(view_path)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    _configure_renderer(scene, style_doc)
    _set_world(scene, style_doc["world"])
    camera = _ensure_camera(scene, style_doc, view_doc)
    pymol_up_axis = None
    try:
        pymol_up_axis = camera.matrix_world.to_quaternion() @ Vector((0.0, 1.0, 0.0))
    except Exception:
        pymol_up_axis = None

    ccd_retry_used = False

    def _load_with_ccd_retry(path: Path, object_name: str):
        nonlocal ccd_retry_used
        try:
            return _load_molecule_with_fallback(path, object_name)
        except Exception as exc:
            message = str(exc).lower()
            needs_ccd = (
                "components.bcif" in message
                or "ccd" in message
                or "chemical component" in message
            )
            if not needs_ccd or ccd_retry_used:
                raise
            _ensure_biotite_ccd_database()
            ccd_retry_used = True
            return _load_molecule_with_fallback(path, object_name)

    load_result = _load_with_ccd_retry(structure_path, "BridgeCustom")
    _apply_auth_chain_override(load_result, structure_path)
    ss_applied = _apply_pymol_ss_hints(load_result, view_doc)
    if ss_applied > 0 and _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
        print(f"Bridge Custom: applied PyMOL SS hints atoms={ss_applied} object=BridgeCustom")
    base_obj = _resolve_loaded_object(load_result, "BridgeCustom", allow_missing=True)
    parser_mode = isinstance(load_result, _ParserMolecule)
    ligand_anchor = _ligand_anchor_from_load_result(load_result)
    if ligand_anchor is None and _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False):
        print("Bridge Custom: ligand anchor not available; ligand cutout styles will be skipped.")

    protein_chain_ids = _protein_chain_ids(load_result)
    material_config = _apply_color_follow_to_materials(style_doc, view_doc)
    runtime_styles, material_config, runtime_chain_colors = expand_polymer_styles_for_multichain_palette(
        style_doc=style_doc,
        materials_cfg=material_config,
        protein_chain_ids=protein_chain_ids,
    )
    materials = _build_materials(material_config)
    created_objects: list[bpy.types.Object] = []
    style_objects_by_name: dict[str, list[bpy.types.Object]] = {}
    debug_styles = _env_bool("PRESS_DEBUG_STYLE_SELECTIONS", False)
    runtime_polymer_backend = "none"
    runtime_ligand_backend = "none"
    runtime_ligand_atom_spheres = False
    styled_geometry_applied = False
    global_interface_center = _scene_bounds([base_obj] if base_obj is not None else [])[0]
    if debug_styles and base_obj is not None:
        try:
            base_center_dbg, base_radius_dbg = _scene_bounds([base_obj])
            print(
                "Bridge Custom base bounds: "
                f"center=({base_center_dbg.x:.3f},{base_center_dbg.y:.3f},{base_center_dbg.z:.3f}) "
                f"radius={base_radius_dbg:.3f}"
            )
        except Exception:
            pass
    cutout_camera_axis = None
    try:
        candidate = camera.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        if candidate.length > 1e-8:
            cutout_camera_axis = candidate.normalized()
    except Exception:
        cutout_camera_axis = None
    if debug_styles and runtime_chain_colors:
        print(f"Bridge Custom: runtime polymer chain colors {runtime_chain_colors}")

    for style_idx, style_cfg in enumerate(runtime_styles):
        style_name = str(style_cfg.get("name", f"style_{style_idx}"))
        style_kind_lower = str(style_cfg.get("style", "")).strip().lower()
        cutout_radius = 0.0
        cutout_offset = 0.0
        cutout_mirror = False
        cutout_isolate = True
        cutout_auto_side = False
        cutout_width_scale = 1.15
        cutout_depth_scale = 0.45
        cutout_height_scale = 1.15
        cutout_one_sided = True
        cutout_axis_mode = "interface"
        cutout_flatten_z = True
        cutout_prefer_camera = False
        if style_kind_lower == "surface":
            try:
                cutout_radius = float(style_cfg.get("ligand_cutout_radius", 0.0) or 0.0)
            except Exception:
                cutout_radius = 0.0
            try:
                cutout_offset = float(style_cfg.get("ligand_cutout_offset", 0.0) or 0.0)
            except Exception:
                cutout_offset = 0.0
            cutout_mirror = bool(style_cfg.get("ligand_cutout_mirror", False))
            cutout_isolate = bool(style_cfg.get("ligand_cutout_isolate", True))
            cutout_auto_side = bool(style_cfg.get("ligand_cutout_auto_side", False))
            cutout_one_sided = bool(style_cfg.get("ligand_cutout_one_sided", True))
            cutout_flatten_z = bool(style_cfg.get("ligand_cutout_flatten_z", True))
            try:
                cutout_width_scale = float(style_cfg.get("ligand_cutout_width_scale", 1.15) or 1.15)
            except Exception:
                cutout_width_scale = 1.15
            try:
                cutout_depth_scale = float(style_cfg.get("ligand_cutout_depth_scale", 0.45) or 0.45)
            except Exception:
                cutout_depth_scale = 0.45
            try:
                cutout_height_scale = float(style_cfg.get("ligand_cutout_height_scale", 1.15) or 1.15)
            except Exception:
                cutout_height_scale = 1.15
            axis_mode_value = str(style_cfg.get("ligand_cutout_axis_mode", "interface")).strip().lower()
            if axis_mode_value in {"interface", "camera"}:
                cutout_axis_mode = axis_mode_value
            cutout_prefer_camera = cutout_axis_mode == "camera"

        isolate_surface_cutout = (
            cutout_isolate
            and not parser_mode
            and style_kind_lower == "surface"
            and cutout_radius > 0
            and ligand_anchor is not None
        )

        style_load_result = load_result
        style_base_obj = base_obj
        if parser_mode or isolate_surface_cutout:
            style_object_name = f"BridgeCustom_{style_idx}"
            style_load_result = _load_with_ccd_retry(structure_path, style_object_name)
            _apply_auth_chain_override(style_load_result, structure_path)
            ss_applied = _apply_pymol_ss_hints(style_load_result, view_doc)
            if ss_applied > 0 and debug_styles:
                print(f"Bridge Custom: applied PyMOL SS hints atoms={ss_applied} object={style_object_name}")
            style_base_obj = _resolve_loaded_object(style_load_result, style_object_name, allow_missing=True)

        selection_cfg = style_cfg.get("selection", {})
        selection, selection_indices = _build_selection(
            style_load_result,
            selection_cfg if isinstance(selection_cfg, dict) else {},
            structure_path=structure_path,
            style_name=style_name,
        )
        effective_cutout_offset = cutout_offset
        if (
            style_kind_lower == "surface"
            and cutout_radius > 0
            and ligand_anchor is not None
            and abs(cutout_offset) > 1e-6
            and cutout_auto_side
        ):
            style_center_for_cutout = _indices_centroid_vector(style_load_result, selection_indices)
            cutout_axis_ref = _ligand_cutout_axis(
                ligand_anchor,
                global_interface_center,
                camera_axis=cutout_camera_axis,
                prefer_camera=cutout_prefer_camera,
                flatten_z=cutout_flatten_z,
            )
            if style_center_for_cutout is not None and cutout_axis_ref.length > 1e-8:
                try:
                    rel = style_center_for_cutout - ligand_anchor
                    projected = float(rel.dot(cutout_axis_ref))
                    if abs(projected) > 1e-6:
                        side_sign = 1.0 if projected >= 0.0 else -1.0
                        effective_cutout_offset = abs(float(cutout_offset)) * side_sign
                except Exception:
                    effective_cutout_offset = cutout_offset
        if style_kind_lower == "surface":
            cutout_selection = bool(style_cfg.get("ligand_cutout_selection", False))
            if cutout_selection and cutout_radius > 0 and ligand_anchor is not None:
                cutout_center_ref = global_interface_center
                selection_indices = _selection_indices_with_ligand_box_cutout(
                    load_result=style_load_result,
                    base_indices=selection_indices,
                    ligand_anchor=ligand_anchor,
                    radius=cutout_radius,
                    offset=effective_cutout_offset,
                    interface_center=cutout_center_ref,
                    camera_axis=cutout_camera_axis,
                    width_scale=cutout_width_scale,
                    depth_scale=cutout_depth_scale,
                    height_scale=cutout_height_scale,
                    one_sided=cutout_one_sided,
                    prefer_camera=cutout_prefer_camera,
                    flatten_z=cutout_flatten_z,
                )
                selection = _selection_to_named_attribute(
                    style_load_result,
                    selection_indices,
                    style_name=f"{style_name}_box_cutout",
                )

        material_name = style_cfg["material"]
        material = materials[material_name]
        material_cfg_for_cutout = material_config.get(material_name)
        if not isinstance(material_cfg_for_cutout, dict):
            material_cfg_for_cutout = None
        style_result, new_modifier_names, style_tree_changed = _add_style_with_tracking(
            load_result=style_load_result,
            style_cfg=style_cfg,
            selection=selection,
            material=material,
            base_object=style_base_obj,
        )
        used_fallback = False
        used_auto_chain_fallback = False
        style_empty = _style_result_is_empty(style_result) and not new_modifier_names and not style_tree_changed
        fallback_cfg = style_cfg.get("fallback_selection")
        if style_empty and isinstance(fallback_cfg, dict):
            fallback_selection, fallback_indices = _build_selection(
                style_load_result,
                fallback_cfg,
                structure_path=structure_path,
                style_name=f"{style_name}_fallback",
            )
            if fallback_indices is not None:
                selection_indices = fallback_indices
            style_result, fallback_modifiers, fallback_tree_changed = _add_style_with_tracking(
                load_result=style_load_result,
                style_cfg=style_cfg,
                selection=fallback_selection,
                material=material,
                base_object=style_base_obj,
            )
            for modifier_name in fallback_modifiers:
                if modifier_name not in new_modifier_names:
                    new_modifier_names.append(modifier_name)
            style_tree_changed = style_tree_changed or fallback_tree_changed
            used_fallback = True
            style_empty = _style_result_is_empty(style_result) and not new_modifier_names and not style_tree_changed
        if style_empty:
            chain_ids = _protein_chain_ids(style_load_result)
            auto_query = _auto_chain_fallback_query(style_cfg, chain_ids)
            if auto_query:
                auto_selection, auto_indices = _build_selection(
                    style_load_result,
                    {"query": auto_query},
                    structure_path=structure_path,
                    style_name=f"{style_name}_auto_chain",
                )
                if auto_indices is not None:
                    selection_indices = auto_indices
                style_result, auto_modifiers, auto_tree_changed = _add_style_with_tracking(
                    load_result=style_load_result,
                    style_cfg=style_cfg,
                    selection=auto_selection,
                    material=material,
                    base_object=style_base_obj,
                )
                for modifier_name in auto_modifiers:
                    if modifier_name not in new_modifier_names:
                        new_modifier_names.append(modifier_name)
                style_tree_changed = style_tree_changed or auto_tree_changed
                used_auto_chain_fallback = True
                style_empty = _style_result_is_empty(style_result) and not new_modifier_names and not style_tree_changed
        style_name_lower = str(style_cfg.get("name", "")).strip().lower()
        style_obj = getattr(style_result, "_object", None)
        if style_kind_lower == "surface":
            if cutout_radius > 0:
                if ligand_anchor is None:
                    if debug_styles:
                        print(
                            "Bridge Custom: skipping ligand cutout due to missing ligand anchor "
                            f"style={style_name}"
                        )
                else:
                    target_obj = None
                    if style_obj is not None and str(getattr(style_obj, "type", "")).upper() == "MESH":
                        target_obj = style_obj
                    elif style_base_obj is not None and str(getattr(style_base_obj, "type", "")).upper() == "MESH":
                        target_obj = style_base_obj
                    cutout_targets: list[bpy.types.Object] = []
                    if target_obj is not None:
                        cutout_targets.append(target_obj)
                    cutout_center_ref = global_interface_center
                    cutout_applied = False
                    for cut_target in cutout_targets:
                        try:
                            applied = _apply_surface_ligand_cutout(
                                load_result=style_load_result,
                                target_obj=cut_target,
                                cap_material_cfg=material_cfg_for_cutout,
                                anchor_load_result=load_result,
                                anchor_object=base_obj,
                                ligand_anchor=ligand_anchor,
                                radius=cutout_radius,
                                offset=effective_cutout_offset,
                                mirror=cutout_mirror,
                                interface_center=cutout_center_ref,
                                camera_axis=cutout_camera_axis,
                                width_scale=cutout_width_scale,
                                depth_scale=cutout_depth_scale,
                                height_scale=cutout_height_scale,
                                one_sided=cutout_one_sided,
                                prefer_camera=cutout_prefer_camera,
                                flatten_z=cutout_flatten_z,
                            )
                            cutout_applied = cutout_applied or bool(applied)
                        except Exception:
                            continue
                    if debug_styles:
                        target_names = [str(getattr(obj, "name", "")) for obj in cutout_targets]
                        print(
                            "Bridge Custom: ligand cutout selection+boolean "
                            f"style={style_name} "
                            f"boolean_applied={int(bool(cutout_applied))} "
                            f"targets={target_names} "
                            f"radius={cutout_radius:.3f} offset={effective_cutout_offset:.3f} "
                            f"mirror={int(cutout_mirror)}"
                        )
        if style_kind_lower == "sticks" and _is_ligand_style(style_cfg):
            runtime_ligand_backend = "mn_native_sticks"
        if _is_polymer_cartoon_style(style_cfg):
            runtime_polymer_backend = "mn_cartoon"
            require_geometry = False
            if style_name_lower == "chain_a_cartoon":
                require_geometry = len(protein_chain_ids) >= 1
            elif style_name_lower == "chain_b_cartoon":
                require_geometry = len(protein_chain_ids) >= 2
            elif style_name_lower == "polymer_other_cartoon":
                require_geometry = len(protein_chain_ids) >= 3
            elif len(protein_chain_ids) >= 1:
                require_geometry = True
            if require_geometry and style_empty:
                raise RuntimeError(
                    "Cartoon polymer style produced no geometry. "
                    f"style={style_cfg.get('name', '<unnamed>')} protein_chains={protein_chain_ids} "
                    "Secondary-structure assignment is required (run dss before export)."
                )
        if (
            style_kind_lower == "sticks"
            and _is_ligand_style(style_cfg)
            and style_obj is None
            and style_base_obj is not None
            and (new_modifier_names or style_tree_changed)
        ):
            tuned = _tune_native_sticks_style(
                style_base_obj,
                float(style_cfg.get("sticks_radius", style_cfg.get("radius"))) if style_cfg.get("sticks_radius", style_cfg.get("radius")) is not None else None,
                style_cfg.get("sticks_quality", style_cfg.get("quality")),
                style_cfg.get("sticks_shade_smooth", style_cfg.get("shade_smooth")),
                style_cfg.get("color_blur"),
                disable_atom_spheres=True,
                modifier_names=new_modifier_names if new_modifier_names else None,
            )
            if tuned > 0 and debug_styles:
                print(
                    "Bridge Custom: tuned native sticks modifiers "
                    f"(sockets={tuned}) style={style_name}"
                )
        if (
            style_kind_lower == "sticks"
            and _is_ligand_style(style_cfg)
            and style_obj is not None
        ):
            runtime_ligand_atom_spheres = (
                runtime_ligand_atom_spheres
                or _native_sticks_atom_spheres_enabled(style_obj)
            )
        elif (
            style_kind_lower == "sticks"
            and _is_ligand_style(style_cfg)
            and style_base_obj is not None
            and (new_modifier_names or style_tree_changed)
        ):
            runtime_ligand_atom_spheres = (
                runtime_ligand_atom_spheres
                or _native_sticks_atom_spheres_enabled(
                    style_base_obj,
                    modifier_names=new_modifier_names if new_modifier_names else None,
                )
            )
        if (
            _is_polymer_cartoon_style(style_cfg)
            and style_obj is not None
            and not _object_has_renderable_geometry(style_obj)
        ):
            raise RuntimeError(
                "Cartoon polymer style produced non-renderable geometry. "
                f"style={style_cfg.get('name', '<unnamed>')} "
                "Refusing tube/trace downgrade for preset fidelity."
            )
        if style_obj is not None:
            styled_geometry_applied = True
            created_objects.append(style_obj)
            style_name = str(style_cfg.get("name", "")).strip()
            if style_name:
                style_objects_by_name.setdefault(style_name, []).append(style_obj)
        elif (new_modifier_names or style_tree_changed) and style_base_obj is not None and style_base_obj not in created_objects:
            styled_geometry_applied = True
            created_objects.append(style_base_obj)
        _assign_style_material(
            load_result=style_load_result,
            base_object=style_base_obj,
            style_result=style_result,
            material=material,
            modifier_names=new_modifier_names,
        )
        if debug_styles:
            sel_indices = getattr(style_result, "_selection_indices", None)
            sel_count = len(sel_indices) if isinstance(sel_indices, list) else None
            if sel_count is None and isinstance(selection_indices, list):
                sel_count = len(selection_indices)
            obj_name = getattr(style_obj, "name", None) if style_obj is not None else None
            material_slots: list[str] = []
            if style_obj is not None:
                try:
                    for slot in getattr(style_obj, "material_slots", []):
                        mat = getattr(slot, "material", None)
                        if mat is not None and getattr(mat, "name", ""):
                            material_slots.append(str(mat.name))
                except Exception:
                    material_slots = []
            print(
                "Bridge Custom debug style: "
                f"idx={style_idx} "
                f"name={style_cfg.get('name')} "
                f"style={style_cfg.get('style')} "
                f"selection_count={sel_count} "
                f"fallback={int(used_fallback)} "
                f"auto_chain_fallback={int(used_auto_chain_fallback)} "
                f"selection={selection!r} "
                f"new_modifiers={new_modifier_names} "
                f"tree_changed={int(style_tree_changed)} "
                f"object={obj_name} "
                f"materials={material_slots}"
            )

    if not created_objects and base_obj is not None:
        if not styled_geometry_applied:
            raise RuntimeError(
                "Styles produced no geometry (no style objects/modifiers were created). "
                "Refusing fallback molecule display because preset fidelity would be invalid."
            )
        try:
            base_obj.hide_render = False
            base_obj.hide_set(False)
        except Exception:
            pass
        created_objects.append(base_obj)
    if not any(_object_has_renderable_geometry(obj) for obj in created_objects):
        fallback_material = next(iter(materials.values()), None)
        if fallback_material is not None:
            try:
                fallback_style = {
                    "name": "fallback_visible_geometry",
                    "style": "spheres",
                    "radius": 0.15,
                    "subdivisions": 2,
                    "shade_smooth": True,
                }
                fallback_result = _add_style(load_result, fallback_style, selection=None, material=fallback_material)
                fallback_obj = getattr(fallback_result, "_object", None)
                if fallback_obj is not None:
                    created_objects.append(fallback_obj)
                    print("Bridge Custom: styles produced no visible geometry, added fallback spheres.")
            except Exception as exc:
                print(f"Bridge Custom: fallback geometry failed: {exc}")

    if debug_styles:
        for obj in created_objects:
            try:
                modifier_names = [str(mod.name) for mod in list(obj.modifiers)]
            except Exception:
                modifier_names = []
            print(
                "Bridge Custom created object: "
                f"name={getattr(obj, 'name', '<unknown>')} "
                f"type={getattr(obj, 'type', '<unknown>')} "
                f"modifiers={modifier_names}"
            )

    camera_cfg = style_doc.get("camera", {})
    if isinstance(camera_cfg, dict) and str(camera_cfg.get("mode", "pymol")).strip().lower() == "override":
        _apply_override_camera(
            scene,
            camera,
            camera_cfg,
            created_objects,
            style_objects_by_name=style_objects_by_name,
        )

    light_target, light_radius = _scene_bounds(created_objects)
    yaw_offset_for_lights = 0.0
    if isinstance(camera_cfg, dict):
        try:
            yaw_offset_for_lights = float(camera_cfg.get("yaw_offset_deg", 0.0))
        except Exception:
            yaw_offset_for_lights = 0.0
    _add_lights(
        scene,
        style_doc["lights"],
        target=light_target,
        radius=light_radius,
        pymol_up_axis=pymol_up_axis,
        yaw_offset_deg=yaw_offset_for_lights,
    )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(output_png)

    output_cfg = style_doc.get("output")
    raw_view = bool(isinstance(output_cfg, dict) and output_cfg.get("raw_view", False))
    if raw_view:
        _render_raw_view(scene)
    else:
        bpy.ops.render.render(animation=False, write_still=True)
    blend_output = _resolve_blend_output_path(style_doc, output_png)
    if blend_output is not None:
        blend_output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_output), copy=True)
        print(f"Custom blend saved: {blend_output}")
    run_manifest_raw = str(getattr(args, "run_manifest", "") or "").strip()
    if run_manifest_raw:
        run_manifest_path = Path(run_manifest_raw).expanduser()
        if not run_manifest_path.is_absolute():
            run_manifest_path = (Path.cwd() / run_manifest_path).absolute()
        try:
            manifest_doc = {}
            if run_manifest_path.is_file():
                manifest_doc = _load_json(run_manifest_path)
            manifest_doc["polymer_backend"] = runtime_polymer_backend
            manifest_doc["ligand_backend"] = runtime_ligand_backend
            manifest_doc["ligand_atom_spheres"] = bool(runtime_ligand_atom_spheres)
            # Backward-compatible aliases.
            manifest_doc["sticks_backend"] = runtime_ligand_backend
            manifest_doc["polymer_cartoon_backend"] = runtime_polymer_backend
            manifest_doc["cartoon_polymer_chain_ids"] = protein_chain_ids
            if runtime_chain_colors:
                manifest_doc["polymer_chain_colors"] = runtime_chain_colors
            run_manifest_path.parent.mkdir(parents=True, exist_ok=True)
            run_manifest_path.write_text(json.dumps(manifest_doc, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"Bridge Custom: failed to update run manifest: {exc}")
    print(f"Custom render complete: {output_png}")


def main() -> None:
    parsed = _parse_argv(sys.argv)
    _render(parsed)


if __name__ == "__main__":
    main()
