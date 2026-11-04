#!/usr/bin/env python3
"""Shared preset validation, structure staging, and Blender command construction."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import math
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

try:
    from cutout_planes import PLANE_NAMES
except Exception:  # pragma: no cover - package import fallback
    from blender_render.cutout_planes import PLANE_NAMES

MACOS_STANDARD_BLENDER_EXE = "/Applications/Blender.app/Contents/MacOS/Blender"
DEFAULT_BLENDER_EXE = MACOS_STANDARD_BLENDER_EXE




def _default_tmp_bridge_root() -> Path:
    override = os.environ.get("PYMOL_BRIDGE_TMP_ROOT", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    user = (os.environ.get("USER") or os.environ.get("LOGNAME") or "").strip()
    if not user:
        try:
            user = f"uid_{os.getuid()}"
        except Exception:
            user = "default"
    return Path(tempfile.gettempdir()) / "pymol_bridge" / user


TMP_BRIDGE_ROOT = _default_tmp_bridge_root()

from jsonschema import Draft202012Validator
from blender_render.presets import PRESET_OUTPUT_FILENAMES, PRESET_STYLE_FILENAMES, preset_path

STYLE_SCHEMA_PATH = Path(__file__).resolve().with_name("style.schema.json")

# Derive accepted keys from the packaged schema instead of duplicating them.
from blender_render.engine import resource_bytes
_SCHEMA = json.loads(resource_bytes(STYLE_SCHEMA_PATH))
_STYLE_VALIDATOR = Draft202012Validator(_SCHEMA)
REQUIRED_STYLE_TOP_KEYS = tuple(_SCHEMA["required"])
STYLE_TOP_LEVEL_OPTIONAL_KEYS = set(_SCHEMA["properties"]) - set(REQUIRED_STYLE_TOP_KEYS)
OUTPUT_KEYS = set(_SCHEMA["properties"]["output"]["properties"])
RENDERER_KEYS = set(_SCHEMA["properties"]["renderer"]["properties"])
CAMERA_KEYS = set(_SCHEMA["properties"]["camera"]["properties"])
WORLD_KEYS = set(_SCHEMA["properties"]["world"]["properties"])
LIGHT_KEYS = set(_SCHEMA["properties"]["lights"]["items"]["properties"])
MATERIAL_KEYS = set(_SCHEMA["properties"]["materials"]["additionalProperties"]["properties"])
STYLE_KEYS = set(_SCHEMA["properties"]["styles"]["items"]["properties"])
COLOR_FOLLOW_KEYS = set(_SCHEMA["properties"]["color_follow"]["properties"])


class RunnerError(RuntimeError):
    """Base runtime error for bridge runner."""


class PayloadValidationError(RunnerError):
    """Invalid PyMOL payload."""


class StyleValidationError(RunnerError):
    """Invalid style JSON."""


def _default_custom_script() -> Path:
    return Path(__file__).resolve().parent / "blender_custom_render.py"


def _default_preset_style(preset: str) -> Path:
    try:
        return preset_path(preset)
    except ValueError as exc:
        raise RunnerError(str(exc)) from exc


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off", ""}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got: {value!r}")




def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(resource_bytes(path))
    except FileNotFoundError as exc:
        raise RunnerError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"Could not parse JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunnerError(f"Expected JSON object in {path}, got {type(payload).__name__}.")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _ensure_tmp_run_dir(run_id: str) -> Path:
    TMP_BRIDGE_ROOT.mkdir(parents=True, exist_ok=True)
    run_dir = TMP_BRIDGE_ROOT / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _expand_path_no_resolve(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).absolute()


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


def _state_coordinate_errors(states: Any) -> list[str]:
    """Per-state coordinates for state animations; shape only, order is checked in Blender."""
    if not isinstance(states, dict) or set(states) != {"base", "coordinates"}:
        return ["'state_coordinates' must contain exactly 'base' and 'coordinates'."]
    coordinates = states["coordinates"]
    if not isinstance(coordinates, list) or not 2 <= len(coordinates) <= 360:
        return ["'state_coordinates.coordinates' must list 2 to 360 states."]
    if type(states["base"]) is not int or not 1 <= states["base"] <= len(coordinates):
        return ["'state_coordinates.base' must number one of the listed states."]
    length = len(coordinates[0]) if isinstance(coordinates[0], list) else 0
    if not length or length % 3:
        return ["Each state must list x, y and z for every atom."]
    for index, values in enumerate(coordinates, 1):
        if not isinstance(values, list) or len(values) != length:
            return [f"State {index} must have the same atoms as state 1."]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            return [f"State {index} coordinates must be finite numbers."]
    return []


def validate_payload_document(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    cif_text = payload.get("cif_text")
    pdb_text = payload.get("pdb_text")

    cif_valid = _looks_like_cif_atom_site(cif_text) if isinstance(cif_text, str) else False
    pdb_valid = _looks_like_pdb_atoms(pdb_text) if isinstance(pdb_text, str) else False

    if not (cif_valid or pdb_valid):
        errors.append(
            "Payload is missing usable structure text. "
            "Provide 'cif_text' with atom-site data and/or 'pdb_text' with ATOM/HETATM records."
        )

    view = payload.get("view")
    if not isinstance(view, dict):
        errors.append("'view' must be an object with a 'pymol_view' list.")
    else:
        pymol_view = view.get("pymol_view")
        if not isinstance(pymol_view, list) or len(pymol_view) != 18:
            errors.append("'view.pymol_view' must contain exactly 18 floats from cmd.get_view().")
        else:
            for idx, value in enumerate(pymol_view):
                try:
                    parsed = float(value)
                except Exception:
                    errors.append(f"'view.pymol_view[{idx}]' is not numeric: {value!r}")
                    continue
                if not math.isfinite(parsed):
                    errors.append(f"'view.pymol_view[{idx}]' is not finite: {value!r}")

        fov_deg = view.get("fov_deg")
        if fov_deg is not None:
            try:
                if float(fov_deg) <= 0:
                    errors.append("'view.fov_deg' must be > 0 when provided.")
            except Exception:
                errors.append("'view.fov_deg' must be numeric when provided.")

        orthoscopic = view.get("orthoscopic")
        if orthoscopic is not None and not isinstance(orthoscopic, bool):
            errors.append("'view.orthoscopic' must be a boolean when provided.")

        viewport = view.get("viewport_px")
        if viewport is not None:
            if (
                not isinstance(viewport, (list, tuple))
                or len(viewport) != 2
                or not all(isinstance(item, (int, float)) for item in viewport)
            ):
                errors.append("'view.viewport_px' must be [width, height] when provided.")

    states = payload.get("state_coordinates")
    contacts = payload.get("polar_contacts")
    if contacts is not None and (not isinstance(contacts, list) or len(contacts) > 256 or any(
            not isinstance(pair, list) or len(pair) != 2 or any(
                not isinstance(key, str) or len(key) > 128 or key.count(':') != 3 for key in pair)
            for pair in contacts)):
        errors.append("polar_contacts must contain at most 256 pairs of bounded atom keys")
    if states is not None:
        errors.extend(_state_coordinate_errors(states))

    if errors:
        bullet = "\n".join(f"- {item}" for item in errors)
        raise PayloadValidationError(f"Payload validation failed:\n{bullet}")
    return payload


def _style_error_path(parts) -> str:
    path = ""
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + str(part)
    return path or "style"


def validate_style_document(style_doc: dict[str, Any]) -> dict[str, Any]:
    """One structural validator; only cross-field semantics are checked below."""
    if not isinstance(style_doc, dict):
        raise StyleValidationError("Style document must be an object")
    try:
        json.dumps(style_doc, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise StyleValidationError("Style values must be finite JSON values") from exc
    errors = []
    for error in _STYLE_VALIDATOR.iter_errors(style_doc):
        # Keep the established actionable error paths for compatibility callers.
        if error.validator == "required" and not error.absolute_path:
            errors.append("Missing required top-level keys: " + error.message)
        else:
            errors.append(_style_error_path(error.absolute_path) + ": " + error.message)

    materials = style_doc.get("materials", {})
    styles = style_doc.get("styles", [])
    for idx, style in enumerate(styles if isinstance(styles, list) else []):
        if not isinstance(style, dict):
            continue
        prefix = f"styles[{idx}]"
        if "nucleic_base_scale" in style and style.get("style") != "cartoon":
            errors.append(f"{prefix}.nucleic_base_scale requires cartoon style")
        if "wireframe_spacing_angstrom" in style and "wireframe_width_angstrom" not in style:
            errors.append(f"{prefix}.wireframe_spacing_angstrom requires wireframe_width_angstrom")
        reference = style.get("material")
        atom_attribute = style.get("atom_color_attribute", "")
        geometry_cfg = style_doc.get("geometry", {})
        if atom_attribute and isinstance(geometry_cfg, dict):
            if str(atom_attribute).startswith("Sequence") and not geometry_cfg.get("chain_sequence_palettes"):
                errors.append(f"{prefix}.atom_color_attribute requires geometry.chain_sequence_palettes")
            if atom_attribute in ("ElementColor", "SequenceElementColor") and not geometry_cfg.get("element_palette"):
                errors.append(f"{prefix}.atom_color_attribute requires geometry.element_palette")
        if style.get("ligand_cutout_mode") == "ellipsoid":
            if style.get("style") != "surface" or style.get("ligand_cutout_isolate") is not True or style.get("ligand_cutout_selection", False):
                errors.append(f"{prefix}: ellipsoid requires an isolated surface without atom selection clipping")
            if style.get("ligand_cutout_frame_mode") != "camera":
                errors.append(f"{prefix}: ellipsoid requires camera framing")
            surface_material = materials.get(reference, {}) if isinstance(materials, dict) and isinstance(reference, str) else {}
            if not isinstance(surface_material, dict) or not surface_material.get("color_attribute"):
                errors.append(f"{prefix}: ellipsoid requires a surface color_attribute")
            output = style_doc.get("output", {})
            animation_cfg = output.get("animation", {}) if isinstance(output, dict) else {}
            if style.get("ligand_cutout_mirror") or (isinstance(animation_cfg, dict) and animation_cfg.get("lid")):
                errors.append(f"{prefix}: ellipsoid does not support mirrored cuts or animated lids")
        if isinstance(reference, str) and isinstance(materials, dict) and reference not in materials:
            errors.append(f"{prefix}.material references '{reference}', which is not defined in materials")
        for key in ("selection", "fallback_selection", "ligand_cutout_selection"):
            selection = style.get(key)
            bounds = selection.get("residue_range") if isinstance(selection, dict) else None
            if (isinstance(bounds, list) and len(bounds) == 2
                    and all(isinstance(v, (int, float)) for v in bounds) and bounds[0] > bounds[1]):
                errors.append(f"{prefix}.{key}.residue_range start must be <= end")
        if (style.get("ligand_cutout_mode", "planes") == "planes"
                and style.get("ligand_cutout_frame_mode") == "preset"
                and "ligand_cutout_frame_axes" not in style):
            errors.append(f"{prefix}.ligand_cutout_frame_axes is required when ligand_cutout_frame_mode is preset")
    camera = style_doc.get("camera", {})
    if isinstance(camera, dict):
        if 'relative_pose' in camera:
            if camera.get('mode') != 'override' or camera.get('projection') not in ('ORTHO', 'PERSP'):
                errors.append('camera.relative_pose requires mode=override and an explicit projection')
            incompatible = {'direction_source', 'yaw_deg', 'pitch_deg', 'roll_deg', 'yaw_offset_deg',
                            'pitch_offset_deg', 'fit', 'fit_margin', 'focus_style', 'distance_scale',
                            'ortho_scale_angstrom', 'preserve_framing'} & camera.keys()
            if incompatible:
                errors.append('camera.relative_pose cannot be combined with ' + ', '.join(sorted(incompatible)))
            pose = camera['relative_pose']
            if isinstance(pose, dict) and all(type(pose.get(key)) in (int, float) for key in ('clip_start', 'clip_end')):
                if pose['clip_end'] <= pose['clip_start']:
                    errors.append('camera.relative_pose.clip_end must exceed clip_start')
        elif {'sensor_fit', 'sensor_width_mm', 'sensor_height_mm'} & camera.keys():
            errors.append('camera sensor settings require relative_pose')
        if {"yaw_deg", "pitch_deg"} & camera.keys() and (
                camera.get("mode") != "override" or camera.get("direction_source", "preset") != "preset"):
            errors.append("camera.yaw_deg/pitch_deg require mode=override and direction_source=preset; use yaw_offset_deg/pitch_offset_deg for PyMOL-relative rotation")
    if isinstance(camera, dict) and "depth_of_field" in camera:
        if camera.get("mode") != "override" or camera.get("projection") != "PERSP":
            errors.append("camera.depth_of_field requires camera.mode=override and camera.projection=PERSP")
        output = style_doc.get("output", {})
        if isinstance(output, dict) and output.get("raw_view"):
            errors.append("camera.depth_of_field is unsupported with output.raw_view")
    world = style_doc.get("world", {})
    gradients = [("world.gradient_direction", world.get("gradient_direction"))] if isinstance(world, dict) else []
    geometry = style_doc.get("geometry", {})
    for name, material in (materials.items() if isinstance(materials, dict) else []):
        if not isinstance(material, dict):
            continue
        gradient = material.get("color_gradient")
        if isinstance(gradient, dict):
            gradients.append((f"materials.{name}.color_gradient.direction", gradient.get("direction")))
        frosted = material.get("frosted")
        if isinstance(frosted, dict):
            low, high = frosted.get("opacity_min"), frosted.get("opacity_max")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low > high:
                errors.append(f"materials.{name}.frosted.opacity_min must be <= opacity_max")
        if material.get("color_attribute") == "ElementColor" and not (isinstance(geometry, dict) and geometry.get("element_palette")):
            errors.append(f"materials.{name}.color_attribute ElementColor requires geometry.element_palette")
        if material.get("color_attribute") and gradient:
            errors.append(f"materials.{name}: choose color_attribute or color_gradient, not both")
        for field in ("color_attribute", "emission_attribute"):
            attribute = material.get(field, "")
            geometry_cfg = geometry if isinstance(geometry, dict) else {}
            if isinstance(attribute, str) and attribute.startswith("Sequence") and not geometry_cfg.get("chain_sequence_palettes"):
                errors.append(f"materials.{name}.{field} requires geometry.chain_sequence_palettes")
            if attribute in ("ElementColor", "SequenceElementColor") and not geometry_cfg.get("element_palette"):
                errors.append(f"materials.{name}.{field} requires geometry.element_palette")
        if material.get("emission_attribute") and material.get("edge_highlight"):
            errors.append(f"materials.{name}: emission_attribute cannot be combined with edge_highlight")
        if material.get("emission_halo") and not material.get("emission_strength", 0):
            errors.append(f"materials.{name}.emission_halo requires positive emission_strength")
        glow = material.get("glow")
        if isinstance(glow, dict) and float(glow.get("strength", 0)) > 0 and "emission_color" not in material:
            errors.append(f"materials.{name}.glow requires emission_color")
    animation = style_doc.get("output", {}).get("animation") if isinstance(style_doc.get("output"), dict) else None
    material_rows = list(materials.values()) if isinstance(materials, dict) else []
    halos = [m["emission_halo"] for m in material_rows if isinstance(m, dict) and "emission_halo" in m]
    if halos and (any(h != halos[0] for h in halos) or any(m.get("glow") for m in material_rows if isinstance(m, dict))):
        errors.append("emission_halo uses one shared emission pass: settings must agree and glow cannot be combined")
    if isinstance(animation, dict) and animation.get("lid") and not any(
            isinstance(entry, dict) and entry.get("ligand_cutout_radius", 0) for entry in style_doc.get("styles", [])):
        errors.append("output.animation.lid needs a preset with a ligand cutout, such as cyan_mint_cutaway")
    if isinstance(animation, dict) and animation.get("states"):
        if isinstance(geometry, dict) and geometry.get("polar_contacts"):
            errors.append("state animations cannot use static polar contact guides")
        for entry in style_doc.get("styles", []):
            if isinstance(entry, dict) and (entry.get("ligand_cutout_radius", 0) or "wireframe_width_angstrom" in entry):
                errors.append(f"styles.{entry.get('name')}: state animations cannot follow cut surfaces or wire overlays; choose a preset without them")
    for path, direction in gradients:
        if isinstance(direction, list) and direction and all(isinstance(v, (int, float)) for v in direction) and not any(direction):
            errors.append(f"{path} must be nonzero")
    output = style_doc.get("output", {})
    if not errors and isinstance(output, dict) and "animation" in output:
        from .animation import normalize
        try:
            normalize(output["animation"], output)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise StyleValidationError("Style JSON validation failed:\n" + "\n".join("- " + e for e in errors))
    return style_doc


def load_and_validate_style_json(path: Path) -> dict[str, Any]:
    return validate_style_document(_load_json(path))


def _ensure_suffix_png(name: str) -> str:
    if name.lower().endswith(".png"):
        return name
    return f"{name}.png"


def _resolve_output_png(
    *,
    output_dir: str,
    output_name: str,
    preset: str | None,
    style_doc: dict[str, Any] | None,
) -> Path:
    if output_dir:
        out_dir = _expand_path_no_resolve(output_dir)
    else:
        out_dir = Path.cwd() / "renders" / _now_stamp()
    out_dir.mkdir(parents=True, exist_ok=True)

    if output_name:
        filename = _ensure_suffix_png(output_name)
    elif preset is not None:
        filename = PRESET_OUTPUT_FILENAMES[preset]
    else:
        style_filename = None
        if style_doc is not None:
            output = style_doc.get("output")
            if isinstance(output, dict):
                style_filename = output.get("filename")
        if isinstance(style_filename, str) and style_filename.strip():
            filename = _ensure_suffix_png(style_filename.strip())
        else:
            filename = "custom_render.png"

    return out_dir / filename


def _materialize_payload_bundle(payload: dict[str, Any], run_id: str) -> tuple[Path, Path, Path, Path | None, Path]:
    temp_root = _ensure_tmp_run_dir(run_id)
    bundle_dir = temp_root / "bundle"
    structures_dir = bundle_dir / "structures"
    structures_dir.mkdir(parents=True, exist_ok=True)

    pdb_text = payload.get("pdb_text")
    cif_text = payload.get("cif_text")
    cif_path: Path | None = None
    pdb_path: Path | None = None
    if isinstance(cif_text, str) and _looks_like_cif_atom_site(cif_text):
        cif_path = structures_dir / "structure.cif"
        cif_path.write_text(cif_text, encoding="utf-8")
    if isinstance(pdb_text, str) and _looks_like_pdb_atoms(pdb_text):
        pdb_path = structures_dir / "structure.pdb"
        pdb_path.write_text(pdb_text, encoding="utf-8")

    if cif_path is None and pdb_path is None:
        raise RunnerError(
            "Payload did not contain usable structure text. "
            "Expected CIF atom-site data or PDB ATOM/HETATM records."
        )
    structure_path = cif_path if cif_path is not None else pdb_path
    fallback_path = pdb_path if (cif_path is not None and pdb_path is not None) else None

    view_path = bundle_dir / "view.json"
    view_doc = dict(payload["view"])
    view_doc.pop("state_coordinates_file", None)  # Only the staged payload can supply coordinates.
    color_hints = payload.get("color_hints")
    if isinstance(color_hints, dict):
        view_doc["color_hints"] = color_hints
    ss_hints = payload.get("ss_hints")
    if isinstance(ss_hints, dict):
        view_doc["ss_hints"] = ss_hints
    if "polar_contacts" in payload:
        view_doc["polar_contacts"] = payload["polar_contacts"]
    states = payload.get("state_coordinates")
    if isinstance(states, dict):
        states_path = bundle_dir / "states.json"
        states_path.write_text(json.dumps(states), encoding="utf-8")
        view_doc["state_coordinates_file"] = str(states_path)
    view_path.write_text(json.dumps(view_doc, indent=2), encoding="utf-8")

    payload_path = temp_root / "payload.json"
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return temp_root, bundle_dir, structure_path, fallback_path, view_path


def _run_subprocess(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    except FileNotFoundError as exc:
        missing = exc.filename or (command[0] if command else "<unknown>")
        raise RunnerError(
            "Executable not found while launching command.\n"
            f"Missing executable: {missing}\n"
            f"Command: {' '.join(shlex.quote(item) for item in command)}\n"
            "Configure the local executable with blender-render setup --blender /path/to/blender."
        ) from exc


def _parse_last_json_object(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise RunnerError("Could not parse JSON result from interactive/local runner output.")




def _blender_executable_exists(path_value: str) -> bool:
    if not path_value:
        return False
    path = Path(path_value).expanduser()
    if path.is_file():
        return True
    if not path.is_absolute():
        resolved = shutil.which(path_value)
        if resolved and Path(resolved).is_file():
            return True
    return False


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _detect_host_blender_executable() -> str:
    candidates: list[str | None] = []
    if _is_macos():
        candidates.append(MACOS_STANDARD_BLENDER_EXE)
    candidates.extend(
        [
            shutil.which("blender"),
            "/usr/bin/blender",
            "/usr/local/bin/blender",
            "/opt/blender/blender",
        ]
    )
    for candidate in candidates:
        if not candidate:
            continue
        if _blender_executable_exists(candidate):
            return str(Path(candidate).expanduser())
    return ""






def build_local_blender_command(
    *,
    blender_exe: str,
    preset: str | None,
    structure_path: Path,
    view_json_path: Path,
    style_json_path: Path | None,
    output_png: Path,
    custom_script: Path,
    run_manifest_path: Path | None = None,
) -> list[str]:
    if style_json_path is None:
        raise RunnerError("JSON preset/style is required. Provide --style-json or --preset.")
    command = [
        blender_exe,
        "--background",
        "--python",
        str(custom_script),
        "--",
        "--structure",
        str(structure_path),
        "--view-json",
        str(view_json_path),
        "--style-json",
        str(style_json_path),
        "--output-png",
        str(output_png),
    ]
    if run_manifest_path is not None:
        command.extend(["--run-manifest", str(run_manifest_path)])

    return command


def _debug_process_text(result: subprocess.CompletedProcess[str]) -> str:
    return f"stdout:\n{(result.stdout or '').strip()}\nstderr:\n{(result.stderr or '').strip()}"


def _tail_text(value: str, max_chars: int = 10000) -> str:
    text = value or ""
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _extract_gpu_info(stdout: str, stderr: str) -> list[str]:
    lines: list[str] = []
    for source in ((stdout or "").splitlines(), (stderr or "").splitlines()):
        for line in source:
            stripped = line.strip()
            if "Bridge Cycles GPU:" in stripped or "Bridge Custom Cycles GPU:" in stripped:
                lines.append(stripped)
    return lines[-12:]


def _apply_rough_style_overrides(style_doc: dict[str, Any]) -> dict[str, Any]:
    rough_doc = json.loads(json.dumps(style_doc))
    output = rough_doc.get("output")
    if not isinstance(output, dict):
        return rough_doc

    width = output.get("width")
    height = output.get("height")
    samples = output.get("samples")
    try:
        width_i = int(width)
    except Exception:
        width_i = 1600
    try:
        height_i = int(height)
    except Exception:
        height_i = 900
    try:
        samples_i = int(samples)
    except Exception:
        samples_i = 128

    output["width"] = max(320, width_i // 2)
    output["height"] = max(240, height_i // 2)
    output["samples"] = max(8, min(32, max(1, samples_i // 2)))
    rough_doc["output"] = output
    return rough_doc


def _apply_color_follow_override(style_doc: dict[str, Any], enabled: bool) -> dict[str, Any]:
    if not enabled and "color_follow" not in style_doc:
        return style_doc
    styled = json.loads(json.dumps(style_doc))
    cfg = styled.get("color_follow")
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["enabled"] = enabled
    styled["color_follow"] = cfg
    return styled


def _apply_raw_view_override(style_doc: dict[str, Any], enabled: bool) -> dict[str, Any]:
    if not enabled:
        return style_doc
    styled = json.loads(json.dumps(style_doc))
    output = styled.get("output")
    if not isinstance(output, dict):
        output = {}
    output["raw_view"] = True
    styled["output"] = output
    return styled


def _apply_save_blend_override(style_doc: dict[str, Any], enabled: bool, blend_output: str) -> dict[str, Any]:
    if not enabled and not str(blend_output or "").strip():
        return style_doc
    styled = json.loads(json.dumps(style_doc))
    output = styled.get("output")
    if not isinstance(output, dict):
        output = {}
    if enabled:
        output["save_blend"] = True
    blend_value = str(blend_output or "").strip()
    if blend_value:
        output["save_blend"] = True
        output["blend_filename"] = blend_value
    styled["output"] = output
    return styled


def _resolve_output_blend(output_png: Path, style_doc: dict[str, Any] | None) -> Path | None:
    if not isinstance(style_doc, dict):
        return None
    output = style_doc.get("output")
    if not isinstance(output, dict):
        return None
    if not bool(output.get("save_blend", False)):
        return None

    configured = str(output.get("blend_filename", "") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_absolute():
            return configured_path
        return output_png.parent / configured_path
    return output_png.with_suffix(".blend")


def _style_entries(style_doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(style_doc, dict):
        return []
    styles = style_doc.get("styles")
    if not isinstance(styles, list):
        return []
    return [entry for entry in styles if isinstance(entry, dict)]


def _resolve_sticks_backend_name(style_doc: dict[str, Any] | None) -> str:
    for style_entry in _style_entries(style_doc):
        if str(style_entry.get("style", "")).strip().lower() == "sticks":
            return "mn_native_sticks"
    return "none"


def _resolve_polymer_cartoon_backend_name(style_doc: dict[str, Any] | None) -> str:
    for style_entry in _style_entries(style_doc):
        if str(style_entry.get("style", "")).strip().lower() != "cartoon":
            continue
        selection = style_entry.get("selection")
        if isinstance(selection, dict):
            if bool(selection.get("polymer")):
                return "mn_cartoon"
            query = str(selection.get("query", "")).strip().lower()
            if "polymer" in query:
                return "mn_cartoon"
    return "none"


def _style_entry_targets_ligand(style_entry: dict[str, Any]) -> bool:
    style_name = str(style_entry.get("name", "")).strip().lower()
    if "ligand" in style_name:
        return True
    selection = style_entry.get("selection")
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


def _resolve_ligand_backend_name(style_doc: dict[str, Any] | None) -> str:
    for style_entry in _style_entries(style_doc):
        style_kind = str(style_entry.get("style", "")).strip().lower()
        if style_kind != "sticks":
            continue
        if _style_entry_targets_ligand(style_entry):
            return "mn_native_sticks"
    return "none"


def _style_source_from_path(path: Path) -> str:
    suffix = path.suffix.strip().lower()
    if suffix == ".cif":
        return "cif"
    if suffix == ".pdb":
        return "pdb"
    return suffix.lstrip(".") or "unknown"


def _write_run_manifest(path: Path, payload: dict[str, Any], *, chosen_structure_path: Path, style_doc: dict[str, Any] | None) -> None:
    dss_doc = payload.get("dss")
    dss_ran = bool(isinstance(dss_doc, dict) and dss_doc.get("ran", False))
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = _load_json(path)
            if isinstance(loaded, dict):
                existing = loaded
        except Exception:
            existing = {}
    polymer_backend = str(existing.get("polymer_backend", "")).strip()
    if not polymer_backend:
        polymer_backend = _resolve_polymer_cartoon_backend_name(style_doc)
    ligand_backend = str(existing.get("ligand_backend", "")).strip()
    if not ligand_backend:
        ligand_backend = _resolve_ligand_backend_name(style_doc)
    ligand_atom_spheres_value = existing.get("ligand_atom_spheres")
    if not isinstance(ligand_atom_spheres_value, bool):
        ligand_atom_spheres_value = False
    manifest: dict[str, Any] = dict(existing)
    manifest.update(
        {
        "structure_source": _style_source_from_path(chosen_structure_path),
        "chosen_structure_file": chosen_structure_path.name,
        "chosen_structure_path": str(chosen_structure_path),
        "dss_ran": dss_ran,
        "polymer_backend": polymer_backend,
        "ligand_backend": ligand_backend,
        "ligand_atom_spheres": bool(ligand_atom_spheres_value),
        # Backward-compatible aliases.
        "sticks_backend": ligand_backend if ligand_backend else _resolve_sticks_backend_name(style_doc),
        "polymer_cartoon_backend": polymer_backend,
        "generated_at_utc": _utc_now_iso(),
        }
    )
    if isinstance(dss_doc, dict):
        manifest["dss"] = dss_doc
    manifest["input_provenance"] = payload.get("provenance", {})
    manifest["structure_summary"] = payload.get("structure_summary", {})
    manifest["input_camera"] = payload.get("view", {})
    manifest["secondary_structure"] = payload.get("ss_hints", {}).get("counts", {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _is_preset_1_gate_target(*, preset: str | None, style_json_path: str | None, style_doc: dict[str, Any] | None) -> bool:
    # Compatibility guard for the reference representation, never for a name
    # or output filename. Colors/materials/camera can change without losing it;
    # a different representation must not inherit the reference's requirements.
    if not isinstance(style_doc, dict):
        return False
    def layout(doc):
        return [(entry.get("style"), entry.get("selection")) for entry in _style_entries(doc)]
    reference = _load_json(_default_preset_style("preset_1_interface"))
    return layout(style_doc) == layout(reference)


def _enforce_preset_1_backend_gates(*, manifest: dict[str, Any], manifest_path: Path) -> None:
    structure_source = str(manifest.get("structure_source", "")).strip().lower()
    dss_ran = bool(manifest.get("dss_ran", False))
    polymer_backend = str(manifest.get("polymer_backend", "")).strip().lower()
    ligand_backend = str(manifest.get("ligand_backend", "")).strip().lower()
    ligand_atom_spheres = bool(manifest.get("ligand_atom_spheres", True))

    errors: list[str] = []
    if structure_source != "cif":
        errors.append(f"structure source must be CIF-first; got structure_source={structure_source or '<empty>'}")
    if not dss_ran:
        errors.append("secondary-structure assignment must run before export (dss_ran=false)")
    if polymer_backend != "mn_cartoon":
        errors.append(f"polymer backend must be cartoon secondary structure; got polymer_backend={polymer_backend or '<empty>'}")
    if ligand_backend != "mn_native_sticks":
        errors.append(f"ligand backend must be non-legacy native sticks; got ligand_backend={ligand_backend or '<empty>'}")
    if ligand_atom_spheres:
        errors.append("ligand atom spheres must be disabled; got ligand_atom_spheres=true")

    if errors:
        bullet = "\n".join(f"- {item}" for item in errors)
        raise RunnerError(
            "Preset gate failure for preset_1_interface.\n"
            f"{bullet}\n"
            f"Manifest: {manifest_path}"
        )
