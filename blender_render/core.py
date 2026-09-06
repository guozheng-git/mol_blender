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
DEFAULT_APPTAINER_BLENDER_EXE = "/usr/local/bin/blender"
DEFAULT_INTERACTIVE_LAUNCHER = "qlogin -p gpu --gres=gpu:a4000:1 --mem=64g -c 4"


def _default_session_root() -> Path:
    override = os.environ.get("PYMOL_BRIDGE_SESSION_ROOT", "").strip()
    if override:
        return Path(override).expanduser().absolute()
    # Must be visible from login + compute nodes for persistent interactive IPC.
    return (Path.home() / ".pymol_bridge" / "sessions").absolute()


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


SESSION_ROOT = _default_session_root()
TMP_BRIDGE_ROOT = _default_tmp_bridge_root()

PRESET_OUTPUT_FILENAMES: dict[str, str] = {
    "preset_1_interface": "01_opaque_pastel_ses_interface.png",
    "preset_2_frosted_blue": "02_saturated_blue_core_frosted_shell.png",
    "preset_3_black_tubes": "03_pastel_tube_cartoon_black.png",
    "preset_4_milky_green": "04_milky_white_green_shell_cyan_dna.png",
    "preset_5_black_interface": "05_black_background_interface.png",
    "dried_paint_green": "dried_paint_green.png",
}

PRESET_STYLE_FILENAMES: dict[str, str] = {
    "preset_1_interface": "style_preset_1_opaque_pastel.json",
    "preset_2_frosted_blue": "style_preset_2_frosted_blue.json",
    "preset_3_black_tubes": "style_preset_3_black_tubes.json",
    "preset_4_milky_green": "style_preset_4_milky_green.json",
    "preset_5_black_interface": "style_preset_5_black_interface.json",
    "dried_paint_green": "style_preset_dried_paint_green.json",
}

SUPPORTED_STYLE_KINDS = {
    "surface",
    "cartoon",
    "sticks",
    "spheres",
    "ribbon",
    "wire",
}

SELECTION_DSL_KEYS = {
    "polymer",
    "nucleic",
    "ligand",
    "chain",
    "residue_range",
    "query",
    "within_ligand_radius",
    "exclude_within_ligand_radius",
    "expand_to_residue",
}
STYLE_SCHEMA_PATH = Path(__file__).resolve().with_name("style.schema.json")
STYLE_TOP_LEVEL_OPTIONAL_KEYS = {"color_follow"}
OUTPUT_KEYS = {"format", "width", "height", "samples", "filename", "save_blend", "blend_filename", "raw_view"}
RENDERER_KEYS = {
    "engine",
    "device",
    "compute_device_type",
    "transparent_background",
    "adaptive_sampling",
    "require_gpu",
    "exposure",
    "gamma",
    "view_transform",
    "look",
    "seed",
    "denoise",
}
CAMERA_KEYS = {
    "mode",
    "lens_mm",
    "shift_x",
    "shift_y",
    "world_scale",
    "anchor_model_center",
    "override_intrinsics",
    "direction_source",
    "yaw_offset_deg",
    "pitch_offset_deg",
    "yaw_deg",
    "pitch_deg",
    "distance_scale",
    "focus_style",
    "clip_start",
    "clip_end",
}
WORLD_KEYS = {"background_type", "color", "top_color", "bottom_color", "strength"}
LIGHT_KEYS = {
    "name",
    "type",
    "energy",
    "size",
    "size_scale",
    "min_size",
    "color",
    "location",
    "rotation_deg",
    "relative_vector",
    "distance_scale",
    "look_at_target",
    "rotate_with_pymol_yaw",
}
MATERIAL_KEYS = {
    "base_color",
    "shadow_color",
    "highlight_color",
    "roughness",
    "specular",
    "transmission",
    "alpha",
    "emission_color",
    "emission_strength",
    "ao_strength",
    "rim_strength",
    "rim_blend",
    "rim_power",
    "ao_distance",
    "coat",
}
STYLE_KEYS = {
    "name",
    "style",
    "material",
    "color",
    "selection",
    "fallback_selection",
    "radius",
    "quality",
    "probe_radius",
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
    "ligand_cutout_radius",
    "ligand_cutout_offset",
    "ligand_cutout_auto_side",
    "ligand_cutout_axis_mode",
    "ligand_cutout_flatten_z",
    "ligand_cutout_width_scale",
    "ligand_cutout_depth_scale",
    "ligand_cutout_height_scale",
    "ligand_cutout_one_sided",
    "ligand_cutout_one_sided_shift_scale",
    "ligand_cutout_cap_mode",
    "ligand_cutout_selection",
    "ligand_cutout_mirror",
    "ligand_cutout_isolate",
    "ligand_cutout_mode",
    "ligand_cutout_frame_mode",
    "ligand_cutout_frame_axes",
    "ligand_cutout_planes_enabled",
    "ligand_cutout_plane_scales",
    "ligand_cutout_seal_mode",
    "ligand_cutout_seal_depth_scale",
    "ligand_cutout_seal_region_scale",
    "del_solvent",
}
COLOR_FOLLOW_KEYS = {"enabled", "mode", "mix", "saturation_boost", "value_boost"}
LIGAND_CUTOUT_FRAME_AXIS_KEYS = {"x", "y", "z"}
LIGAND_CUTOUT_PLANE_KEYS = set(PLANE_NAMES)

