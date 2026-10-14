"""The shared scene: one JSON document that the viewer panel draws and renders reproduce.

Claude edits it (``scene/current`` in a workspace artifact's database), the page writes it when
someone uses the controls, and ``pymol_session.py --scene`` turns it into a PyMOL session for
Blender. See references/scene.md for the full description.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

SCHEMA = 1
REPRESENTATIONS = ("cartoon", "surface", "spacefill", "putty", "backbone", "hidden")
LAYER_REPRESENTATIONS = ("sticks", "spacefill", "surface", "cartoon", "putty")
THEMES = ("chain", "ss", "rainbow", "bfactor", "plddt", "element")
KINDS = ("all", "polymer", "protein", "nucleic", "ligand", "water", "ion")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
RESIDUES = re.compile(r"^\s*-?\d+[A-Za-z]?(\s*-\s*-?\d+[A-Za-z]?)?(\s*,\s*-?\d+[A-Za-z]?(\s*-\s*-?\d+[A-Za-z]?)?)*\s*$")
SELECTION_KEYS = {"chain", "residues", "ligand", "site", "cutoff", "kind"}
DEFAULT = {"schema": SCHEMA, "representation": "cartoon", "color": "chain", "chain_colors": {}, "opacity": 1.0,
           "hidden": [], "ligands": True, "ions": True, "waters": False, "layers": [], "focus": None}
BOOKKEEPING = ("note", "revision", "updated_by", "written_at")


class SceneError(ValueError):
    pass


def default_scene(**changes) -> dict:
    scene = copy.deepcopy(DEFAULT)
    scene.update(changes)
    return scene


def _color(value, where: str, allow_themes=True) -> str:
    if isinstance(value, str) and (HEX.match(value) or (allow_themes and value in THEMES)):
        return value
    options = "a #rrggbb color" + (" or one of " + ", ".join(THEMES) if allow_themes else "")
    raise SceneError(f"{where}: {value!r} is not {options}")


def _selection(value, where: str) -> dict:
    if not isinstance(value, dict) or not value:
        raise SceneError(f"{where}: a selection is a non-empty object such as {{\"chain\": \"A\"}}")
    unknown = set(value) - SELECTION_KEYS
    if unknown:
        raise SceneError(f"{where}: unknown selection fields {sorted(unknown)}; use {sorted(SELECTION_KEYS)}")
    out = {}
    if "chain" in value:
        chains = value["chain"] if isinstance(value["chain"], list) else [value["chain"]]
        if not chains or not all(isinstance(c, str) and c for c in chains):
            raise SceneError(f"{where}.chain: give a chain id or a list of chain ids")
        out["chain"] = chains
    if "residues" in value:
        residues = value["residues"]
        if isinstance(residues, int):
            residues = str(residues)
        if isinstance(residues, list):
            residues = ",".join(str(r) for r in residues)
        if not isinstance(residues, str) or not RESIDUES.match(residues):
            raise SceneError(f"{where}.residues: use author numbering like \"45-50,60\"")
        out["residues"] = re.sub(r"\s+", "", residues)
    for key in ("ligand", "site"):
        if key in value:
            if not isinstance(value[key], str) or not value[key].strip():
                raise SceneError(f"{where}.{key}: give a ligand key such as \"HEM A142\" or a residue name such as \"HEM\"")
            out[key] = " ".join(value[key].split())
    if "cutoff" in value:
        if not isinstance(value["cutoff"], (int, float)) or not 1 <= value["cutoff"] <= 15:
            raise SceneError(f"{where}.cutoff: use a distance between 1 and 15 Å")
        out["cutoff"] = float(value["cutoff"])
    if "kind" in value:
        if value["kind"] not in KINDS:
            raise SceneError(f"{where}.kind: use one of {', '.join(KINDS)}")
        out["kind"] = value["kind"]
    return out


def validate(scene: dict, summary: dict | None = None) -> dict:
    """Normalized copy of ``scene``; raises SceneError naming the first problem.

    With a structure ``summary`` (structure_io.summarize), chain ids and ligands are checked too.
    """
    if not isinstance(scene, dict):
        raise SceneError("The scene must be a JSON object")
    unknown = set(scene) - set(DEFAULT) - set(BOOKKEEPING) - {"camera"}
    if unknown:
        raise SceneError(f"Unknown scene fields {sorted(unknown)}; see references/scene.md")
    out = default_scene()
    out.update({k: copy.deepcopy(v) for k, v in scene.items()})
    if out["schema"] != SCHEMA:
        raise SceneError(f"Unsupported scene schema {out['schema']!r}; expected {SCHEMA}")
    if out["representation"] not in REPRESENTATIONS:
        raise SceneError(f"representation: use one of {', '.join(REPRESENTATIONS)}")
    out["color"] = _color(out["color"], "color")
    if not isinstance(out["chain_colors"], dict):
        raise SceneError("chain_colors: map chain ids to #rrggbb colors")
    out["chain_colors"] = {str(k): _color(v, f"chain_colors.{k}", False) for k, v in out["chain_colors"].items()}
    if not isinstance(out["opacity"], (int, float)) or not 0.05 <= out["opacity"] <= 1:
        raise SceneError("opacity: use a number between 0.05 and 1")
    for flag in ("ligands", "ions", "waters"):
        if not isinstance(out[flag], bool):
            raise SceneError(f"{flag}: use true or false")
    if not isinstance(out["hidden"], list):
        raise SceneError("hidden: a list of selections")
    out["hidden"] = [_selection(s, f"hidden[{i}]") for i, s in enumerate(out["hidden"])]
    if not isinstance(out["layers"], list) or len(out["layers"]) > 40:
        raise SceneError("layers: a list of at most 40 layers")
    layers = []
    for i, layer in enumerate(out["layers"]):
        where = f"layers[{i}]"
        if not isinstance(layer, dict) or "select" not in layer:
            raise SceneError(f"{where}: each layer needs a \"select\"")
        extra = set(layer) - {"select", "representation", "color", "label", "opacity"}
        if extra:
            raise SceneError(f"{where}: unknown fields {sorted(extra)}")
        rep = layer.get("representation", "sticks")
        if rep not in LAYER_REPRESENTATIONS:
            raise SceneError(f"{where}.representation: use one of {', '.join(LAYER_REPRESENTATIONS)}")
        clean = {"select": _selection(layer["select"], where + ".select"), "representation": rep,
                 "color": _color(layer.get("color", "element"), where + ".color")}
        if "label" in layer:
            if not isinstance(layer["label"], (bool, str)):
                raise SceneError(f"{where}.label: true for residue labels, or a text label")
            clean["label"] = layer["label"]
        if "opacity" in layer:
            if not isinstance(layer["opacity"], (int, float)) or not 0.05 <= layer["opacity"] <= 1:
                raise SceneError(f"{where}.opacity: use a number between 0.05 and 1")
            clean["opacity"] = layer["opacity"]
        layers.append(clean)
    out["layers"] = layers
    if out["focus"] is not None:
        out["focus"] = _selection(out["focus"], "focus")
    if summary:
        _check_against(out, summary)
    return out


def _selections(scene: dict):
    yield from scene["hidden"]
    yield from (layer["select"] for layer in scene["layers"])
    if scene["focus"]:
        yield scene["focus"]


def _check_against(scene: dict, summary: dict) -> None:
    chains = {c["id"] for c in summary.get("chains", [])} | {i.split()[1][0] for i in summary.get("ions", [])}
    ligands = summary.get("ligands", [])
    keys = {l["key"].upper() for l in ligands} | {l["comp"].upper() for l in ligands}
    for selection in _selections(scene):
        for chain in selection.get("chain", []):
            if chain not in chains:
                raise SceneError(f"Chain {chain!r} is not in this structure (chains: {', '.join(sorted(chains))})")
        for key in ("ligand", "site"):
            if key in selection and selection[key].upper() not in keys:
                raise SceneError(f"Ligand {selection[key]!r} is not in this structure "
                                 f"(ligands: {', '.join(l['key'] for l in ligands) or 'none'})")


def merge(current: dict, patch: dict) -> dict:
    """Apply a partial scene (only the fields to change) and bump the revision."""
    merged = copy.deepcopy(current or default_scene())
    for key, value in patch.items():
        merged[key] = copy.deepcopy(value)
    merged["revision"] = int((current or {}).get("revision", 0)) + 1
    return merged


# ---------------------------------------------------------------- PyMOL translation

STANDARD = ["ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET", "PHE", "PRO",
            "SER", "THR", "TRP", "TYR", "VAL", "SEC", "PYL", "UNK", "A", "C", "G", "U", "T", "I", "N", "DA", "DC", "DG",
            "DT", "DU", "DI"]
MODIFIED = ["MSE", "SEP", "TPO", "CSO", "CSD", "CME", "HYP", "MLY", "M3L", "KCX", "PCA", "OCS", "CSX", "NEP", "HIC",
            "ALY", "SAC", "MLZ", "CGU", "TYS", "FME", "ACE", "NH2", "NME"]
COVALENT_MIN_HEAVY = 15


def _resi(text: str) -> str:
    parts = []
    for item in text.split(","):
        bounds = re.match(r"^(-?\d+[A-Za-z]?)(?:-(-?\d+[A-Za-z]?))?$", item)
        lo, hi = bounds.group(1), bounds.group(2)
        escape = lambda v: "\\" + v if v.startswith("-") else v
        parts.append(escape(lo) + (f"-{escape(hi)}" if hi else ""))
    return "+".join(parts)


def ligand_pymol(key: str, obj: str) -> str:
    words = key.split()
    if len(words) == 2:
        match = re.match(r"^(\S)(-?\d+)([A-Za-z]?)$", words[1]) or re.match(r"^(\S+?)(-?\d+)([A-Za-z]?)$", words[1])
        if match:
            chain, resi, ins = match.groups()
            resi_sel = ("\\" + resi if resi.startswith("-") else resi) + ins
            return f"({obj} and resn {words[0]} and chain {chain} and resi {resi_sel})"
    return f"({obj} and resn {words[0]})"


def to_pymol(selection: dict, obj: str) -> str:
    """PyMOL selection for one scene selection (fields are combined with AND)."""
    parts = [f"({obj})"]
    if "chain" in selection:
        parts.append("chain " + "+".join(selection["chain"]))
    if "residues" in selection:
        parts.append("resi " + _resi(selection["residues"]))
    if "ligand" in selection:
        parts.append(ligand_pymol(selection["ligand"], obj))
    if "site" in selection:
        ligand = ligand_pymol(selection["site"], obj)
        parts.append(f"(byres ({obj} and polymer within {selection.get('cutoff', 5.0)} of {ligand}) and not {ligand})")
    kind = selection.get("kind")
    if kind and kind != "all":
        covalent = f"(polymer and not resn {'+'.join(STANDARD + MODIFIED)})"
        parts.append({"polymer": "polymer", "protein": "polymer.protein", "nucleic": "polymer.nucleic",
                      "ligand": f"(organic or {covalent})", "water": "solvent", "ion": "inorganic"}[kind])
    return " and ".join(parts)


def load(value: str) -> dict:
    """A scene from a JSON string or file; accepts the database document as returned by tools."""
    text = Path(value).read_text() if not value.lstrip().startswith("{") else value
    data = json.loads(text)
    if "data" in data and isinstance(data["data"], dict) and "schema" in data["data"]:
        data = data["data"]
    return data
