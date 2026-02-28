#!/usr/bin/env python3
"""Local/SLURM runner for PyMOL -> Blender bridge renders."""

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
}

PRESET_STYLE_FILENAMES: dict[str, str] = {
    "preset_1_interface": "style_preset_1_opaque_pastel.json",
    "preset_2_frosted_blue": "style_preset_2_frosted_blue.json",
    "preset_3_black_tubes": "style_preset_3_black_tubes.json",
    "preset_4_milky_green": "style_preset_4_milky_green.json",
    "preset_5_black_interface": "style_preset_5_black_interface.json",
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
    "ligand_cutout_selection",
    "ligand_cutout_mirror",
    "ligand_cutout_isolate",
    "del_solvent",
}
COLOR_FOLLOW_KEYS = {"enabled", "mode", "mix", "saturation_boost", "value_boost"}

try:
    import jsonschema as _jsonschema
except Exception:  # pragma: no cover - optional dependency
    _jsonschema = None


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
    errors: list[str] = []

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


def _build_local_runner_cli(
    *,
    python_executable: str,
    payload_json_path: str,
    preset: str | None,
    style_json_path: str | None,
    output_dir: str,
    output_name: str,
    blender_exe: str,
    custom_script: str,
    use_apptainer: bool,
    apptainer_image: str,
    rough: bool,
    raw_view: bool,
    color_follow: bool,
    save_blend: bool,
    blend_output: str,
    keep_temp: bool,
) -> list[str]:
    command = [
        python_executable,
        str(Path(__file__).resolve()),
        "local",
        "--payload-json",
        payload_json_path,
        "--blender-exe",
        blender_exe,
        "--custom-script",
        custom_script,
        "--rough",
        "1" if rough else "0",
        "--raw-view",
        "1" if raw_view else "0",
        "--color-follow",
        "1" if color_follow else "0",
        "--save-blend",
        "1" if save_blend else "0",
        "--keep-temp",
        "1" if keep_temp else "0",
    ]
    if blend_output:
        command.extend(["--blend-output", blend_output])
    if preset is not None:
        command.extend(["--preset", preset])
    elif style_json_path is not None:
        command.extend(["--style-json", style_json_path])
    else:
        raise RunnerError("Either preset or style_json_path must be provided for local runner invocation.")

    if output_dir:
        command.extend(["--output-dir", output_dir])
    if output_name:
        command.extend(["--output-name", output_name])
    if use_apptainer:
        command.extend(["--use-apptainer", "1"])
        if apptainer_image:
            command.extend(["--apptainer-image", apptainer_image])
    return command


def _session_dir(session_id: str) -> Path:
    return SESSION_ROOT / session_id


def _spool_requests_dir(session_id: str) -> Path:
    return _session_dir(session_id) / "requests"


def _spool_responses_dir(session_id: str) -> Path:
    return _session_dir(session_id) / "responses"


def _spool_session_dir(session_id: str) -> Path:
    return _session_dir(session_id) / "session"


def _session_ready_flag(session_id: str) -> Path:
    return _spool_session_dir(session_id) / "ready.flag"


def _session_heartbeat_file(session_id: str) -> Path:
    return _spool_session_dir(session_id) / "heartbeat.json"


def _legacy_session_file(session_id: str) -> Path:
    return _session_dir(session_id) / "session.json"


def _session_file(session_id: str) -> Path:
    return _spool_session_dir(session_id) / "session.json"


def _load_session_record(session_id: str) -> dict[str, Any]:
    path = _session_file(session_id)
    if path.exists():
        return _load_json(path)
    legacy = _legacy_session_file(session_id)
    if legacy.exists():
        return _load_json(legacy)
    return _load_json(path)


def _save_session_record(record: dict[str, Any]) -> None:
    session_id = str(record.get("session_id", "")).strip()
    if not session_id:
        raise RunnerError("Session record is missing 'session_id'.")
    _write_json_atomic(_session_file(session_id), record)


def _heartbeat_age_seconds(session_id: str) -> float | None:
    heartbeat_path = _session_heartbeat_file(session_id)
    try:
        payload = _load_json(heartbeat_path)
        ts = str(payload.get("updated_at_utc", "")).strip()
        if ts:
            normalized = ts.replace("Z", "+00:00")
            updated = dt.datetime.fromisoformat(normalized)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=dt.timezone.utc)
            return max(0.0, (dt.datetime.now(dt.timezone.utc) - updated).total_seconds())
    except Exception:
        pass
    try:
        return max(0.0, time.time() - heartbeat_path.stat().st_mtime)
    except FileNotFoundError:
        return None


def _write_heartbeat(
    session_id: str,
    *,
    request_id: str = "",
    status: str = "idle",
    queue_depth: int = 0,
) -> None:
    heartbeat_path = _session_heartbeat_file(session_id)
    payload = {
        "session_id": session_id,
        "updated_at_utc": _utc_now_iso(),
        "request_id": str(request_id or ""),
        "status": str(status or "idle"),
        "queue_depth": int(max(0, queue_depth)),
        "worker_pid": os.getpid(),
        "worker_host": socket.gethostname(),
    }
    _write_json_atomic(heartbeat_path, payload)


def _session_pid_alive(pid_value: Any) -> bool:
    try:
        pid = int(pid_value)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _host_aliases(value: str) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    aliases = {text}
    if "." in text:
        aliases.add(text.split(".", 1)[0])
    return aliases