try:
    import jsonschema as _jsonschema
except Exception:  # pragma: no cover - optional dependency
    _jsonschema = None


# Derive accepted keys from the packaged schema instead of duplicating them.
_SCHEMA = json.loads(STYLE_SCHEMA_PATH.read_text(encoding="utf-8"))
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
    filename = PRESET_STYLE_FILENAMES.get(preset)
    if not filename:
        raise RunnerError(
            f"Unknown preset {preset!r}. Expected one of: {', '.join(sorted(PRESET_STYLE_FILENAMES))}."
        )
    return Path(__file__).resolve().parent / filename


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


def _default_remote_python_executable() -> str:
    override = os.environ.get("PYMOL_BRIDGE_REMOTE_PYTHON", "").strip()
    if override:
        return override
    if shutil.which("python3"):
        return "python3"
    if shutil.which("python"):
        return "python"
    return sys.executable


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
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

    if errors:
        bullet = "\n".join(f"- {item}" for item in errors)
        raise PayloadValidationError(f"Payload validation failed:\n{bullet}")
    return payload


def _validate_selection_dsl(selection: Any, field_path: str, errors: list[str]) -> None:
    if not isinstance(selection, dict):
        errors.append(f"'{field_path}' must be an object.")
        return

    extra_keys = sorted(set(selection) - SELECTION_DSL_KEYS)
    if extra_keys:
        errors.append(
            f"'{field_path}' contains unsupported keys: {', '.join(extra_keys)}. "
            f"Allowed keys: {', '.join(sorted(SELECTION_DSL_KEYS))}."
        )

    for key in ("polymer", "nucleic", "ligand"):
        if key in selection and not isinstance(selection[key], bool):
            errors.append(f"'{field_path}.{key}' must be boolean.")

    if "chain" in selection:
        chains = selection["chain"]
        if isinstance(chains, str):
            if not chains.strip():
                errors.append(f"'{field_path}.chain' cannot be empty.")
        elif isinstance(chains, list):
            if not chains:
                errors.append(f"'{field_path}.chain' cannot be empty.")
            elif not all(isinstance(item, str) and item.strip() for item in chains):
                errors.append(f"'{field_path}.chain' list must contain non-empty strings.")
        else:
            errors.append(f"'{field_path}.chain' must be a string or list of strings.")

    if "residue_range" in selection:
        value = selection["residue_range"]
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            errors.append(f"'{field_path}.residue_range' must be [start, end].")
        else:
            try:
                start = int(value[0])
                end = int(value[1])
                if start > end:
                    errors.append(f"'{field_path}.residue_range' start must be <= end.")
            except Exception:
                errors.append(f"'{field_path}.residue_range' values must be integers.")

    if "query" in selection and not isinstance(selection["query"], str):
        errors.append(f"'{field_path}.query' must be a string.")

    for key in ("within_ligand_radius", "exclude_within_ligand_radius"):
        if key in selection:
            try:
                if float(selection[key]) < 0:
                    errors.append(f"'{field_path}.{key}' must be >= 0.")
            except Exception:
                errors.append(f"'{field_path}.{key}' must be numeric.")

    if "expand_to_residue" in selection and not isinstance(selection.get("expand_to_residue"), bool):
        errors.append(f"'{field_path}.expand_to_residue' must be boolean.")


REQUIRED_STYLE_TOP_KEYS = [
    "version",
    "output",
    "renderer",
    "camera",
    "world",
    "lights",
    "materials",
    "styles",
]


