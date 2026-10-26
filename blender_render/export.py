"""PyMOL scene export. Imports no Blender or service dependencies."""
from __future__ import annotations
import json
import os
import shlex
import hashlib
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
NUCLEIC_RES_NAMES = {"DA", "DT", "DG", "DC", "A", "U", "G", "C"}
TMP_BRIDGE_ROOT = Path(tempfile.gettempdir()) / "blender-render"

def _normalize_cli_string(value: Any) -> str:
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _ensure_tmp_run_dir(run_id: str) -> Path:
    root = TMP_BRIDGE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raw = _normalize_cli_string(value).lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(_normalize_cli_string(value))
    except Exception:
        return default


def _parse_float(value: Any) -> float | None:
    try:
        return float(_normalize_cli_string(value))
    except Exception:
        return None


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r = max(0.0, min(1.0, float(rgb[0])))
    g = max(0.0, min(1.0, float(rgb[1])))
    b = max(0.0, min(1.0, float(rgb[2])))
    return f"#{int(round(r * 255.0)):02X}{int(round(g * 255.0)):02X}{int(round(b * 255.0)):02X}"


def _collect_color_hints(cmd_obj: Any, selection: str) -> dict[str, Any]:
    records = []
    space: dict[str, Any] = {"records": records}
    try:
        cmd_obj.iterate(
            selection,
            "records.append((chain, resn, resi, color, type == 'HETATM', name))",
            space=space,
        )
    except Exception:
        return {}

    chain_color_counts: dict[str, Counter[str]] = defaultdict(Counter)
    class_color_counts: dict[str, Counter[str]] = defaultdict(Counter)

    atom_colors = {}
    for chain, resn, resi, color_idx, hetatm, atom_name in records:
        chain_name = str(chain or "").strip() or "_"
        resn_name = str(resn or "").strip().upper()
        try:
            rgb = cmd_obj.get_color_tuple(int(color_idx))
            color_hex = _rgb_to_hex((float(rgb[0]), float(rgb[1]), float(rgb[2])))
        except Exception:
            continue
        chain_color_counts[chain_name][color_hex] += 1
        atom_colors[f"{chain}:{resi}:{resn}:{atom_name}"] = color_hex
        if int(hetatm):
            class_color_counts["ligand"][color_hex] += 1
        elif resn_name in NUCLEIC_RES_NAMES:
            class_color_counts["nucleic"][color_hex] += 1
        else:
            class_color_counts["polymer"][color_hex] += 1

    def pick(counter: Counter[str]) -> str | None:
        if not counter:
            return None
        return counter.most_common(1)[0][0]

    chain_colors = {name: color for name, counts in chain_color_counts.items() if (color := pick(counts))}
    class_colors = {name: color for name, counts in class_color_counts.items() if (color := pick(counts))}
    if not chain_colors and not class_colors:
        return {}
    return {
        "chain": chain_colors,
        "classes": class_colors,
        "atoms": atom_colors,
        "source": "pymol",
    }


def _collect_secondary_structure_hints(cmd_obj: Any, selection: str) -> dict[str, Any]:
    records: list[tuple[str, str, str, int]] = []
    space: dict[str, Any] = {"records": records}
    try:
        cmd_obj.iterate(
            selection,
            "records.append((chain, resi, ss, type == 'HETATM'))",
            space=space,
        )
    except Exception:
        return {}

    by_chain_resi: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for chain, resi, ss, hetatm in records:
        if int(hetatm):
            continue
        ss_code = str(ss or "").strip().upper()
        if ss_code not in {"H", "S", "L"}:
            continue
        residue_id = str(resi or "").strip()
        if not residue_id:
            continue
        chain_name = str(chain or "").strip()
        key = f"{chain_name}:{residue_id}"
        previous = by_chain_resi.get(key)
        if previous in {"H", "S"} and ss_code == "L":
            continue
        by_chain_resi[key] = ss_code
        counts[ss_code] += 1

    if not by_chain_resi:
        return {}
    return {
        "source": "pymol_dss",
        "by_chain_resi": by_chain_resi,
        "counts": dict(counts),
    }