def _same_host(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return bool(_host_aliases(left) & _host_aliases(right))


def _qlogin_worker_running(session_id: str, session: dict[str, Any], stale_seconds: int = 120) -> tuple[bool, float | None]:
    status = str(session.get("status", "")).strip().lower()
    if status in {"closed", "close_requested"}:
        return False, None

    ready_flag = _session_ready_flag(session_id)
    heartbeat_age = _heartbeat_age_seconds(session_id)
    stale_limit = max(5, stale_seconds)

    if ready_flag.exists() and heartbeat_age is not None and heartbeat_age <= stale_limit:
        return True, heartbeat_age
    if heartbeat_age is not None and heartbeat_age > stale_limit:
        return False, heartbeat_age

    worker_pid = session.get("worker_pid")
    worker_host = str(session.get("worker_host", "")).strip()
    if worker_host and _same_host(worker_host, socket.gethostname()):
        return _session_pid_alive(worker_pid), heartbeat_age
    if not worker_host and worker_pid is not None:
        return _session_pid_alive(worker_pid), heartbeat_age

    return False, heartbeat_age


def _discover_slurm_job_ids(session_id: str) -> list[str]:
    session = _load_session_record(session_id)
    found: set[str] = set()

    for key in ("slurm_job_id", "job_id"):
        raw = str(session.get(key, "")).strip()
        if raw.isdigit():
            found.add(raw)

    stderr_path = _session_dir(session_id) / "worker.stderr.log"
    try:
        text = stderr_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""

    for match in re.findall(r"\bjob\s+(\d+)\b", text):
        found.add(match)
    for match in re.findall(r"\bSTEP\s+(\d+)\.\d+\b", text):
        found.add(match)

    return sorted(found)


def _cancel_slurm_jobs(job_ids: list[str]) -> tuple[list[str], list[str]]:
    cancelled: list[str] = []
    failed: list[str] = []
    for job_id in job_ids:
        if not str(job_id).isdigit():
            continue
        result = _run_subprocess(["scancel", str(job_id)])
        if result.returncode == 0:
            cancelled.append(str(job_id))
            continue
        stderr_text = (result.stderr or "").lower()
        if "invalid job id" in stderr_text or "already completing" in stderr_text:
            cancelled.append(str(job_id))
            continue
        failed.append(str(job_id))
    return cancelled, failed


def _log_tail(path: Path, max_lines: int = 40) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "\n".join(lines[-max_lines:])


def run_local(args: argparse.Namespace) -> dict[str, Any]:
    rough = bool(getattr(args, "rough", False))
    raw_view = bool(getattr(args, "raw_view", False))
    color_follow = bool(getattr(args, "color_follow", False))
    save_blend = bool(getattr(args, "save_blend", False))
    keep_temp = bool(getattr(args, "keep_temp", False))
    blend_output = str(getattr(args, "blend_output", "") or "").strip()
    payload_path = Path(args.payload_json).expanduser().resolve()
    payload = validate_payload_document(_load_json(payload_path))

    style_doc: dict[str, Any] | None = None
    if args.style_json:
        style_doc = load_and_validate_style_json(Path(args.style_json).expanduser().resolve())
    elif args.preset:
        style_doc = load_and_validate_style_json(_default_preset_style(args.preset))
    else:
        raise RunnerError("Local mode requires either --preset or --style-json.")

    if rough:
        style_doc = _apply_rough_style_overrides(style_doc)
    style_doc = _apply_color_follow_override(style_doc, color_follow)
    style_doc = _apply_raw_view_override(style_doc, enabled=raw_view)
    style_doc = _apply_save_blend_override(style_doc, enabled=save_blend, blend_output=blend_output)

    run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else _new_run_id()
    temp_root: Path | None = None
    try:
        temp_root, _bundle_dir, structure_path, pdb_fallback_path, view_path = _materialize_payload_bundle(payload, run_id)
        run_manifest_path = temp_root / "run_manifest.json"
        _write_run_manifest(
            run_manifest_path,
            payload,
            chosen_structure_path=structure_path,
            style_doc=style_doc,
        )

        staged_style = temp_root / "style.json"
        staged_style.write_text(json.dumps(style_doc, indent=2), encoding="utf-8")
        style_json_path = staged_style

        output_png = _resolve_output_png(
            output_dir=args.output_dir,
            output_name=args.output_name,
            preset=args.preset,
            style_doc=style_doc,
        )
        preset_1_gate_target = _is_preset_1_gate_target(
            preset=args.preset,
            style_json_path=args.style_json,
            style_doc=style_doc,
        )
        if preset_1_gate_target and structure_path.suffix.lower() != ".cif":
            raise RunnerError(
                "Preset gate failure for preset_1_interface.\n"
                "- structure source must be CIF-first; payload did not contain a usable CIF export\n"
                f"Manifest: {run_manifest_path}\n"
                "Hint: rerun with --keep-temp 1 to inspect staged payload/structure files."
            )

        if not args.use_apptainer and not _blender_executable_exists(args.blender_exe):
            detected_blender = _detect_host_blender_executable()
            if detected_blender:
                args.blender_exe = detected_blender

        subprocess_env: dict[str, str] | None = os.environ.copy()
        subprocess_env.setdefault("PRESS_CYCLES_DEVICE", "GPU")
        subprocess_env.setdefault("PRESS_CYCLES_COMPUTE_TYPE", "AUTO")
        subprocess_env.setdefault("PRESS_PRINT_DEVICE_INFO", "1")
        # Prefer GPU but do not hard-fail unless explicitly requested by caller env.
        subprocess_env.setdefault("PRESS_REQUIRE_GPU", "0")
        if rough:
            subprocess_env["PRESS_RENDER_SCALE"] = "0.30"
            subprocess_env["PRESS_SAMPLES"] = "24"

        def _run_for_structure(struct_path: Path, target_output: Path) -> tuple[list[str], subprocess.CompletedProcess[str], float]:
            _write_run_manifest(
                run_manifest_path,
                payload,
                chosen_structure_path=struct_path,
                style_doc=style_doc,
            )
            cmd = build_local_blender_command(
                blender_exe=args.blender_exe,
                preset=args.preset,
                structure_path=struct_path,
                view_json_path=view_path,
                style_json_path=style_json_path,
                output_png=target_output,
                custom_script=_expand_path_no_resolve(args.custom_script),
                run_manifest_path=run_manifest_path,
                use_apptainer=args.use_apptainer,
                apptainer_image=args.apptainer_image,
            )
            started = time.time()
            proc = _run_subprocess(cmd, env=subprocess_env)
            elapsed = round(time.time() - started, 3)
            return cmd, proc, elapsed

        used_structure = structure_path
        command, result, runtime_seconds = _run_for_structure(used_structure, output_png)
        if (
            result.returncode != 0
            and used_structure.suffix.lower() == ".cif"
            and pdb_fallback_path is not None
            and not preset_1_gate_target
        ):
            retry_command, retry_result, retry_runtime = _run_for_structure(pdb_fallback_path, output_png)
            if retry_result.returncode == 0:
                used_structure = pdb_fallback_path
                command, result, runtime_seconds = retry_command, retry_result, retry_runtime

        if result.returncode != 0:
            if (
                preset_1_gate_target
                and used_structure.suffix.lower() == ".cif"
                and pdb_fallback_path is not None
            ):
                raise RunnerError(
                    "Local Blender render failed while using CIF for preset_1_interface.\n"
                    "PDB fallback is disabled for this preset to enforce CIF-first topology.\n"
                    f"Command: {' '.join(shlex.quote(item) for item in command)}\n"
                    f"{_debug_process_text(result)}"
                )
            raise RunnerError(
                "Local Blender render failed.\n"
                f"Command: {' '.join(shlex.quote(item) for item in command)}\n"
                f"{_debug_process_text(result)}"
            )

        final_output = output_png

        if not final_output.exists():
            if (
                used_structure.suffix.lower() == ".cif"
                and pdb_fallback_path is not None
                and not preset_1_gate_target
            ):
                retry_command, retry_result, retry_runtime = _run_for_structure(pdb_fallback_path, output_png)
                if retry_result.returncode == 0 and output_png.exists():
                    used_structure = pdb_fallback_path
                    command, result, runtime_seconds = retry_command, retry_result, retry_runtime

            if args.use_apptainer and not final_output.exists():
                fallback_out = TMP_BRIDGE_ROOT / "_apptainer_output" / run_id / final_output.name
                fallback_out.parent.mkdir(parents=True, exist_ok=True)
                fallback_command, fallback_result, fallback_runtime_seconds = _run_for_structure(
                    used_structure, fallback_out
                )
                if fallback_result.returncode == 0 and fallback_out.exists():
                    try:
                        final_output.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(fallback_out, final_output)
                        final_output = final_output
                    except Exception:
                        final_output = fallback_out
                    command = fallback_command
                    result = fallback_result
                    runtime_seconds = fallback_runtime_seconds

            if not final_output.exists():
                raise RunnerError(
                    "Blender process completed but output image was not found.\n"
                    f"Expected: {final_output}\n"
                    f"{_debug_process_text(result)}"
                )

        gpu_info = _extract_gpu_info(result.stdout or "", result.stderr or "")
        output_blend_path = _resolve_output_blend(final_output, style_doc)
        output_blend = ""
        if output_blend_path is not None and output_blend_path.exists():
            output_blend = str(output_blend_path)
        _write_run_manifest(
            run_manifest_path,
            payload,
            chosen_structure_path=used_structure,
            style_doc=style_doc,
        )
        manifest_doc = _load_json(run_manifest_path)
        if preset_1_gate_target:
            _enforce_preset_1_backend_gates(
                manifest=manifest_doc,
                manifest_path=run_manifest_path,
            )
        return {
            "status": "completed",
            "mode": "local",
            "run_id": run_id,
            "output_png": str(final_output),
            "output_dir": str(final_output.parent),
            "command": command,
            "runtime_seconds": runtime_seconds,
            "gpu_info": gpu_info,
            "output_blend": output_blend,
            "staged_structure_path": str(used_structure),
            "temp_dir": str(temp_root),
            "temp_kept": bool(keep_temp),
            "manifest_path": str(run_manifest_path),
            "manifest": manifest_doc,
            "blender_stdout_tail": _tail_text(result.stdout or "", max_chars=10000),
            "blender_stderr_tail": _tail_text(result.stderr or "", max_chars=10000),
        }
    finally:
        if temp_root is not None and temp_root.exists() and not keep_temp:
            shutil.rmtree(temp_root, ignore_errors=True)


def run_interactive(args: argparse.Namespace) -> dict[str, Any]:
    rough = bool(getattr(args, "rough", False))
    raw_view = bool(getattr(args, "raw_view", False))
    color_follow = bool(getattr(args, "color_follow", False))
    save_blend = bool(getattr(args, "save_blend", False))
    blend_output = str(getattr(args, "blend_output", "") or "").strip()
    payload_path = Path(args.payload_json).expanduser().resolve()
    payload = validate_payload_document(_load_json(payload_path))

    style_doc: dict[str, Any] | None = None
    if args.style_json:
        style_doc = load_and_validate_style_json(Path(args.style_json).expanduser().resolve())
        if rough:
            style_doc = _apply_rough_style_overrides(style_doc)

    run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else _new_run_id()

    interactive_output_dir = args.output_dir if args.output_dir else str(TMP_BRIDGE_ROOT / "renders" / _now_stamp())
    output_png = _resolve_output_png(
        output_dir=interactive_output_dir,
        output_name=args.output_name,
        preset=args.preset,
        style_doc=style_doc,
    )

    payload_b64 = _encode_json_b64(payload)
    style_b64 = _encode_json_b64(style_doc) if style_doc is not None else None

    local_runner_command = _build_local_runner_cli(
        python_executable=_default_remote_python_executable(),
        payload_json_path="$RUN_DIR/payload.json",
        preset=args.preset,
        style_json_path="$RUN_DIR/style.json" if style_doc is not None else None,
        output_dir=str(output_png.parent),
        output_name=output_png.name,
        blender_exe=args.blender_exe,
        custom_script=str(_expand_path_no_resolve(args.custom_script)),
        use_apptainer=args.use_apptainer,
        apptainer_image=args.apptainer_image,
        rough=rough,
        raw_view=raw_view,
        color_follow=color_follow,
        save_blend=save_blend,
        blend_output=blend_output,
        keep_temp=bool(getattr(args, "keep_temp", False)),
    )
    local_runner_command_q = " ".join(
        f'"{item}"' if item.startswith("$RUN_DIR/") else shlex.quote(item)
        for item in local_runner_command
    )
    tmp_root_q = shlex.quote(str(TMP_BRIDGE_ROOT))

    script_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"RUN_ID={shlex.quote(run_id)}",
        f"TMP_ROOT={tmp_root_q}",
        'mkdir -p "$TMP_ROOT"',
        'RUN_DIR="$TMP_ROOT/${SLURM_JOB_ID:-$RUN_ID}"',
        'mkdir -p "$RUN_DIR"',
        "cat <<'PAYLOAD_B64' > \"$RUN_DIR/payload.json.b64\"",
        payload_b64,
        "PAYLOAD_B64",
        'base64 --decode "$RUN_DIR/payload.json.b64" > "$RUN_DIR/payload.json"',
    ]
    if style_b64 is not None:
        script_lines.extend(
            [
                "cat <<'STYLE_B64' > \"$RUN_DIR/style.json.b64\"",
                style_b64,
                "STYLE_B64",
                'base64 --decode "$RUN_DIR/style.json.b64" > "$RUN_DIR/style.json"',
            ]
        )
    script_lines.append(local_runner_command_q)
    script_text = "\n".join(script_lines)

    launch_dir = Path.cwd() / ".bridge_tmp" / run_id
    launch_dir.mkdir(parents=True, exist_ok=True)
    script_path = launch_dir / "interactive_render.sh"
    script_path.write_text(script_text + "\n", encoding="utf-8")
    script_path.chmod(0o700)

    launcher = str(getattr(args, "interactive_launcher", "") or DEFAULT_INTERACTIVE_LAUNCHER).strip()
    launcher_command = shlex.split(launcher)
    if not launcher_command:
        raise RunnerError("Interactive launcher command is empty.")
    command = launcher_command + ["bash", str(script_path)]

    result = _run_subprocess(command)
    if result.returncode != 0:
        raise RunnerError(
            "Interactive render failed.\n"
            f"Command: {' '.join(shlex.quote(item) for item in command)}\n"
            f"stdout:\n{(result.stdout or '').strip()}\n"
            f"stderr:\n{(result.stderr or '').strip()}"
        )

    parsed: dict[str, Any] | None = None
    parse_error: Exception | None = None
    for candidate in ((result.stdout or ""), (result.stderr or ""), (result.stdout or "") + "\n" + (result.stderr or "")):
        try:
            parsed = _parse_last_json_object(candidate)
            break
        except Exception as exc:
            parse_error = exc

    if parsed is None:
        if output_png.exists():
            parsed = {
                "status": "completed",
                "mode": "local",
                "run_id": run_id,
                "output_png": str(output_png),
                "output_dir": str(output_png.parent),
            }
        else:
            raise RunnerError(
                "Interactive launcher completed but no JSON result was detected from local runner.\n"
                f"stdout:\n{(result.stdout or '').strip()}\n"
                f"stderr:\n{(result.stderr or '').strip()}"
            ) from parse_error

    output_png_value = str(parsed.get("output_png", output_png))
    output_dir_value = str(parsed.get("output_dir", output_png.parent))
    return {
        "status": str(parsed.get("status", "completed")),
        "mode": "interactive",
        "run_id": str(parsed.get("run_id", run_id)),
        "output_png": output_png_value,
        "output_blend": str(parsed.get("output_blend", "")),
        "output_dir": output_dir_value,
        "command": command,
        "script_path": str(script_path),
        "launcher": launcher_command,
        "local_result": parsed,
    }