def validate_style_document(style_doc: dict[str, Any]) -> dict[str, Any]:
    import jsonschema
    errors: list[str] = []
    if not isinstance(style_doc, dict):
        raise StyleValidationError("Style document must be an object")
    for error in jsonschema.Draft202012Validator(_SCHEMA).iter_errors(style_doc):
        errors.append(".".join(str(p) for p in error.absolute_path) + ": " + error.message)
    try:
        json.dumps(style_doc, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise StyleValidationError("Style values must be finite JSON values") from exc

    missing = [key for key in REQUIRED_STYLE_TOP_KEYS if key not in style_doc]
    if missing:
        errors.append(f"Missing required top-level keys: {', '.join(missing)}")
    extra_top = sorted(
        set(style_doc.keys()) - (set(REQUIRED_STYLE_TOP_KEYS) | STYLE_TOP_LEVEL_OPTIONAL_KEYS)
    )
    if extra_top:
        errors.append(
            "Style JSON contains unsupported top-level keys: "
            f"{', '.join(extra_top)}. "
            f"Allowed top-level keys: {', '.join(sorted(set(REQUIRED_STYLE_TOP_KEYS) | STYLE_TOP_LEVEL_OPTIONAL_KEYS))}."
        )

    version = style_doc.get("version")
    if version is not None and not isinstance(version, str):
        errors.append("'version' must be a string, for example '1.0'.")

    output = style_doc.get("output")
    if not isinstance(output, dict):
        errors.append("'output' must be an object.")
    else:
        extra_output = sorted(set(output.keys()) - OUTPUT_KEYS)
        if extra_output:
            errors.append(
                "'output' contains unsupported keys: "
                f"{', '.join(extra_output)}. "
                f"Allowed keys: {', '.join(sorted(OUTPUT_KEYS))}."
            )
        for key in ("width", "height", "samples"):
            value = output.get(key)
            if not isinstance(value, int) or value <= 0:
                errors.append(f"'output.{key}' must be a positive integer.")
        image_format = output.get("format")
        if not isinstance(image_format, str) or image_format.upper() not in {"PNG", "JPEG", "TIFF", "OPEN_EXR"}:
            errors.append("'output.format' must be one of PNG/JPEG/TIFF/OPEN_EXR.")
        if "save_blend" in output and not isinstance(output.get("save_blend"), bool):
            errors.append("'output.save_blend' must be boolean when provided.")
        if "blend_filename" in output:
            blend_filename = output.get("blend_filename")
            if not isinstance(blend_filename, str) or not blend_filename.strip():
                errors.append("'output.blend_filename' must be a non-empty string when provided.")
        if "raw_view" in output and not isinstance(output.get("raw_view"), bool):
            errors.append("'output.raw_view' must be boolean when provided.")

    renderer = style_doc.get("renderer")
    if not isinstance(renderer, dict):
        errors.append("'renderer' must be an object.")
    else:
        extra_renderer = sorted(set(renderer.keys()) - RENDERER_KEYS)
        if extra_renderer:
            errors.append(
                "'renderer' contains unsupported keys: "
                f"{', '.join(extra_renderer)}. "
                f"Allowed keys: {', '.join(sorted(RENDERER_KEYS))}."
            )
        engine = renderer.get("engine")
        if not isinstance(engine, str) or engine.upper() not in {"CYCLES", "BLENDER_EEVEE", "EEVEE"}:
            errors.append("'renderer.engine' must be CYCLES, BLENDER_EEVEE, or EEVEE.")

    camera = style_doc.get("camera")
    if not isinstance(camera, dict):
        errors.append("'camera' must be an object.")
    else:
        extra_camera = sorted(set(camera.keys()) - CAMERA_KEYS)
        if extra_camera:
            errors.append(
                "'camera' contains unsupported keys: "
                f"{', '.join(extra_camera)}. "
                f"Allowed keys: {', '.join(sorted(CAMERA_KEYS))}."
            )

    world = style_doc.get("world")
    if not isinstance(world, dict):
        errors.append("'world' must be an object.")
    else:
        extra_world = sorted(set(world.keys()) - WORLD_KEYS)
        if extra_world:
            errors.append(
                "'world' contains unsupported keys: "
                f"{', '.join(extra_world)}. "
                f"Allowed keys: {', '.join(sorted(WORLD_KEYS))}."
            )

    lights = style_doc.get("lights")
    if not isinstance(lights, list):
        errors.append("'lights' must be an array.")
    else:
        for idx, light in enumerate(lights):
            if not isinstance(light, dict):
                errors.append(f"'lights[{idx}]' must be an object.")
                continue
            extra_light = sorted(set(light.keys()) - LIGHT_KEYS)
            if extra_light:
                errors.append(
                    f"'lights[{idx}]' contains unsupported keys: {', '.join(extra_light)}. "
                    f"Allowed keys: {', '.join(sorted(LIGHT_KEYS))}."
                )
            light_type = light.get("type")
            if not isinstance(light_type, str) or light_type.upper() not in {"AREA", "SUN", "POINT", "SPOT"}:
                errors.append(f"'lights[{idx}].type' must be AREA/SUN/POINT/SPOT.")

    materials = style_doc.get("materials")
    if not isinstance(materials, dict) or not materials:
        errors.append("'materials' must be a non-empty object keyed by material name.")
    else:
        for mat_name, mat_def in materials.items():
            if not isinstance(mat_name, str) or not mat_name.strip():
                errors.append("Material names must be non-empty strings.")
                continue
            if not isinstance(mat_def, dict):
                errors.append(f"'materials.{mat_name}' must be an object.")
                continue
            extra_mat_keys = sorted(set(mat_def.keys()) - MATERIAL_KEYS)
            if extra_mat_keys:
                errors.append(
                    f"'materials.{mat_name}' contains unsupported keys: {', '.join(extra_mat_keys)}. "
                    f"Allowed keys: {', '.join(sorted(MATERIAL_KEYS))}."
                )
            base_color = mat_def.get("base_color")
            if base_color is not None and (
                not isinstance(base_color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", base_color)
            ):
                errors.append(f"'materials.{mat_name}.base_color' must be #RRGGBB.")

    styles = style_doc.get("styles")
    if not isinstance(styles, list) or not styles:
        errors.append("'styles' must be a non-empty array.")
    else:
        for idx, style in enumerate(styles):
            field_base = f"styles[{idx}]"
            if not isinstance(style, dict):
                errors.append(f"'{field_base}' must be an object.")
                continue
            extra_style_keys = sorted(set(style.keys()) - STYLE_KEYS)
            if extra_style_keys:
                errors.append(
                    f"'{field_base}' contains unsupported keys: {', '.join(extra_style_keys)}. "
                    f"Allowed keys: {', '.join(sorted(STYLE_KEYS))}."
                )

            for key in ("name", "style", "selection", "material"):
                if key not in style:
                    errors.append(f"'{field_base}.{key}' is required.")

            style_name = style.get("name")
            if style_name is not None and not isinstance(style_name, str):
                errors.append(f"'{field_base}.name' must be a string.")

            style_kind = style.get("style")
            if style_kind is not None:
                if not isinstance(style_kind, str):
                    errors.append(f"'{field_base}.style' must be a string.")
                elif style_kind not in SUPPORTED_STYLE_KINDS:
                    errors.append(
                        f"'{field_base}.style' must be one of {', '.join(sorted(SUPPORTED_STYLE_KINDS))}."
                    )

            selection = style.get("selection")
            if selection is not None:
                _validate_selection_dsl(selection, f"{field_base}.selection", errors)
            fallback_selection = style.get("fallback_selection")
            if fallback_selection is not None:
                _validate_selection_dsl(fallback_selection, f"{field_base}.fallback_selection", errors)

            material_ref = style.get("material")
            if isinstance(material_ref, str) and isinstance(materials, dict):
                if material_ref not in materials:
                    errors.append(
                        f"'{field_base}.material' references '{material_ref}', which is not defined in 'materials'."
                    )
            elif material_ref is not None and not isinstance(material_ref, str):
                errors.append(f"'{field_base}.material' must be a string.")

            if "ligand_cutout_radius" in style:
                try:
                    if float(style["ligand_cutout_radius"]) < 0:
                        errors.append(f"'{field_base}.ligand_cutout_radius' must be >= 0.")
                except Exception:
                    errors.append(f"'{field_base}.ligand_cutout_radius' must be numeric.")
            if "ligand_cutout_offset" in style:
                try:
                    float(style["ligand_cutout_offset"])
                except Exception:
                    errors.append(f"'{field_base}.ligand_cutout_offset' must be numeric.")
            if "ligand_cutout_auto_side" in style and not isinstance(style.get("ligand_cutout_auto_side"), bool):
                errors.append(f"'{field_base}.ligand_cutout_auto_side' must be boolean.")
            if "ligand_cutout_one_sided" in style and not isinstance(style.get("ligand_cutout_one_sided"), bool):
                errors.append(f"'{field_base}.ligand_cutout_one_sided' must be boolean.")
            if "ligand_cutout_flatten_z" in style and not isinstance(style.get("ligand_cutout_flatten_z"), bool):
                errors.append(f"'{field_base}.ligand_cutout_flatten_z' must be boolean.")
            if "ligand_cutout_isolate" in style and not isinstance(style.get("ligand_cutout_isolate"), bool):
                errors.append(f"'{field_base}.ligand_cutout_isolate' must be boolean.")
            if "ligand_cutout_axis_mode" in style:
                axis_mode = str(style.get("ligand_cutout_axis_mode", "")).strip().lower()
                if axis_mode not in {"interface", "camera"}:
                    errors.append(
                        f"'{field_base}.ligand_cutout_axis_mode' must be one of: interface, camera."
                    )
            if "ligand_cutout_cap_mode" in style:
                cap_mode = str(style.get("ligand_cutout_cap_mode", "")).strip().lower()
                if cap_mode not in {"panel", "none"}:
                    errors.append(
                        f"'{field_base}.ligand_cutout_cap_mode' must be one of: panel, none."
                    )
            cutout_mode = str(style.get("ligand_cutout_mode", "planes")).strip().lower()
            if "ligand_cutout_mode" in style and cutout_mode != "planes":
                errors.append(f"'{field_base}.ligand_cutout_mode' must be planes.")
            frame_mode = str(style.get("ligand_cutout_frame_mode", "preset")).strip().lower()
            if "ligand_cutout_frame_mode" in style and frame_mode not in {"preset", "interface", "camera"}:
                errors.append(f"'{field_base}.ligand_cutout_frame_mode' must be one of: preset, interface, camera.")
            if "ligand_cutout_frame_axes" in style:
                frame_axes = style.get("ligand_cutout_frame_axes")
                if not isinstance(frame_axes, dict):
                    errors.append(f"'{field_base}.ligand_cutout_frame_axes' must be an object.")
                else:
                    extra_axis_keys = sorted(set(frame_axes.keys()) - LIGAND_CUTOUT_FRAME_AXIS_KEYS)
                    if extra_axis_keys:
                        errors.append(
                            f"'{field_base}.ligand_cutout_frame_axes' contains unsupported keys: "
                            f"{', '.join(extra_axis_keys)}. "
                            "Allowed keys: x, y, z."
                        )
                    for axis_key in ("x", "y", "z"):
                        axis_value = frame_axes.get(axis_key)
                        if axis_value is None:
                            errors.append(f"'{field_base}.ligand_cutout_frame_axes.{axis_key}' is required.")
                            continue
                        if not isinstance(axis_value, (list, tuple)) or len(axis_value) != 3:
                            errors.append(
                                f"'{field_base}.ligand_cutout_frame_axes.{axis_key}' must be a numeric 3-tuple."
                            )
                            continue
                        for idx_axis, entry in enumerate(axis_value):
                            try:
                                float(entry)
                            except Exception:
                                errors.append(
                                    f"'{field_base}.ligand_cutout_frame_axes.{axis_key}[{idx_axis}]' must be numeric."
                                )
            if (
                cutout_mode == "planes"
                and frame_mode == "preset"
                and "ligand_cutout_frame_mode" in style
                and "ligand_cutout_frame_axes" not in style
            ):
                errors.append(
                    f"'{field_base}.ligand_cutout_frame_axes' is required when "
                    f"'{field_base}.ligand_cutout_frame_mode' is 'preset'."
                )
            if "ligand_cutout_planes_enabled" in style:
                planes_enabled = style.get("ligand_cutout_planes_enabled")
                if not isinstance(planes_enabled, dict):
                    errors.append(f"'{field_base}.ligand_cutout_planes_enabled' must be an object.")
                else:
                    extra_plane_enabled = sorted(set(planes_enabled.keys()) - LIGAND_CUTOUT_PLANE_KEYS)
                    if extra_plane_enabled:
                        errors.append(
                            f"'{field_base}.ligand_cutout_planes_enabled' contains unsupported keys: "
                            f"{', '.join(extra_plane_enabled)}. "
                            f"Allowed keys: {', '.join(sorted(LIGAND_CUTOUT_PLANE_KEYS))}."
                        )
                    for plane_key in sorted(LIGAND_CUTOUT_PLANE_KEYS):
                        if plane_key in planes_enabled and not isinstance(planes_enabled.get(plane_key), bool):
                            errors.append(
                                f"'{field_base}.ligand_cutout_planes_enabled.{plane_key}' must be boolean."
                            )
            if "ligand_cutout_plane_scales" in style:
                plane_scales = style.get("ligand_cutout_plane_scales")
                if not isinstance(plane_scales, dict):
                    errors.append(f"'{field_base}.ligand_cutout_plane_scales' must be an object.")
                else:
                    extra_plane_scales = sorted(set(plane_scales.keys()) - LIGAND_CUTOUT_PLANE_KEYS)
                    if extra_plane_scales:
                        errors.append(
                            f"'{field_base}.ligand_cutout_plane_scales' contains unsupported keys: "
                            f"{', '.join(extra_plane_scales)}. "
                            f"Allowed keys: {', '.join(sorted(LIGAND_CUTOUT_PLANE_KEYS))}."
                        )
                    for plane_key in sorted(LIGAND_CUTOUT_PLANE_KEYS):
                        if plane_key not in plane_scales:
                            continue
                        try:
                            if float(plane_scales[plane_key]) <= 0:
                                errors.append(
                                    f"'{field_base}.ligand_cutout_plane_scales.{plane_key}' must be > 0."
                                )
                        except Exception:
                            errors.append(
                                f"'{field_base}.ligand_cutout_plane_scales.{plane_key}' must be numeric."
                            )
            if "ligand_cutout_seal_mode" in style:
                seal_mode = str(style.get("ligand_cutout_seal_mode", "")).strip().lower()
                if seal_mode not in {"boundary_fill", "trimmed_panel", "none"}:
                    errors.append(
                        f"'{field_base}.ligand_cutout_seal_mode' must be one of: boundary_fill, trimmed_panel, none."
                    )
            for key in ("ligand_cutout_seal_depth_scale", "ligand_cutout_seal_region_scale"):
                if key in style:
                    try:
                        if float(style[key]) <= 0:
                            errors.append(f"'{field_base}.{key}' must be > 0.")
                    except Exception:
                        errors.append(f"'{field_base}.{key}' must be numeric.")
            for key in (
                "ligand_cutout_width_scale",
                "ligand_cutout_depth_scale",
                "ligand_cutout_height_scale",
            ):
                if key in style:
                    try:
                        if float(style[key]) <= 0:
                            errors.append(f"'{field_base}.{key}' must be > 0.")
                    except Exception:
                        errors.append(f"'{field_base}.{key}' must be numeric.")
            if "ligand_cutout_one_sided_shift_scale" in style:
                try:
                    if float(style["ligand_cutout_one_sided_shift_scale"]) < 0:
                        errors.append(f"'{field_base}.ligand_cutout_one_sided_shift_scale' must be >= 0.")
                except Exception:
                    errors.append(f"'{field_base}.ligand_cutout_one_sided_shift_scale' must be numeric.")

    color_follow = style_doc.get("color_follow")
    if color_follow is not None:
        if not isinstance(color_follow, dict):
            errors.append("'color_follow' must be an object when provided.")
        else:
            extra_cf_keys = sorted(set(color_follow.keys()) - COLOR_FOLLOW_KEYS)
            if extra_cf_keys:
                errors.append(
                    "'color_follow' contains unsupported keys: "
                    f"{', '.join(extra_cf_keys)}. "
                    f"Allowed keys: {', '.join(sorted(COLOR_FOLLOW_KEYS))}."
                )
            enabled = color_follow.get("enabled")
            if enabled is not None and not isinstance(enabled, bool):
                errors.append("'color_follow.enabled' must be boolean when provided.")
            mode = color_follow.get("mode")
            if mode is not None and str(mode).strip().lower() not in {"auto", "chain", "class"}:
                errors.append("'color_follow.mode' must be one of: auto, chain, class.")
            for key in ("mix", "saturation_boost", "value_boost"):
                value = color_follow.get(key)
                if value is not None:
                    try:
                        float(value)
                    except Exception:
                        errors.append(f"'color_follow.{key}' must be numeric when provided.")

    if errors:
        bullet = "\n".join(f"- {item}" for item in errors)
        raise StyleValidationError(f"Style JSON validation failed:\n{bullet}")

    return style_doc


def load_and_validate_style_json(path: Path) -> dict[str, Any]:
    style_doc = _load_json(path)
    if STYLE_SCHEMA_PATH.exists() and _jsonschema is not None:
        schema_doc = _load_json(STYLE_SCHEMA_PATH)
        try:
            _jsonschema.Draft202012Validator(schema_doc).validate(style_doc)
        except Exception as exc:  # pragma: no cover - depends on jsonschema availability
            raise StyleValidationError(f"Schema validation against {STYLE_SCHEMA_PATH} failed: {exc}") from exc
    return validate_style_document(style_doc)


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
    color_hints = payload.get("color_hints")
    if isinstance(color_hints, dict):
        view_doc["color_hints"] = color_hints
    ss_hints = payload.get("ss_hints")
    if isinstance(ss_hints, dict):
        view_doc["ss_hints"] = ss_hints
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
            "If you want container execution, set --use-apptainer 1 and provide --apptainer-image."
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


def _resolve_apptainer_executable() -> str:
    candidates: list[str] = []
    resolved = shutil.which("apptainer")
    if resolved:
        candidates.append(resolved)
    candidates.extend(("/usr/bin/apptainer", "/usr/local/bin/apptainer"))

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)

    checked = ", ".join(candidates) if candidates else "<none>"
    raise RunnerError(
        "Apptainer executable was not found. "
        f"Checked: {checked}. "
        "Install apptainer or run without --use-apptainer."
    )


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


def _normalize_apptainer_blender_executable(blender_exe: str) -> str:
    raw = str(blender_exe or "").strip()
    if not raw:
        return DEFAULT_APPTAINER_BLENDER_EXE

    expanded = str(Path(raw).expanduser())
    known_container_paths = {
        "blender",
        DEFAULT_APPTAINER_BLENDER_EXE,
        "/bin/blender",
        "/opt/blender/blender",
    }
    if raw in known_container_paths or expanded in known_container_paths:
        return raw

    # Host-absolute Blender paths usually don't exist in the container.
    if expanded.startswith("/Applications/") or expanded.endswith(".app/Contents/MacOS/Blender"):
        return DEFAULT_APPTAINER_BLENDER_EXE
    if expanded.startswith("/"):
        return DEFAULT_APPTAINER_BLENDER_EXE
    return raw


def _apptainer_bind_args(paths: list[Path]) -> list[str]:
    args: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_file():
            candidate = path.parent
        else:
            candidate = path
        if not candidate.exists():
            continue
        resolved = _expand_path_no_resolve(candidate)
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        args.extend(["--bind", f"{key}:{key}:rw"])
    return args


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
    use_apptainer: bool,
    apptainer_image: str,
) -> list[str]:
    if use_apptainer and _is_macos():
        raise RunnerError("Apptainer execution is not supported on macOS. Use host Blender instead.")
    if use_apptainer:
        blender_exe = _normalize_apptainer_blender_executable(blender_exe)

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

    if use_apptainer:
        if not apptainer_image:
            raise RunnerError("--use-apptainer was enabled but --apptainer-image is empty.")
        apptainer_exe = _resolve_apptainer_executable()
        bind_paths = [
            output_png.parent,
            structure_path.parent,
            view_json_path.parent,
            custom_script.parent,
            Path.cwd(),
            Path.home(),
        ]
        if style_json_path is not None:
            bind_paths.append(style_json_path.parent)

        bind_args = _apptainer_bind_args(bind_paths)
        command = [
            apptainer_exe,
            "exec",
            "--nv",
            *bind_args,
            str(Path(apptainer_image).expanduser()),
        ] + command

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
    if not enabled:
        return style_doc
    styled = json.loads(json.dumps(style_doc))
    cfg = styled.get("color_follow")
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["enabled"] = True
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
    if str(preset or "").strip() == "preset_1_interface":
        return True
    raw_style_json = str(style_json_path or "").strip()
    if raw_style_json:
        name = Path(raw_style_json).name
        if name == "style_preset_1_opaque_pastel.json":
            return True
    if isinstance(style_doc, dict):
        output = style_doc.get("output")
        if isinstance(output, dict):
            filename = str(output.get("filename", "")).strip()
            if filename == "01_opaque_pastel_ses_interface.png":
                return True
    return False


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