def collect_view_payload(cmd_obj: Any) -> dict[str, Any]:
    view_raw = cmd_obj.get_view(quiet=1)
    if not isinstance(view_raw, (list, tuple)) or len(view_raw) < 18:
        raise RuntimeError("cmd.get_view(quiet=1) did not return the expected 18-float view payload.")
    pymol_view = [float(value) for value in list(view_raw)[:18]]

    payload: dict[str, Any] = {"pymol_view": pymol_view}

    try:
        fov_value = cmd_obj.get("field_of_view")
    except Exception:
        fov_value = None
    fov_deg = _parse_float(fov_value)
    if fov_deg is not None and fov_deg > 0:
        payload["fov_deg"] = fov_deg

    try:
        orthoscopic_raw = cmd_obj.get("orthoscopic")
    except Exception:
        orthoscopic_raw = None
    if orthoscopic_raw is not None:
        payload["orthoscopic"] = _parse_bool(orthoscopic_raw)

    try:
        viewport = cmd_obj.get_viewport()
    except Exception:
        viewport = None
    if isinstance(viewport, (list, tuple)) and len(viewport) == 2:
        try:
            payload["viewport_px"] = [int(viewport[0]), int(viewport[1])]
        except Exception:
            pass

    return payload


def _run_dss_assignment(cmd_obj: Any, selection: str) -> dict[str, Any]:
    dss_target = str(selection or "(all)").strip() or "(all)"
    result: dict[str, Any] = {"ran": False, "selection": dss_target}
    try:
        cmd_obj.dss(dss_target)
        result["ran"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _summarize_cif_text(cif_text: str) -> dict[str, Any]:
    atom_count = 0
    chain_ids: set[str] = set()
    residues: set[tuple[str, str, str]] = set()

    atom_headers: list[str] = []
    atom_header_index: dict[str, int] = {}
    reading_atom_loop = False

    def _clean(value: str) -> str:
        token = _normalize_cli_string(str(value))
        if token in {"?", ".", ""}:
            return ""
        return token

    def _split_fields(raw: str) -> list[str]:
        text = str(raw).strip()
        if not text:
            return []
        try:
            return shlex.split(text, posix=True)
        except Exception:
            return text.split()

    for raw_line in cif_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.lower() == "loop_":
            atom_headers = []
            atom_header_index = {}
            reading_atom_loop = False
            continue

        if line.startswith("_atom_site."):
            reading_atom_loop = True
            header_name = line.split(".", 1)[1].strip()
            atom_header_index[header_name] = len(atom_headers)
            atom_headers.append(header_name)
            continue

        if reading_atom_loop and atom_headers and line.startswith("_"):
            reading_atom_loop = False
            atom_headers = []
            atom_header_index = {}

        if reading_atom_loop and atom_headers:
            parts = _split_fields(line)
            if not parts:
                continue
            group = parts[0].upper()
            if group not in {"ATOM", "HETATM"}:
                continue
            atom_count += 1

            def _from_header(*names: str) -> str:
                for name in names:
                    idx = atom_header_index.get(name)
                    if idx is not None and idx < len(parts):
                        return _clean(parts[idx])
                return ""

            chain = _from_header("auth_asym_id", "label_asym_id")
            resi = _from_header("auth_seq_id", "label_seq_id", "id")
            resn = _from_header("auth_comp_id", "label_comp_id")
            if chain:
                chain_ids.add(chain)
            residues.add((chain, resi, resn))
            continue

        # Fallback for atypical single-line formats.
        if not line.startswith(("ATOM", "HETATM")):
            continue
        atom_count += 1
        parts = _split_fields(line)
        if len(parts) < 6:
            continue
        chain = ""
        if len(parts) > 16:
            chain = _clean(parts[16])
        if not chain and len(parts) > 6:
            chain = _clean(parts[6])
        resi = _clean(parts[8]) if len(parts) > 8 else ""
        resn = _clean(parts[5]) if len(parts) > 5 else ""
        if chain:
            chain_ids.add(chain)
        residues.add((chain, resi, resn))

    return {
        "atom_count": atom_count,
        "residue_count": len(residues),
        "chain_ids": sorted(chain_ids),
    }


def _summarize_pdb_text(pdb_text: str) -> dict[str, Any]:
    atom_count = 0
    chain_ids: set[str] = set()
    residues: set[tuple[str, str, str]] = set()

    for raw_line in str(pdb_text or "").splitlines():
        line = raw_line.rstrip("\n")
        if not line.startswith(("ATOM", "HETATM")):
            continue
        atom_count += 1
        chain = _normalize_cli_string(line[21:22]) if len(line) >= 22 else ""
        resn = _normalize_cli_string(line[17:20]) if len(line) >= 20 else ""
        resi = _normalize_cli_string(line[22:26]) if len(line) >= 26 else ""
        if chain:
            chain_ids.add(chain)
        residues.add((chain, resi, resn))

    return {
        "atom_count": atom_count,
        "residue_count": len(residues),
        "chain_ids": sorted(chain_ids),
    }


def _looks_like_cif_atom_site(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    has_data_block = any(line.lower().startswith("data_") for line in lines)
    has_loop = any(line.lower() == "loop_" for line in lines)
    has_atom_headers = any(line.lower().startswith("_atom_site.") for line in lines)
    has_atom_rows = any(line.startswith("ATOM") or line.startswith("HETATM") for line in lines)
    if has_atom_headers:
        return True
    return bool(has_atom_rows and (has_data_block or has_loop))


def _looks_like_pdb_atoms(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return any(line.startswith(("ATOM", "HETATM")) for line in text.splitlines())


def _build_single_payload(
    cmd_obj: Any,
    selection: str,
    state: int = -1,
    *,
    include_color_hints: bool = False,
    enabled_only: bool = True,
) -> dict[str, Any]:
    selection = f"({selection}) and enabled" if enabled_only else selection
    dss_status = _run_dss_assignment(cmd_obj, selection)
    if not bool(dss_status.get("ran", False)):
        raise RuntimeError(
            "Secondary-structure assignment failed before export. "
            f"dss selection={dss_status.get('selection', selection)!r} "
            f"error={dss_status.get('error', '<unknown>')}"
        )

    cif_text = ""
    pdb_text = ""
    cif_ok = False
    pdb_ok = False

    try:
        cif_text = str(cmd_obj.get_str("cif", selection, state=state) or "")
        cif_ok = _looks_like_cif_atom_site(cif_text)
    except Exception:
        cif_text = ""
        cif_ok = False

    try:
        pdb_text = str(cmd_obj.get_str("pdb", selection, state=state) or "")
        pdb_ok = _looks_like_pdb_atoms(pdb_text)
    except Exception:
        pdb_text = ""
        pdb_ok = False

    if not cif_ok and not pdb_ok:
        raise RuntimeError(
            "Neither CIF nor PDB export contained atom records. "
            "Verify the selection contains atoms."
        )

    if cif_ok:
        structure_summary = _summarize_cif_text(cif_text)
    else:
        structure_summary = _summarize_pdb_text(pdb_text)

    payload = {
        "version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection": selection,
        "state": state,
        "run_id": uuid.uuid4().hex[:12],
        "structure_summary": structure_summary,
        "view": collect_view_payload(cmd_obj),
        "dss": dss_status,
    }
    ss_hints = _collect_secondary_structure_hints(cmd_obj, selection)
    if ss_hints:
        payload["ss_hints"] = ss_hints
    elif callable(getattr(cmd_obj, "count_atoms", None)) and cmd_obj.count_atoms(f"({selection}) and polymer.protein and name CA"):
        raise RuntimeError("DSS completed but no protein secondary-structure metadata was exported.")
    if cif_ok:
        payload["cif_text"] = cif_text
    if pdb_ok:
        payload["pdb_text"] = pdb_text
    if include_color_hints:
        color_hints = _collect_color_hints(cmd_obj, selection)
        if color_hints:
            payload["color_hints"] = color_hints
    return payload


MAX_STATES = 360  # matches the most animation frames


def collect_state_coordinates(cmd_obj, selection: str, base_state: int) -> dict[str, Any]:
    """Every state's coordinates in exported atom order, for state animations.

    Rounded like CIF coordinates. Renderers verify the base state against the
    exported CIF, so an ordering difference fails instead of scrambling atoms.
    """
    count = int(cmd_obj.count_states(selection))
    if not 2 <= count <= MAX_STATES:
        raise ValueError(f"State animation needs 2 to {MAX_STATES} states; the selection has {count}")
    # CIFs can contain atoms without coordinates in any state. Compare the
    # atoms actually exported in each state, not PyMOL's union atom count.
    expected = [atom.index for atom in cmd_obj.get_model(selection, state=base_state).atom]
    atoms = len(expected)
    if not atoms:
        raise ValueError("The base state contains no coordinates")
    coordinates = []
    for number in range(1, count + 1):
        indices = [atom.index for atom in cmd_obj.get_model(selection, state=number).atom]
        coords = cmd_obj.get_coords(selection, state=number)
        if indices != expected or coords is None or len(coords) != atoms:
            raise ValueError(f"State {number} does not contain the same {atoms} atoms as the others")
        coordinates.append([round(float(value), 3) for row in coords for value in row])
    return {"base": int(base_state), "coordinates": coordinates}


def build_render_payload(cmd_obj, selection="(all)", state=-1, *, include_color_hints=False, enabled_only=True, all_states=False):
    """Flatten enabled objects to one CIF while preserving transforms and identity.

    PyMOL's multi-object CIF contains separate data blocks; molecular loaders
    commonly read only the first. Temporary copies also namespace colliding
    chain IDs so secondary structure cannot bridge unrelated objects.
    """
    selected = f"({selection}) and enabled" if enabled_only else selection
    objects = cmd_obj.get_object_list(selected) if callable(getattr(cmd_obj, "get_object_list", None)) else []
    provenance = {"enabled_objects": list(objects), "object_transforms": {}}
    object_state = getattr(cmd_obj, 'get_object_state', None)
    current_state = getattr(cmd_obj, 'get_state', None)
    if state == -1 and (callable(object_state) or callable(current_state)):
        provenance['object_states'] = {
            name: int(object_state(name) if callable(object_state) else current_state()) for name in objects}
    for name in objects:
        provenance["object_transforms"][name] = list(cmd_obj.get_object_matrix(name) or [])
    if all_states and len(objects) != 1:
        raise ValueError("State animation needs exactly one enabled object with several states")
    if len(objects) <= 1:
        result = _build_single_payload(cmd_obj, selected, state, include_color_hints=include_color_hints, enabled_only=False)
        result["provenance"] = provenance
        if all_states:
            base = state if state > 0 else provenance.get("object_states", {}).get(objects[0], cmd_obj.get_state())
            result["state_coordinates"] = collect_state_coordinates(cmd_obj, selected, base)
        return result
    temporary = []
    namespace = "_blender_export_" + uuid.uuid4().hex[:12]
    used, mapping = set(), {}
    original_view = cmd_obj.get_view()
    try:
        for index, name in enumerate(objects):
            clone = namespace + "_" + str(index)
            cmd_obj.create(clone, f"({selected}) and model {name}", source_state=state, target_state=1, zoom=0)
            temporary.append(clone)
            remap = {}
            for chain in cmd_obj.get_chains(clone):
                replacement = chain
                if replacement in used:
                    serial = 2
                    while f"{chain or 'chain'}_{serial}" in used:
                        serial += 1
                    replacement = f"{chain or 'chain'}_{serial}"
                used.add(replacement)
                remap[chain] = replacement
                mapping[f"{name}:{chain}"] = replacement
            cmd_obj.alter(clone, "chain=remap[chain]", space={"remap":remap})
        combined = namespace + "_all"
        cmd_obj.create(combined, " or ".join(temporary), source_state=1, target_state=1, zoom=0)
        temporary.append(combined)
        cmd_obj.set_view(original_view)
        result = _build_single_payload(cmd_obj, combined, 1, include_color_hints=include_color_hints, enabled_only=False)
        # The UUID belongs only to the temporary PyMOL objects. Preserve the
        # user's selection/state and stable exported block identity instead.
        result["selection"], result["state"] = selected, state
        result["dss"]["selection"] = selected
        for field in ("cif_text", "pdb_text"):
            if field in result:
                result[field] = result[field].replace(combined, "blender_export")
        # PDB cannot faithfully encode namespaced chain identifiers.
        if any(len(value) > 1 for value in mapping.values()):
            result.pop("pdb_text", None)
            provenance["pdb_fallback_unavailable"] = "Namespaced multi-object chain IDs require CIF"
        provenance["chain_mapping"] = mapping
        result["provenance"] = provenance
        return result
    finally:
        for name in temporary:
            cmd_obj.delete(name)
        cmd_obj.set_view(original_view)


def from_file(path, selection="(all)", state=-1):
    """Load trusted local files. PSE sessions use pickle and may execute code."""
    import pymol2
    path = Path(path).resolve()
    if path.suffix.lower() not in {".pse", ".cif", ".pdb", ".mmcif"}:
        raise ValueError("Supported inputs are .pse, .cif, and .pdb")
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        if path.suffix.lower() == '.pse':
            cmd.load(str(path))
        else:
            # Converter scratch filenames differ across backends. They must
            # not become molecular object identity in the normalized bundle.
            cmd.load(str(path), 'structure')
        if path.suffix.lower() != ".pse":
            cmd.hide("everything")
            cmd.show("cartoon", "polymer")
            cmd.show("sticks", "organic")
            cmd.orient()
            cmd.zoom("all", buffer=2)
        payload = build_render_payload(cmd, selection, state, include_color_hints=True)
        payload.setdefault("provenance", {}).update(filename=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 format=path.suffix.lower(), pymol_version=list(cmd.get_version())[:2])
        return payload


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Export a trusted molecular file to a render payload")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--selection", default="(all)")
    args = parser.parse_args()
    Path(args.output).write_text(json.dumps(from_file(args.input, args.selection), allow_nan=False))