def _request_spool_dir(session_id: str, request_id: str) -> Path:
    return _spool_requests_dir(session_id) / request_id


def _response_spool_dir(session_id: str, request_id: str) -> Path:
    return _spool_responses_dir(session_id) / request_id


def _interactive_result_path(session_id: str, request_id: str) -> Path:
    return _response_spool_dir(session_id, request_id) / "result.json"


def _submitted_by_tag() -> str:
    user = os.environ.get("USER", "").strip() or os.environ.get("LOGNAME", "").strip() or "unknown"
    return f"{user}@{socket.gethostname()}:{os.getpid()}"


def _list_request_dirs_fifo(session_id: str) -> list[Path]:
    root = _spool_requests_dir(session_id)
    if not root.exists():
        return []
    request_dirs = [
        path for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and (path / "request.json").is_file()
    ]
    return sorted(request_dirs, key=lambda path: path.name)


def _pending_queue_depth(session_id: str) -> int:
    return len(_list_request_dirs_fifo(session_id))


def _worker_currently_running(session_id: str) -> bool:
    heartbeat = _session_heartbeat_file(session_id)
    try:
        payload = _load_json(heartbeat)
    except Exception:
        return False
    status = str(payload.get("status", "")).strip().lower()
    return status == "running"


def _queue_position_for_new_request(session_id: str) -> int:
    # Request directories represent queued + currently running work in FIFO order.
    # A new request will be appended behind all existing request dirs.
    existing = len(_list_request_dirs_fifo(session_id))
    return max(1, existing + 1)


