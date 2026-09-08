"""Local preset persistence and agent-authored visual briefs; no compute or network."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .core import load_and_validate_style_json, validate_style_document
from .presets import catalog, preset_id, preset_path, required_capabilities
from .spec import PresetDefinition, resolve_options, style_digest, validate_camera_policy


def _local_path(identifier: str, root: Path) -> Path:
    if not isinstance(identifier, str) or not re.fullmatch(r"(?:user|project)/[a-z0-9][a-z0-9_-]{0,79}", identifier):
        raise ValueError("Local preset ID must be user/name or project/name (lowercase letters, digits, _ or -)")
    path = root / (identifier + ".json")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Local preset path escapes catalog directory")
    return path


def inspect_preset(identifier: str, root: Path) -> dict:
    if identifier.startswith(("user/", "project/")):
        doc = json.loads(_local_path(identifier, root).read_text())
        if not isinstance(doc, dict) or type(doc.get("format")) is not int or doc["format"] not in (1, 2) or doc.get("id") != identifier:
            raise ValueError("Local preset identity/format mismatch")
        required = {"format", "id", "revision", "description", "style_doc", "sha256", "required_capabilities", "provenance"}
        if doc["format"] == 2:
            required.add("camera_policy")
        if set(doc) != required:
            raise ValueError("Invalid local preset envelope fields; missing=" + str(sorted(required - set(doc)))
                             + ", unsupported=" + str(sorted(set(doc) - required)))
        if (type(doc["revision"]) is not int or doc["revision"] < 1
                or not isinstance(doc["description"], str) or not isinstance(doc["provenance"], dict)
                or not isinstance(doc["required_capabilities"], list)
                or not all(isinstance(item, str) for item in doc["required_capabilities"])):
            raise ValueError("Invalid local preset envelope metadata")
        validate_style_document(doc["style_doc"])
        if doc.get("sha256") != style_digest(doc["style_doc"]):
            raise ValueError("Saved preset digest mismatch; validate and save the edited JSON again")
        if doc["required_capabilities"] != required_capabilities(doc["style_doc"]):
            raise ValueError("Saved preset capability requirements mismatch")
        if doc["format"] == 1:
            doc.update(camera_policy="authored", camera_policy_source="legacy_format_1")
        validate_camera_policy(doc["camera_policy"], doc["style_doc"])
        return doc
    name = preset_id(identifier)
    return {"format": 1, "id": "builtin/" + name, "revision": 1,
            "style_doc": load_and_validate_style_json(preset_path(name)),
            "description": next(row["description"] for row in catalog() if row["id"] == name)}


def resolve_authored(options: dict, payload: dict, root: Path) -> dict:
    """Local catalog lookup only; all option semantics belong to the shared resolver."""
    options = copy.deepcopy(options)
    name = options.get("preset", "")
    local = None
    if "style_doc" not in options and name.startswith(("user/", "project/")):
        local = inspect_preset(name, root)
    definition = None
    if local:
        source = {
            "kind": name.split("/")[0], "id": name, "revision": local["revision"],
            "sha256": local["sha256"], "camera_policy": local["camera_policy"],
        }
        if "camera_policy_source" in local:
            source["camera_policy_source"] = local["camera_policy_source"]
        definition = PresetDefinition(name, local["style_doc"], source, local["camera_policy"])
    return resolve_options(options, payload, preset_definition=definition)


def save_preset(identifier: str, style: dict, root: Path, *, description: str,
                provenance: dict | None = None, replace: bool = False,
                camera_policy: str = "preserve_input") -> dict:
    validate_style_document(style)
    validate_camera_policy(camera_policy, style)
    if not isinstance(description, str) or (provenance is not None and not isinstance(provenance, dict)):
        raise ValueError("Preset description must be text and provenance must be an object")
    path = _local_path(identifier, root)
    previous = inspect_preset(identifier, root) if path.exists() else None
    if previous and not replace:
        raise ValueError("Preset already exists; use --replace to create the next revision")
    doc = {"format": 2, "camera_policy": camera_policy, "id": identifier, "revision": previous["revision"] + 1 if previous else 1,
           "description": description, "style_doc": copy.deepcopy(style), "sha256": style_digest(style),
           "required_capabilities": required_capabilities(style), "provenance": provenance or {}}
    data = json.dumps(doc, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".preset-")
    temp = Path(name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
        if replace:
            os.replace(temp, path)
        else:
            # Atomic, no-clobber publication, including concurrent writers.
            os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return {**doc, "path": str(path)}


def list_presets(root: Path) -> list[dict]:
    entries = catalog()
    for namespace in ("user", "project"):
        for path in sorted((root / namespace).glob("*.json")):
            doc = inspect_preset(namespace + "/" + path.stem, root)
            entries.append({key: doc[key] for key in ("id", "revision", "description", "required_capabilities", "camera_policy")})
    return entries


def reference_brief(image: Path, observations: dict) -> dict:
    """Bind an agent's visible observations to a PNG, then rank existing designs.

    This is not an image-understanding model. The developer skill requires actual
    image inspection before supplying observations/overrides. Pixels establish
    provenance and dimensions; no hidden shader implementation is inferred.
    """
    from PIL import Image
    fields = {"representations", "material_family", "palette", "background", "lighting",
              "edges", "composition", "ligand_treatment", "required_capabilities", "overrides", "notes", "base_preset"}
    unknown = set(observations) - fields
    if unknown:
        raise ValueError("Unsupported visual brief fields: " + ", ".join(sorted(unknown)))
    for key in ("representations", "palette", "required_capabilities"):
        if not isinstance(observations.get(key), list) or not all(isinstance(v, str) for v in observations[key]):
            raise ValueError("Visual brief requires a string list: " + key)
    if not observations["representations"]:
        raise ValueError("Visual brief requires observable representations")
    for key in ("material_family", "background", "lighting", "edges", "composition", "ligand_treatment", "notes", "base_preset"):
        if key in observations and not isinstance(observations[key], str):
            raise ValueError("Visual brief field must be text: " + key)
    if not observations.get("material_family"):
        raise ValueError("Visual brief requires material_family")
    with Image.open(image) as png:
        if png.format != "PNG":
            raise ValueError("Reference must be a PNG")
        dimensions = list(png.size)
        png.verify()
    brief = {"reference": {"sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                           "dimensions": dimensions},
             "observation_source": "developer image inspection", **copy.deepcopy(observations)}
    reps = set(observations["representations"])
    candidates = catalog()
    overrides = observations.get("overrides", {})
    if not isinstance(overrides, dict) or not isinstance(overrides.get("materials", {}), dict):
        raise ValueError("Visual brief overrides and overrides.materials must be objects")
    if observations.get("base_preset"):
        chosen = preset_id(observations["base_preset"])
        candidates = [row for row in candidates if row["id"] == chosen]
    # A palette override must affect an existing, used material. Adding a new
    # unused material would validate structurally but would not match the image.
    applicable = []
    for row in candidates:
        candidate = load_and_validate_style_json(preset_path(row["id"]))
        used = {entry.get("material") for entry in candidate["styles"]}
        if set(overrides.get("materials", {})) <= used:
            applicable.append(row)
    if not applicable:
        raise ValueError("Palette overrides must name materials used by the selected base preset")
    candidates = applicable
    # Representation and material match precede palette adjustments. Stable
    # catalog order breaks ties; the decision exposes the limits of this ranker.
    def score(row):
        return (reps <= set(row["representations"]), row["material_family"] == observations["material_family"],
                -len(reps ^ set(row["representations"])))
    base = max(candidates, key=score)
    requested = set(observations["required_capabilities"])
    missing_from_base = requested - set(base["required_capabilities"])
    available = set().union(*(set(row["required_capabilities"]) for row in catalog())) | {"camera.depth_of_field"}
    resolved = resolve_options({"preset": base["id"], "overrides": overrides}, {})
    authored = resolved["style_doc"]
    changed_language = (set(required_capabilities(authored)) - set(base["required_capabilities"]))
    extensive = bool(set(overrides) & {"styles", "geometry"}) or len(overrides.get("materials", {})) > 2
    low = (bool(missing_from_base or changed_language or extensive)
           or not reps <= set(base["representations"]) or base["material_family"] != observations["material_family"])
    decision = {"preset_id": base["qualified_id"], "preset_revision": base["revision"],
                "fit": "low" if low else "high", "overrides": overrides,
                "reasons": ["Developer selected the base after image inspection" if observations.get("base_preset") else
                            "Ranked applicable representation coverage, then observed material family",
                            "Requires a dedicated design" if low else "Existing design plus supported parameter overrides"],
                "unmatched_features": sorted(missing_from_base | changed_language),
                "unsupported_requirements": sorted(requested - available),
                "new_preset_recommended": low,
                "route": "developer/new-preset" if low else "preset-overrides",
                "resolved_spec_sha256": resolved["render_spec"]["sha256"],
                "limitations": "Agent observations and a simple catalog rank; no exact-match or automatic vision claim."}
    return {"visual_brief": brief, "preset_decision": decision}