def _build_interactive_request(
    *,
    session_id: str,
    args: argparse.Namespace,
    session: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    payload_path = Path(args.payload_json).expanduser().resolve()
    payload = validate_payload_document(_load_json(payload_path))
    style_doc: dict[str, Any] | None = None
    if args.style_json:
        style_doc = load_and_validate_style_json(Path(args.style_json).expanduser().resolve())

    session_output_dir = str(session.get("default_output_dir", "") or "").strip()
    if args.output_dir:
        output_dir = str(Path(args.output_dir).expanduser())
    elif session_output_dir:
        output_dir = session_output_dir
    else:
        output_dir = str(TMP_BRIDGE_ROOT / "renders" / _now_stamp())

    request_id = f"{time.time_ns():020d}_{_new_run_id()}"
    queue_position = _queue_position_for_new_request(session_id)
    submitted_by = _submitted_by_tag()
    request_doc: dict[str, Any] = {
        "request_id": request_id,
        "created_at_utc": _utc_now_iso(),
        "submitted_by": submitted_by,
        "queue_position": int(queue_position),
        "status": "submitted",
        "payload": payload,
        "preset": args.preset,
        "style_doc": style_doc,
        "output_dir": output_dir,
        "output_name": args.output_name,
        "blender_exe": args.blender_exe,
        "custom_script": str(_expand_path_no_resolve(args.custom_script)),
        "use_apptainer": bool(args.use_apptainer),
        "apptainer_image": args.apptainer_image,
        "rough": bool(getattr(args, "rough", False)),
        "raw_view": bool(getattr(args, "raw_view", False)),
        "color_follow": bool(getattr(args, "color_follow", False)),
        "save_blend": bool(getattr(args, "save_blend", False)),
        "blend_output": str(getattr(args, "blend_output", "") or ""),
        "keep_temp": bool(getattr(args, "keep_temp", False)),
    }
    return request_id, request_doc


def _interactive_requested_output_path(request_doc: dict[str, Any], result_doc: dict[str, Any]) -> Path | None:
    output_dir_raw = str(request_doc.get("output_dir", "") or "").strip()
    if not output_dir_raw:
        return None
    output_dir = _expand_path_no_resolve(output_dir_raw)
    output_name_raw = str(request_doc.get("output_name", "") or "").strip()
    if output_name_raw:
        filename = _ensure_suffix_png(output_name_raw)
    else:
        result_name = Path(str(result_doc.get("output_png", "") or "")).name
        filename = _ensure_suffix_png(result_name) if result_name else "interactive_render.png"
    return output_dir / filename


def _finalize_interactive_result_output(
    *,
    session_id: str,
    request_id: str,
    request_doc: dict[str, Any],
    result_doc: dict[str, Any],
) -> dict[str, Any]:
    finalized = dict(result_doc)
    response_png = _response_spool_dir(session_id, request_id) / "output.png"
    requested_output = _interactive_requested_output_path(request_doc, finalized)
    if requested_output is None:
        return finalized

    if response_png.is_file():
        requested_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(response_png, requested_output)
        finalized["output_png"] = str(requested_output)
        finalized["output_dir"] = str(requested_output.parent)
        return finalized

    existing_output = Path(str(finalized.get("output_png", "") or "")).expanduser()
    if existing_output.is_file():
        finalized["output_png"] = str(existing_output)
        finalized["output_dir"] = str(existing_output.parent)
        return finalized

    # Preserve requested output location even if remote output file is unavailable.
    finalized["output_png"] = str(requested_output)
    finalized["output_dir"] = str(requested_output.parent)
    return finalized


def _collect_existing_sessions() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not SESSION_ROOT.exists():
        return records
    for session_dir in SESSION_ROOT.iterdir():
        if not session_dir.is_dir():
            continue
        session_id = session_dir.name
        try:
            record = _load_session_record(session_id)
        except Exception:
            continue
        if str(record.get("session_id", "")).strip() != session_id:
            continue
        records.append(record)
    return records


def _find_attachable_qlogin_session() -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for record in _collect_existing_sessions():
        if str(record.get("backend", "")).strip().lower() != "qlogin":
            continue
        session_id = str(record.get("session_id", "")).strip()
        if not session_id:
            continue
        running, heartbeat_age = _qlogin_worker_running(session_id, record, stale_seconds=120)
        if not running:
            continue
        if isinstance(heartbeat_age, float):
            rank = heartbeat_age
        else:
            rank = float("inf")
        candidates.append((rank, record))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _start_qlogin_worker(
    *,
    session_id: str,
    session_record: dict[str, Any],
    launcher: str,
    poll_interval: int,
) -> tuple[subprocess.Popen[str], list[str], Path, Path]:
    session_dir = _session_dir(session_id)
    launcher_command = shlex.split(launcher)
    if not launcher_command:
        raise RunnerError("Interactive launcher command is empty.")

    worker_command = launcher_command + [
        _default_remote_python_executable(),
        "-u",
        str(Path(__file__).resolve()),
        "_interactive-worker",
        "--session-dir",
        str(session_dir),
        "--poll-interval",
        str(max(1, int(poll_interval))),
    ]
    stdout_log = session_dir / "worker.stdout.log"
    stderr_log = session_dir / "worker.stderr.log"
    popen_env = os.environ.copy()
    popen_env.setdefault("PYTHONUNBUFFERED", "1")
    _session_ready_flag(session_id).unlink(missing_ok=True)  # type: ignore[arg-type]
    _session_heartbeat_file(session_id).unlink(missing_ok=True)  # type: ignore[arg-type]

    with stdout_log.open("a", encoding="utf-8") as out_handle, stderr_log.open("a", encoding="utf-8") as err_handle:
        process = subprocess.Popen(
            worker_command,
            stdout=out_handle,
            stderr=err_handle,
            text=True,
            start_new_session=True,
            env=popen_env,
        )

    session_record["launcher"] = launcher
    session_record["launcher_pid"] = process.pid
    session_record["worker_pid"] = process.pid
    session_record["status"] = "starting"
    session_record["worker_restart_count"] = int(session_record.get("worker_restart_count", 0)) + 1
    session_record["worker_last_start_at_utc"] = _utc_now_iso()
    _save_session_record(session_record)
    return process, worker_command, stdout_log, stderr_log


def _wait_for_qlogin_worker_ready(
    *,
    session_id: str,
    process: subprocess.Popen[str] | None,
    worker_command: list[str] | None,
    stdout_log: Path,
    stderr_log: Path,
    startup_timeout: int,
    poll_interval: int,
) -> dict[str, Any]:
    deadline = time.time() + max(1, int(startup_timeout))
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            tail_out = _log_tail(stdout_log)
            tail_err = _log_tail(stderr_log)
            command_text = (
                " ".join(shlex.quote(part) for part in worker_command)
                if isinstance(worker_command, list) and worker_command
                else "<unknown>"
            )
            raise RunnerError(
                "Interactive worker exited before becoming ready.\n"
                f"Launcher command: {command_text}\n"
                f"stdout tail:\n{tail_out}\n"
                f"stderr tail:\n{tail_err}"
            )

        record = _load_session_record(session_id)
        running, _heartbeat_age = _qlogin_worker_running(session_id, record, stale_seconds=max(30, poll_interval * 4))
        if running:
            record["status"] = "ready"
            record["ready_at_utc"] = _utc_now_iso()
            _save_session_record(record)
            return record
        time.sleep(max(1, int(poll_interval)))

    raise RunnerError(
        f"Timed out waiting for interactive session '{session_id}' to become ready "
        f"after {startup_timeout}s. Check logs in {_session_dir(session_id)}."
    )


def _restart_qlogin_session_worker(
    *,
    session_id: str,
    session_record: dict[str, Any],
    launcher: str,
    startup_timeout: int,
    poll_interval: int,
) -> dict[str, Any]:
    process, worker_command, stdout_log, stderr_log = _start_qlogin_worker(
        session_id=session_id,
        session_record=session_record,
        launcher=launcher,
        poll_interval=poll_interval,
    )
    return _wait_for_qlogin_worker_ready(
        session_id=session_id,
        process=process,
        worker_command=worker_command,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        startup_timeout=startup_timeout,
        poll_interval=poll_interval,
    )


def _execute_session_request(*, session_id: str, request_doc: dict[str, Any]) -> dict[str, Any]:
    request_id = str(request_doc.get("request_id", _new_run_id()))
    stage_dir = _session_dir(session_id) / "staged" / request_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    keep_temp = bool(request_doc.get("keep_temp", False))

    try:
        payload = request_doc.get("payload")
        if not isinstance(payload, dict):
            raise RunnerError("Interactive request is missing payload object.")
        validate_payload_document(payload)
        payload_path = stage_dir / "payload.json"
        _write_json_atomic(payload_path, payload)

        style_json_path = ""
        style_doc = request_doc.get("style_doc")
        if style_doc is not None:
            if not isinstance(style_doc, dict):
                raise RunnerError("Interactive request 'style_doc' must be an object when provided.")
            validate_style_document(style_doc)
            style_path = stage_dir / "style.json"
            _write_json_atomic(style_path, style_doc)
            style_json_path = str(style_path)

        local_args = argparse.Namespace(
            payload_json=str(payload_path),
            preset=request_doc.get("preset"),
            style_json=style_json_path,
            rough=bool(request_doc.get("rough", False)),
            raw_view=bool(request_doc.get("raw_view", False)),
            output_dir=str(request_doc.get("output_dir", "") or ""),
            output_name=str(request_doc.get("output_name", "") or ""),
            blender_exe=str(request_doc.get("blender_exe", DEFAULT_BLENDER_EXE) or DEFAULT_BLENDER_EXE),
            custom_script=str(
                request_doc.get("custom_script", str(_default_custom_script())) or str(_default_custom_script())
            ),
            use_apptainer=bool(request_doc.get("use_apptainer", False)),
            apptainer_image=str(request_doc.get("apptainer_image", "") or ""),
            color_follow=bool(request_doc.get("color_follow", False)),
            save_blend=bool(request_doc.get("save_blend", False)),
            blend_output=str(request_doc.get("blend_output", "") or ""),
            keep_temp=keep_temp,
        )
        if not local_args.preset and not local_args.style_json:
            raise RunnerError("Interactive request must include either a preset or style_doc.")

        result = run_local(local_args)
        result["mode"] = "interactive"
        result["session_id"] = session_id
        result["request_id"] = request_id
        return result
    finally:
        if stage_dir.exists() and not keep_temp:
            shutil.rmtree(stage_dir, ignore_errors=True)


def _recover_stale_request_locks(session_id: str, stale_seconds: int = 300) -> int:
    recovered = 0
    now = time.time()
    for request_dir in _list_request_dirs_fifo(session_id):
        lock_path = request_dir / "processing.lock"
        if not lock_path.exists():
            continue
        try:
            age = max(0.0, now - lock_path.stat().st_mtime)
        except Exception:
            age = float(stale_seconds + 1)
        if age <= max(15, stale_seconds):
            continue
        try:
            lock_path.unlink()
            recovered += 1
        except Exception:
            continue
    return recovered


def run_interactive_worker(args: argparse.Namespace) -> dict[str, Any]:
    session_dir = Path(args.session_dir).expanduser().resolve()
    session_file = session_dir / "session" / "session.json"
    request_root = session_dir / "requests"
    response_root = session_dir / "responses"
    control_dir = session_dir / "control"
    stop_file = control_dir / "stop"
    worker_state_file = control_dir / "worker_state.json"

    for path in (request_root, response_root, control_dir, session_dir / "session", session_dir / "staged"):
        path.mkdir(parents=True, exist_ok=True)

    session = _load_json(session_file)
    session_id = str(session.get("session_id", "")).strip()
    if not session_id:
        raise RunnerError("Worker session metadata is missing session_id.")

    session["status"] = "ready"
    session["worker_pid"] = os.getpid()
    session["worker_host"] = socket.gethostname()
    session["worker_started_at_utc"] = _utc_now_iso()
    session["backend"] = str(session.get("backend", "qlogin") or "qlogin")
    _save_session_record(session)
    _session_ready_flag(session_id).write_text(_utc_now_iso() + "\n", encoding="utf-8")

    poll_interval = max(1, int(args.poll_interval))

    while True:
        _write_heartbeat(session_id, status="idle", queue_depth=_pending_queue_depth(session_id))
        if stop_file.exists():
            print(f"[{_utc_now_iso()}] interactive worker stop requested: session={session_id}", flush=True)
            break

        _recover_stale_request_locks(session_id, stale_seconds=max(120, poll_interval * 60))
        request_dirs = _list_request_dirs_fifo(session_id)
        if not request_dirs:
            time.sleep(poll_interval)
            continue

        for request_dir in request_dirs:
            if stop_file.exists():
                break

            request_id = request_dir.name
            request_path = request_dir / "request.json"
            lock_path = request_dir / "processing.lock"
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                os.close(fd)
            except FileExistsError:
                continue
            except Exception:
                continue

            started_at = _utc_now_iso()
            print(f"[{started_at}] request start: session={session_id} request={request_id}", flush=True)
            _write_heartbeat(
                session_id,
                request_id=request_id,
                status="running",
                queue_depth=max(0, _pending_queue_depth(session_id) - 1),
            )

            request_doc: dict[str, Any]
            try:
                request_doc = _load_json(request_path)
            except Exception:
                request_doc = {"request_id": request_id}
            request_doc["started_at"] = started_at
            request_doc["status"] = "running"
            try:
                _write_json_atomic(request_path, request_doc)
            except Exception:
                pass

            try:
                result_doc = _execute_session_request(session_id=session_id, request_doc=request_doc)
            except Exception as exc:
                result_doc = {
                    "status": "failed",
                    "mode": "interactive",
                    "session_id": session_id,
                    "request_id": request_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }

            finished_at = _utc_now_iso()
            result_doc["session_id"] = session_id
            result_doc["submitted_by"] = str(request_doc.get("submitted_by", ""))
            result_doc["queue_position"] = int(request_doc.get("queue_position", 1))
            result_doc["started_at"] = str(request_doc.get("started_at", started_at))
            result_doc["finished_at"] = finished_at
            result_doc["status"] = str(result_doc.get("status", "completed"))

            status = str(result_doc.get("status", "unknown"))
            runtime = result_doc.get("runtime_seconds")
            output_png = result_doc.get("output_png", "")
            gpu_info = result_doc.get("gpu_info", [])
            print(
                f"[{finished_at}] request done: session={session_id} request={request_id} "
                f"status={status} runtime={runtime} output={output_png}",
                flush=True,
            )
            if isinstance(gpu_info, list) and gpu_info:
                for line in gpu_info:
                    print(f"[{_utc_now_iso()}] {line}", flush=True)

            response_dir = _response_spool_dir(session_id, request_id)
            response_dir.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(_interactive_result_path(session_id, request_id), result_doc)

            output_png_path = Path(str(output_png or "")).expanduser()
            if output_png_path.is_file():
                try:
                    shutil.copy2(output_png_path, response_dir / "output.png")
                except Exception:
                    pass

            _write_json_atomic(
                worker_state_file,
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "updated_at_utc": finished_at,
                    "status": result_doc.get("status", "unknown"),
                    "output_png": result_doc.get("output_png", ""),
                },
            )

            try:
                lock_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            except Exception:
                pass
            shutil.rmtree(request_dir, ignore_errors=True)

    session = _load_json(session_file)
    session["status"] = "closed"
    session["closed_at_utc"] = _utc_now_iso()
    _save_session_record(session)
    try:
        _session_ready_flag(session_id).unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass
    return {
        "status": "closed",
        "mode": "interactive_worker",
        "session_id": session_id,
    }


def run_interactive_open(args: argparse.Namespace) -> dict[str, Any]:
    backend = str(args.backend).strip().lower()
    if backend not in {"qlogin", "jobid"}:
        raise RunnerError("interactive-open backend must be 'qlogin' or 'jobid'.")

    attach_only = bool(getattr(args, "attach_only", False))
    attach = bool(getattr(args, "attach", False) or attach_only)
    requested_session_id = str(getattr(args, "session_id", "") or "").strip()
    startup_timeout = max(1, int(getattr(args, "startup_timeout", 900)))
    poll_interval = max(1, int(getattr(args, "poll_interval", 2)))

    if attach and backend != "qlogin":
        raise RunnerError("interactive-open attach/attach-only is supported only for backend='qlogin'.")

    session_record: dict[str, Any] | None = None
    session_id = requested_session_id
    had_existing_session = False

    if session_id:
        session_path = _session_file(session_id)
        legacy_path = _legacy_session_file(session_id)
        if session_path.exists() or legacy_path.exists():
            session_record = _load_session_record(session_id)
            had_existing_session = True
    elif attach:
        session_record = _find_attachable_qlogin_session()
        if session_record is not None:
            session_id = str(session_record.get("session_id", "")).strip()
            had_existing_session = True

    if attach_only and (not session_record or not session_id):
        raise RunnerError("attach_only=1 requested but no healthy interactive session is available.")

    if not session_id:
        session_id = _new_run_id()

    default_output_dir = (
        str(Path(args.output_dir).expanduser())
        if args.output_dir
        else str((Path.cwd() / "renders" / f"interactive_{session_id}").absolute())
    )
    session_dir = _session_dir(session_id)
    for path in (
        _spool_requests_dir(session_id),
        _spool_responses_dir(session_id),
        _spool_session_dir(session_id),
        session_dir / "control",
        session_dir / "staged",
    ):
        path.mkdir(parents=True, exist_ok=True)

    if session_record is None:
        session_record = {
            "session_id": session_id,
            "backend": backend,
            "status": "starting" if backend == "qlogin" else "ready",
            "created_at_utc": _utc_now_iso(),
            "default_output_dir": default_output_dir,
        }
    else:
        session_record["backend"] = backend
        if args.output_dir:
            session_record["default_output_dir"] = default_output_dir
        else:
            session_record["default_output_dir"] = str(session_record.get("default_output_dir", default_output_dir) or default_output_dir)

    if backend == "jobid":
        job_id = str(getattr(args, "job_id", "") or "").strip()
        if not job_id:
            raise RunnerError("interactive-open --backend jobid requires --job-id.")
        job_launcher = str(getattr(args, "job_launcher", "") or "srun --jobid {job_id}").strip()
        session_record["job_id"] = job_id
        session_record["job_launcher"] = job_launcher
        _save_session_record(session_record)
        return {
            "status": "ready",
            "mode": "interactive_open",
            "backend": backend,
            "session_id": session_id,
            "job_id": job_id,
            "default_output_dir": str(session_record.get("default_output_dir", default_output_dir)),
            "message": "Interactive GPU session ready",
        }

    existing = _load_session_record(session_id) if _session_file(session_id).exists() else session_record
    running, heartbeat_age = _qlogin_worker_running(session_id, existing, stale_seconds=max(30, poll_interval * 4))
    if attach and running:
        existing["status"] = "ready"
        existing["attached_at_utc"] = _utc_now_iso()
        _save_session_record(existing)
        return {
            "status": "ready",
            "mode": "interactive_open",
            "backend": backend,
            "session_id": session_id,
            "launcher_pid": int(existing.get("launcher_pid", 0) or 0),
            "worker_pid": int(existing.get("worker_pid", 0) or 0),
            "default_output_dir": str(existing.get("default_output_dir", default_output_dir)),
            "session_dir": str(session_dir),
            "attached": True,
            "heartbeat_age_seconds": round(heartbeat_age, 1) if isinstance(heartbeat_age, float) else None,
            "message": "Interactive GPU session ready",
        }

    if requested_session_id and had_existing_session and not attach:
        raise RunnerError(f"Interactive session already exists: {session_id}. Use --attach 1 to reuse it.")

    launcher = str(
        getattr(args, "interactive_launcher", "") or session_record.get("launcher", "") or DEFAULT_INTERACTIVE_LAUNCHER
    ).strip()
    started = _restart_qlogin_session_worker(
        session_id=session_id,
        session_record=session_record,
        launcher=launcher,
        startup_timeout=startup_timeout,
        poll_interval=poll_interval,
    )
    return {
        "status": "ready",
        "mode": "interactive_open",
        "backend": backend,
        "session_id": session_id,
        "launcher_pid": int(started.get("launcher_pid", 0) or 0),
        "worker_pid": int(started.get("worker_pid", 0) or 0),
        "default_output_dir": str(started.get("default_output_dir", default_output_dir)),
        "session_dir": str(session_dir),
        "attached": bool(attach and had_existing_session),
        "message": "Interactive GPU session ready",
    }


def run_interactive_submit(args: argparse.Namespace) -> dict[str, Any]:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        raise RunnerError("interactive-submit requires --session-id.")
    session = _load_session_record(session_id)
    backend = str(session.get("backend", "")).strip().lower()

    if backend == "jobid":
        launcher_template = str(session.get("job_launcher", "srun --jobid {job_id}"))
        launcher = launcher_template.format(job_id=str(session.get("job_id", "")))
        passthrough = argparse.Namespace(**vars(args))
        passthrough.interactive_launcher = launcher
        if not passthrough.output_dir:
            passthrough.output_dir = str(session.get("default_output_dir", "") or "")
        result = run_interactive(passthrough)
        result["session_id"] = session_id
        result["backend"] = backend
        return result

    if backend != "qlogin":
        raise RunnerError(f"Unsupported interactive session backend: {backend!r}")

    running, heartbeat_age = _qlogin_worker_running(session_id, session)
    if not running:
        open_args = argparse.Namespace(
            backend="qlogin",
            session_id=session_id,
            output_dir=str(session.get("default_output_dir", "") or ""),
            interactive_launcher=str(session.get("launcher", "") or getattr(args, "interactive_launcher", "") or DEFAULT_INTERACTIVE_LAUNCHER),
            job_id="",
            job_launcher="",
            startup_timeout=max(30, int(getattr(args, "wait_timeout", 0)) or 180),
            poll_interval=max(1, int(getattr(args, "poll_interval", 5))),
            attach=True,
            attach_only=False,
        )
        reopened = run_interactive_open(open_args)
        session = _load_session_record(session_id)
        running, heartbeat_age = _qlogin_worker_running(session_id, session)
        if not running:
            session_dir = _session_dir(session_id)
            heartbeat_note = (
                f", heartbeat_age={heartbeat_age:.1f}s" if isinstance(heartbeat_age, float) else ", heartbeat=missing"
            )
            raise RunnerError(
                f"Interactive session '{session_id}' is not running after restart attempt{heartbeat_note}. "
                "Re-open the session with interactive-open.\n"
                f"Session logs:\n- {session_dir / 'worker.stdout.log'}\n- {session_dir / 'worker.stderr.log'}"
            )
        if isinstance(reopened, dict):
            session["last_auto_restart_at_utc"] = _utc_now_iso()
            _save_session_record(session)

    request_id, request_doc = _build_interactive_request(session_id=session_id, args=args, session=session)
    request_path = _request_spool_dir(session_id, request_id) / "request.json"
    request_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(request_path, request_doc)

    if not bool(args.poll):
        return {
            "status": "submitted",
            "mode": "interactive_submit",
            "session_id": session_id,
            "request_id": request_id,
            "backend": backend,
            "submitted_by": request_doc.get("submitted_by", ""),
            "queue_position": int(request_doc.get("queue_position", 1)),
            "started_at": None,
            "finished_at": None,
            "output_dir": request_doc.get("output_dir", ""),
        }

    timeout_seconds = max(0, int(args.wait_timeout))
    poll_interval = max(1, int(args.poll_interval))
    started = time.time()
    result_path = _interactive_result_path(session_id, request_id)

    while True:
        if result_path.exists():
            result = _load_json(result_path)
            result = _finalize_interactive_result_output(
                session_id=session_id,
                request_id=request_id,
                request_doc=request_doc,
                result_doc=result,
            )
            result["mode"] = "interactive"
            result["backend"] = backend
            result["session_id"] = session_id
            result["request_id"] = request_id
            if str(result.get("status", "")).lower() == "failed":
                raise RunnerError(
                    "Interactive render failed.\n"
                    f"session_id: {session_id}\n"
                    f"request_id: {request_id}\n"
                    f"error: {result.get('error', '<unknown>')}"
                )
            return result

        if timeout_seconds and (time.time() - started) >= timeout_seconds:
            return {
                "status": "timeout",
                "mode": "interactive_submit",
                "backend": backend,
                "session_id": session_id,
                "request_id": request_id,
                "output_dir": request_doc.get("output_dir", ""),
            }

        time.sleep(poll_interval)


def run_interactive_status(args: argparse.Namespace) -> dict[str, Any]:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        raise RunnerError("interactive-status requires --session-id.")
    session = _load_session_record(session_id)

    backend = str(session.get("backend", "")).strip().lower()
    result: dict[str, Any] = {
        "mode": "interactive_status",
        "session_id": session_id,
        "backend": backend,
        "status": str(session.get("status", "unknown")),
        "default_output_dir": str(session.get("default_output_dir", "") or ""),
    }

    if backend == "qlogin":
        pid = session.get("worker_pid")
        launcher_pid = session.get("launcher_pid")
        running, heartbeat_age = _qlogin_worker_running(session_id, session)
        pending = len(_list_request_dirs_fifo(session_id))
        completed = len([path for path in _spool_responses_dir(session_id).glob("*/result.json") if path.is_file()])
        job_ids = _discover_slurm_job_ids(session_id)
        result.update(
            {
                "launcher_pid": launcher_pid,
                "launcher_running": _session_pid_alive(launcher_pid),
                "worker_pid": pid,
                "worker_host": session.get("worker_host", ""),
                "worker_running": running,
                "pending_requests": pending,
                "completed_requests": completed,
                "slurm_job_ids": job_ids,
            }
        )
        if isinstance(heartbeat_age, float):
            result["heartbeat_age_seconds"] = round(heartbeat_age, 1)
        else:
            result["heartbeat_age_seconds"] = None
        if running and result["status"] == "ready":
            result["status"] = "running"
        elif not running and result["status"] in {"ready", "running", "starting"}:
            result["status"] = "stopped"
        return result

    if backend == "jobid":
        job_id = str(session.get("job_id", "") or "")
        scheduler_state = "unknown"
        if job_id:
            check = _run_subprocess(["squeue", "-h", "-j", job_id, "-o", "%T"])
            if check.returncode == 0:
                state = (check.stdout or "").strip()
                scheduler_state = state if state else "not_found"
        result["job_id"] = job_id
        result["scheduler_state"] = scheduler_state
        if scheduler_state == "not_found" and result["status"] == "ready":
            result["status"] = "finished"
        return result

    raise RunnerError(f"Unsupported interactive session backend: {backend!r}")


def run_interactive_close(args: argparse.Namespace) -> dict[str, Any]:
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        raise RunnerError("interactive-close requires --session-id.")
    session = _load_session_record(session_id)
    backend = str(session.get("backend", "")).strip().lower()
    wait_seconds = max(0, int(getattr(args, "wait_timeout", 30)))

    if backend == "qlogin":
        stop_file = _session_dir(session_id) / "control" / "stop"
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text(_utc_now_iso() + "\n", encoding="utf-8")

        pid = session.get("worker_pid")
        launcher_pid = session.get("launcher_pid")
        worker_host = str(session.get("worker_host", "")).strip()
        local_host = socket.gethostname()
        local_worker_pid = bool(worker_host and _same_host(worker_host, local_host))
        wait_flag = bool(getattr(args, "wait", True))
        force_flag = bool(getattr(args, "force", False))
        if wait_flag:
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                refreshed = _load_session_record(session_id)
                running, _heartbeat_age = _qlogin_worker_running(session_id, refreshed, stale_seconds=10)
                if not running:
                    break
                time.sleep(1)

        if _session_pid_alive(launcher_pid):
            try:
                os.kill(int(launcher_pid), signal.SIGTERM)
            except Exception:
                pass

        if force_flag and local_worker_pid and _session_pid_alive(pid):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
            deadline = time.time() + 5
            while time.time() < deadline and _session_pid_alive(pid):
                time.sleep(1)
            if _session_pid_alive(pid):
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except Exception:
                    pass

        if force_flag and _session_pid_alive(launcher_pid):
            try:
                os.kill(int(launcher_pid), signal.SIGKILL)
            except Exception:
                pass

        job_ids = _discover_slurm_job_ids(session_id)
        cancelled_job_ids, failed_job_ids = _cancel_slurm_jobs(job_ids)

        refreshed = _load_session_record(session_id)
        running, _heartbeat_age = _qlogin_worker_running(session_id, refreshed, stale_seconds=10)
        if running:
            refreshed["status"] = "close_requested"
            refreshed["close_requested_at_utc"] = _utc_now_iso()
            final_status = "close_requested"
        else:
            refreshed["status"] = "closed"
            refreshed["closed_at_utc"] = _utc_now_iso()
            final_status = "closed"
        _save_session_record(refreshed)
        if final_status == "closed":
            _session_ready_flag(session_id).unlink(missing_ok=True)  # type: ignore[arg-type]
            _session_heartbeat_file(session_id).unlink(missing_ok=True)  # type: ignore[arg-type]
        return {
            "status": final_status,
            "mode": "interactive_close",
            "session_id": session_id,
            "backend": backend,
            "worker_pid": pid,
            "worker_host": worker_host,
            "launcher_pid": launcher_pid,
            "cancelled_job_ids": cancelled_job_ids,
            "failed_cancel_job_ids": failed_job_ids,
        }

    if backend == "jobid":
        session["status"] = "closed"
        session["closed_at_utc"] = _utc_now_iso()
        _save_session_record(session)
        return {
            "status": "closed",
            "mode": "interactive_close",
            "session_id": session_id,
            "backend": backend,
            "job_id": session.get("job_id", ""),
        }

    raise RunnerError(f"Unsupported interactive session backend: {backend!r}")


def _encode_json_b64(data: dict[str, Any]) -> str:
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _extract_heredoc(script_text: str, marker: str) -> str:
    pattern = re.compile(rf"cat <<'{re.escape(marker)}' > .*?\n(.*?)\n{re.escape(marker)}", re.DOTALL)
    match = pattern.search(script_text)
    if not match:
        raise RunnerError(f"Could not find heredoc marker {marker!r} in generated script.")
    return match.group(1).strip()


def extract_embedded_payload(script_text: str) -> dict[str, Any]:
    encoded = _extract_heredoc(script_text, "PAYLOAD_B64")
    decoded = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    parsed = json.loads(decoded)
    if not isinstance(parsed, dict):
        raise RunnerError("Embedded payload decoded to non-object JSON.")
    return parsed


def extract_embedded_style(script_text: str) -> dict[str, Any] | None:
    if "STYLE_B64" not in script_text:
        return None
    encoded = _extract_heredoc(script_text, "STYLE_B64")
    decoded = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    parsed = json.loads(decoded)
    if not isinstance(parsed, dict):
        raise RunnerError("Embedded style decoded to non-object JSON.")
    return parsed


def build_sbatch_script(
    *,
    run_id: str,
    payload: dict[str, Any],
    preset: str | None,
    style_doc: dict[str, Any] | None,
    blender_exe: str,
    output_dir: str,
    output_name: str,
    custom_script: Path,
    use_apptainer: bool,
    apptainer_image: str,
    rough: bool,
    raw_view: bool,
    color_follow: bool,
    save_blend: bool,
    blend_output: str,
    keep_temp: bool = False,
    slurm_partition: str,
    slurm_account: str,
    slurm_gres: str,
    slurm_time: str,
) -> str:
    payload_b64 = _encode_json_b64(payload)
    style_b64 = _encode_json_b64(style_doc) if style_doc is not None else None

    python_exec = shlex.quote(_default_remote_python_executable())
    runner_path = shlex.quote(str(Path(__file__).resolve()))
    custom_script_q = shlex.quote(str(custom_script))
    slurm_log_dir = TMP_BRIDGE_ROOT / run_id

    sbatch_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"#SBATCH --job-name=pymol_bridge_{run_id}",
        f"#SBATCH --output={slurm_log_dir}/slurm-%j.out",
        f"#SBATCH --error={slurm_log_dir}/slurm-%j.err",
        f"#SBATCH --gres={slurm_gres}",
        f"#SBATCH --time={slurm_time}",
    ]
    if slurm_partition:
        sbatch_lines.append(f"#SBATCH --partition={slurm_partition}")
    if slurm_account:
        sbatch_lines.append(f"#SBATCH --account={slurm_account}")

    tmp_root_q = shlex.quote(str(TMP_BRIDGE_ROOT))
    script_lines = sbatch_lines + [
        "",
        f"RUN_ID={shlex.quote(run_id)}",
        f"TMP_ROOT={tmp_root_q}",
        "mkdir -p \"$TMP_ROOT\"",
        "RUN_DIR=\"$TMP_ROOT/${SLURM_JOB_ID:-$RUN_ID}\"",
        "mkdir -p \"$RUN_DIR\"",
        "",
        "cat <<'PAYLOAD_B64' > \"$RUN_DIR/payload.json.b64\"",
        payload_b64,
        "PAYLOAD_B64",
        "base64 --decode \"$RUN_DIR/payload.json.b64\" > \"$RUN_DIR/payload.json\"",
    ]

    if style_b64 is not None:
        script_lines.extend(
            [
                "",
                "cat <<'STYLE_B64' > \"$RUN_DIR/style.json.b64\"",
                style_b64,
                "STYLE_B64",
                "base64 --decode \"$RUN_DIR/style.json.b64\" > \"$RUN_DIR/style.json\"",
            ]
        )

    local_command_lines = [
        f"{python_exec} {runner_path} local",
        '  --payload-json "$RUN_DIR/payload.json"',
        f"  --blender-exe {shlex.quote(blender_exe)}",
        f"  --custom-script {custom_script_q}",
        f"  --save-blend {1 if save_blend else 0}",
    ]

    if preset is not None:
        local_command_lines.append(f"  --preset {shlex.quote(preset)}")
    else:
        local_command_lines.append('  --style-json "$RUN_DIR/style.json"')

    if output_dir:
        local_command_lines.append(f"  --output-dir {shlex.quote(output_dir)}")
    if output_name:
        local_command_lines.append(f"  --output-name {shlex.quote(output_name)}")

    if use_apptainer:
        local_command_lines.append("  --use-apptainer 1")
        local_command_lines.append(f"  --apptainer-image {shlex.quote(apptainer_image)}")
    if rough:
        local_command_lines.append("  --rough 1")
    if raw_view:
        local_command_lines.append("  --raw-view 1")
    if color_follow:
        local_command_lines.append("  --color-follow 1")
    if blend_output:
        local_command_lines.append(f"  --blend-output {shlex.quote(blend_output)}")
    if keep_temp:
        local_command_lines.append("  --keep-temp 1")

    script_lines.append("")
    for idx, line in enumerate(local_command_lines):
        suffix = " \\" if idx < len(local_command_lines) - 1 else ""
        script_lines.append(f"{line}{suffix}")

    script_lines.append("")
    script_lines.append("echo \"SLURM local render command completed\"")
    return "\n".join(script_lines) + "\n"


def _parse_sbatch_job_id(stdout: str) -> str:
    raw = (stdout or "").strip()
    if not raw:
        raise RunnerError("sbatch returned empty output; could not determine job id.")

    token = raw.split(";", 1)[0].strip()
    if token.isdigit():
        return token

    match = re.search(r"(\d+)", raw)
    if match:
        return match.group(1)

    raise RunnerError(f"Could not parse SLURM job id from output: {raw!r}")


def _poll_until_complete(job_id: str, poll_interval: int, max_polls: int = 720) -> str:
    for _ in range(max_polls):
        check = _run_subprocess(["squeue", "-h", "-j", str(job_id), "-o", "%T"])
        if check.returncode != 0:
            return "unknown"
        state = (check.stdout or "").strip()
        if not state:
            return "finished"
        time.sleep(max(1, poll_interval))
    return "timeout"


def run_slurm(args: argparse.Namespace) -> dict[str, Any]:
    rough = bool(getattr(args, "rough", False))
    raw_view = bool(getattr(args, "raw_view", False))
    payload_path = Path(args.payload_json).expanduser().resolve()
    payload = validate_payload_document(_load_json(payload_path))

    style_doc: dict[str, Any] | None = None
    if args.style_json:
        style_doc = load_and_validate_style_json(Path(args.style_json).expanduser().resolve())

    run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else _new_run_id()

    output_png = _resolve_output_png(
        output_dir=args.output_dir,
        output_name=args.output_name,
        preset=args.preset,
        style_doc=style_doc,
    )

    script = build_sbatch_script(
        run_id=run_id,
        payload=payload,
        preset=args.preset,
        style_doc=style_doc,
        blender_exe=args.blender_exe,
        output_dir=str(output_png.parent),
        output_name=output_png.name,
        custom_script=_expand_path_no_resolve(args.custom_script),
        use_apptainer=args.use_apptainer,
        apptainer_image=args.apptainer_image,
        rough=rough,
        raw_view=raw_view,
        color_follow=bool(getattr(args, "color_follow", False)),
        save_blend=bool(getattr(args, "save_blend", False)),
        blend_output=str(getattr(args, "blend_output", "") or ""),
        keep_temp=bool(getattr(args, "keep_temp", False)),
        slurm_partition=args.slurm_partition,
        slurm_account=args.slurm_account,
        slurm_gres=args.slurm_gres,
        slurm_time=args.slurm_time,
    )

    submit_dir = _ensure_tmp_run_dir(run_id)
    script_path = submit_dir / "submit.sbatch"
    script_path.write_text(script, encoding="utf-8")

    if args.dry_run:
        return {
            "status": "dry_run",
            "mode": "slurm",
            "run_id": run_id,
            "script_path": str(script_path),
            "output_png": str(output_png),
            "output_dir": str(output_png.parent),
        }

    submit = _run_subprocess(["sbatch", "--parsable", str(script_path)])
    if submit.returncode != 0:
        raise RunnerError(
            "sbatch submission failed.\n"
            f"stdout:\n{(submit.stdout or '').strip()}\n"
            f"stderr:\n{(submit.stderr or '').strip()}"
        )
    job_id = _parse_sbatch_job_id(submit.stdout)

    status = "submitted"
    if args.poll:
        poll_state = _poll_until_complete(job_id, poll_interval=args.poll_interval)
        if poll_state == "finished" and output_png.exists():
            status = "completed"
        elif poll_state == "finished":
            status = "finished_no_output"
        elif poll_state == "timeout":
            status = "timeout"
        else:
            status = "unknown"

    return {
        "status": status,
        "mode": "slurm",
        "run_id": run_id,
        "job_id": job_id,
        "script_path": str(script_path),
        "output_png": str(output_png),
        "output_dir": str(output_png.parent),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bridge_runner",
        description="Run local Blender renders, persistent interactive sessions, or submit SLURM jobs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_options(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--payload-json", required=True, help="Path to JSON payload with CIF + PyMOL view metadata.")
        mode_group = sp.add_mutually_exclusive_group(required=True)
        mode_group.add_argument("--preset", choices=sorted(PRESET_OUTPUT_FILENAMES))
        mode_group.add_argument("--style-json", default="")
        sp.add_argument("--output-dir", default="", help="Final output directory (default: <cwd>/renders/<timestamp>/).")
        sp.add_argument("--output-name", default="", help="Output image name override.")
        sp.add_argument("--blender-exe", default=DEFAULT_BLENDER_EXE)
        sp.add_argument("--custom-script", default=str(_default_custom_script()))
        sp.add_argument("--use-apptainer", type=_parse_bool, default=False)
        sp.add_argument("--apptainer-image", default="")
        sp.add_argument("--rough", type=_parse_bool, default=False)
        sp.add_argument("--raw-view", type=_parse_bool, default=False)
        sp.add_argument("--color-follow", type=_parse_bool, default=False)
        sp.add_argument("--save-blend", type=_parse_bool, default=False)
        sp.add_argument("--blend-output", default="")
        sp.add_argument("--keep-temp", type=_parse_bool, default=False)

    local_parser = subparsers.add_parser("local", help="Run render locally with Blender.")
    add_common_options(local_parser)

    interactive_open_parser = subparsers.add_parser(
        "interactive-open",
        help="Open a persistent interactive render session.",
    )
    interactive_open_parser.add_argument("--backend", choices=["qlogin", "jobid"], default="qlogin")
    interactive_open_parser.add_argument("--session-id", default="")
    interactive_open_parser.add_argument("--output-dir", default="")
    interactive_open_parser.add_argument(
        "--interactive-launcher",
        default=DEFAULT_INTERACTIVE_LAUNCHER,
        help="Launcher command used to reserve an interactive GPU allocation (qlogin backend).",
    )
    interactive_open_parser.add_argument(
        "--attach",
        type=_parse_bool,
        default=False,
        help="Attach to an existing healthy qlogin session when possible.",
    )
    interactive_open_parser.add_argument(
        "--attach-only",
        type=_parse_bool,
        default=False,
        help="Require attaching to an existing healthy qlogin session; fail otherwise.",
    )
    interactive_open_parser.add_argument("--job-id", default="", help="Existing allocation job id (jobid backend).")
    interactive_open_parser.add_argument(
        "--job-launcher",
        default="srun --jobid {job_id}",
        help="Launcher template for jobid backend. Must contain '{job_id}'.",
    )
    interactive_open_parser.add_argument("--startup-timeout", type=int, default=900)
    interactive_open_parser.add_argument("--poll-interval", type=int, default=2)

    interactive_submit_parser = subparsers.add_parser(
        "interactive-submit",
        help="Submit a render request to an interactive session.",
    )
    add_common_options(interactive_submit_parser)
    interactive_submit_parser.add_argument("--session-id", required=True)
    interactive_submit_parser.add_argument("--poll", type=_parse_bool, default=True)
    interactive_submit_parser.add_argument("--poll-interval", type=int, default=5)
    interactive_submit_parser.add_argument(
        "--wait-timeout",
        type=int,
        default=0,
        help="Wait timeout in seconds when --poll=1 (0 means no timeout).",
    )

    interactive_alias_parser = subparsers.add_parser(
        "interactive",
        help="Compatibility alias for interactive-submit.",
    )
    add_common_options(interactive_alias_parser)
    interactive_alias_parser.add_argument("--session-id", required=True)
    interactive_alias_parser.add_argument("--poll", type=_parse_bool, default=True)
    interactive_alias_parser.add_argument("--poll-interval", type=int, default=5)
    interactive_alias_parser.add_argument("--wait-timeout", type=int, default=0)

    interactive_status_parser = subparsers.add_parser(
        "interactive-status",
        help="Get interactive session status.",
    )
    interactive_status_parser.add_argument("--session-id", required=True)

    interactive_close_parser = subparsers.add_parser(
        "interactive-close",
        help="Close an interactive session.",
    )
    interactive_close_parser.add_argument("--session-id", required=True)
    interactive_close_parser.add_argument("--wait", type=_parse_bool, default=True)
    interactive_close_parser.add_argument("--wait-timeout", type=int, default=30)
    interactive_close_parser.add_argument("--force", type=_parse_bool, default=False)

    interactive_worker_parser = subparsers.add_parser("_interactive-worker", help=argparse.SUPPRESS)
    interactive_worker_parser.add_argument("--session-dir", required=True)
    interactive_worker_parser.add_argument("--poll-interval", type=int, default=2)
    # Some qlogin wrappers append an interactive shell command (for example: /bin/zsh -l).
    # Accept and ignore trailing launcher tokens so worker startup remains robust.
    interactive_worker_parser.add_argument("launcher_tail", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    slurm_parser = subparsers.add_parser("slurm", help="Submit render via sbatch and optionally poll.")
    add_common_options(slurm_parser)
    slurm_parser.add_argument("--slurm-partition", default="")
    slurm_parser.add_argument("--slurm-account", default="")
    slurm_parser.add_argument("--slurm-gres", default="gpu:1")
    slurm_parser.add_argument("--slurm-time", default="00:30:00")
    slurm_parser.add_argument("--poll", type=_parse_bool, default=True)
    slurm_parser.add_argument("--poll-interval", type=int, default=5)
    slurm_parser.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "local":
            result = run_local(args)
        elif args.command == "interactive-open":
            result = run_interactive_open(args)
        elif args.command in {"interactive-submit", "interactive"}:
            result = run_interactive_submit(args)
        elif args.command == "interactive-status":
            result = run_interactive_status(args)
        elif args.command == "interactive-close":
            result = run_interactive_close(args)
        elif args.command == "_interactive-worker":
            result = run_interactive_worker(args)
        elif args.command == "slurm":
            result = run_slurm(args)
        else:
            raise RunnerError(f"Unknown command: {args.command}")
    except RunnerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
