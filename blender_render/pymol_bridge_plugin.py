"""PyMOL-loadable plugin for local/SLURM Blender rendering."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _detect_default_blender_exe() -> str:
    if sys.platform == "darwin":
        return "/Applications/Blender.app/Contents/MacOS/Blender"
    candidates = [
        shutil.which("blender"),
        "/usr/bin/blender",
        "/usr/local/bin/blender",
        "/opt/blender/blender",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path)
    return "/Applications/Blender.app/Contents/MacOS/Blender"


DEFAULT_BLENDER_EXE = _detect_default_blender_exe()
SUPPORTED_PRESETS = {
    "preset_1_interface",
    "preset_2_frosted_blue",
    "preset_3_black_tubes",
    "preset_4_milky_green",
    "preset_5_black_interface",
}
SUPPORTED_INTERACTIVE_BACKENDS = {"qlogin", "jobid"}
_ACTIVE_INTERACTIVE_SESSION_ID: str | None = None
NUCLEIC_RES_NAMES = {"DA", "DT", "DG", "DC", "A", "U", "G", "C"}

try:
    from pymol import cmd as _PYMOL_CMD
except Exception:  # pragma: no cover - import guarded for unit tests
    _PYMOL_CMD = None


def _normalize_cli_string(value: Any) -> str:
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


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
    records: list[tuple[str, str, str, int, int]] = []
    space: dict[str, Any] = {"records": records}
    try:
        cmd_obj.iterate(
            selection,
            "records.append((chain, resn, resi, int(color), int(hetatm)))",
            space=space,
        )
    except Exception:
        return {}

    chain_color_counts: dict[str, Counter[str]] = defaultdict(Counter)
    class_color_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for chain, resn, _resi, color_idx, hetatm in records:
        chain_name = str(chain or "").strip() or "_"
        resn_name = str(resn or "").strip().upper()
        try:
            rgb = cmd_obj.get_color_tuple(int(color_idx))
            color_hex = _rgb_to_hex((float(rgb[0]), float(rgb[1]), float(rgb[2])))
        except Exception:
            continue
        chain_color_counts[chain_name][color_hex] += 1
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
        "source": "pymol",
    }


def _collect_secondary_structure_hints(cmd_obj: Any, selection: str) -> dict[str, Any]:
    records: list[tuple[str, str, str, int]] = []
    space: dict[str, Any] = {"records": records}
    try:
        cmd_obj.iterate(
            selection,
            "records.append((chain, resi, ss, int(hetatm)))",
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
        try:
            resi_i = int(str(resi or "").strip())
        except Exception:
            continue
        chain_name = str(chain or "").strip().upper()
        if not chain_name:
            continue
        key = f"{chain_name}:{resi_i}"
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
    has_atom_headers = any(line.lower().startswith("_atom_site.") for line in lines)
    has_atom_rows = any(line.startswith("ATOM") or line.startswith("HETATM") for line in lines)
    return bool(has_data_block and (has_atom_headers or has_atom_rows))


def _looks_like_pdb_atoms(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return any(line.startswith(("ATOM", "HETATM")) for line in text.splitlines())


def build_render_payload(
    cmd_obj: Any,
    selection: str,
    state: int = -1,
    *,
    include_color_hints: bool = False,
) -> dict[str, Any]:
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
    if cif_ok:
        payload["cif_text"] = cif_text
    if pdb_ok:
        payload["pdb_text"] = pdb_text
    if include_color_hints:
        color_hints = _collect_color_hints(cmd_obj, selection)
        if color_hints:
            payload["color_hints"] = color_hints
    return payload


def _runner_path() -> Path:
    candidates: list[Path] = []

    module_file = globals().get("__file__")
    if isinstance(module_file, str) and module_file:
        candidates.append(Path(module_file).expanduser().resolve().with_name("bridge_runner.py"))

    try:
        source_file = Path(bridge_render.__code__.co_filename).expanduser().resolve()
        candidates.append(source_file.with_name("bridge_runner.py"))
    except Exception:
        pass

    override_root = os.environ.get("PYMOL_BRIDGE_ROOT", "").strip()
    if override_root:
        candidates.append(Path(override_root).expanduser().resolve() / "bridge_runner.py")

    seen: set[str] = set()
    unique_candidates: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(path)

    for path in unique_candidates:
        if path.is_file():
            return path

    checked = "\n".join(f"- {path}" for path in unique_candidates) or "- <none>"
    raise RuntimeError(
        "Could not locate bridge_runner.py next to the loaded plugin.\n"
        f"Checked:\n{checked}\n"
        "Fix: load pymol_bridge_plugin.py from its project folder, or set PYMOL_BRIDGE_ROOT."
    )


def _default_apptainer_image() -> Path:
    candidates: list[Path] = []

    override_root = os.environ.get("PYMOL_BRIDGE_ROOT", "").strip()
    if override_root:
        candidates.append(Path(override_root).expanduser().resolve() / "blender_bridge.sif")

    module_file = globals().get("__file__")
    if isinstance(module_file, str) and module_file:
        candidates.append(Path(module_file).expanduser().resolve().with_name("blender_bridge.sif"))

    try:
        source_file = Path(bridge_render.__code__.co_filename).expanduser().resolve()
        candidates.append(source_file.with_name("blender_bridge.sif"))
    except Exception:
        pass

    try:
        candidates.append(_runner_path().with_name("blender_bridge.sif"))
    except Exception:
        pass

    candidates.append(Path.cwd() / "blender_render" / "blender_bridge.sif")

    seen: set[str] = set()
    fallback: Path | None = None
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if fallback is None:
            fallback = candidate
        if candidate.is_file():
            return candidate

    return fallback if fallback is not None else Path("blender_bridge.sif")


def _find_apptainer_executable() -> str | None:
    resolved = shutil.which("apptainer")
    if resolved and Path(resolved).is_file():
        return resolved

    for candidate in ("/usr/bin/apptainer", "/usr/local/bin/apptainer"):
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def _blender_executable_exists(blender_exe: str) -> bool:
    expanded = Path(blender_exe).expanduser()
    if expanded.is_file():
        return True

    executable_name = expanded.name
    if executable_name and not expanded.is_absolute():
        resolved = shutil.which(executable_name)
        if resolved and Path(resolved).is_file():
            return True

    return False


def _write_payload(payload: dict[str, Any]) -> Path:
    run_id = payload.get("run_id") or uuid.uuid4().hex[:12]
    run_dir = Path("/tmp/pymol_bridge") / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload_path = run_dir / "payload.json"
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload_path


def _get_active_interactive_session_id() -> str:
    global _ACTIVE_INTERACTIVE_SESSION_ID
    return str(_ACTIVE_INTERACTIVE_SESSION_ID or "").strip()


def _set_active_interactive_session_id(session_id: str) -> None:
    global _ACTIVE_INTERACTIVE_SESSION_ID
    session = str(session_id).strip()
    _ACTIVE_INTERACTIVE_SESSION_ID = session if session else None


def _run_runner(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Bridge runner failed.\n"
            f"Command: {' '.join(command)}\n"
            f"stdout:\n{(result.stdout or '').strip()}\n"
            f"stderr:\n{(result.stderr or '').strip()}"
        )

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("Bridge runner completed but did not return JSON output.")

    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse bridge runner JSON output: {lines[-1]!r}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Bridge runner output JSON was not an object.")
    return parsed


def try_load_preview(cmd_obj: Any, output_png: str) -> bool:
    try:
        cmd_obj.load_png(output_png, movie=1, quiet=1)
    except Exception:
        return False
    return True


def bridge_render(
    selection: str = "(all)",
    preset: str = "preset_1_interface",
    style_json: str = "",
    mode: str = "local",
    interactive_session: str = "",
    interactive_launcher: str = "qlogin -p gpu --gres=gpu:a4000:1 --mem=16g -c 2 -t 120",
    rough: int | str = 0,
    color_follow: int | str = 0,
    output_dir: str = "",
    output_name: str = "",
    blender_exe: str = DEFAULT_BLENDER_EXE,
    use_apptainer: int | str = 0,
    apptainer_image: str = "",
    slurm_partition: str = "",
    slurm_account: str = "",
    slurm_gres: str = "gpu:1",
    slurm_time: str = "00:30:00",
    poll: int | str = 1,
    poll_interval: int | str = 5,
    save_blend: int | str = 0,
    blend_output: str = "",
    keep_temp: int | str = 0,
) -> dict[str, Any]:
    if _PYMOL_CMD is None:
        raise RuntimeError("PyMOL cmd API is unavailable. Load this module from inside PyMOL.")

    selection = _normalize_cli_string(selection)
    preset = _normalize_cli_string(preset)
    style_json = _normalize_cli_string(style_json)
    render_mode = _normalize_cli_string(mode).lower()
    interactive_session = _normalize_cli_string(interactive_session)
    interactive_launcher = _normalize_cli_string(interactive_launcher)
    rough_flag = _parse_bool(rough)
    color_follow_flag = _parse_bool(color_follow)
    output_dir = _normalize_cli_string(output_dir)
    output_name = _normalize_cli_string(output_name)
    blender_exe = _normalize_cli_string(blender_exe)
    apptainer_image = _normalize_cli_string(apptainer_image)
    slurm_partition = _normalize_cli_string(slurm_partition)
    slurm_account = _normalize_cli_string(slurm_account)
    slurm_gres = _normalize_cli_string(slurm_gres)
    slurm_time = _normalize_cli_string(slurm_time)
    save_blend_flag = _parse_bool(save_blend)
    keep_temp_flag = _parse_bool(keep_temp)
    blend_output = _normalize_cli_string(blend_output)

    if render_mode not in {"local", "interactive", "slurm", "interactive_open", "interactive_status", "interactive_close"}:
        raise RuntimeError(
            "mode must be 'local', 'interactive', 'slurm', "
            "'interactive_open', 'interactive_status', or 'interactive_close'."
        )

    if render_mode == "interactive_open":
        result = bridge_render_interactive_open(
            backend="qlogin",
            interactive_launcher=interactive_launcher,
            poll_interval=_parse_int(poll_interval, 2),
        )
        return result
    if render_mode == "interactive_status":
        return bridge_render_interactive_status(session_id=interactive_session)
    if render_mode == "interactive_close":
        return bridge_render_interactive_close(session_id=interactive_session)

    use_apptainer_flag = _parse_bool(use_apptainer)
    if render_mode in {"local", "interactive"}:
        default_image = _default_apptainer_image()

        if apptainer_image:
            use_apptainer_flag = True
        elif use_apptainer_flag and default_image.is_file():
            apptainer_image = str(default_image)
        elif (
            render_mode == "local"
            and not use_apptainer_flag
            and not apptainer_image
            and not style_json
            and preset == "preset_1_interface"
            and selection == "(all)"
            and not output_dir
            and not output_name
            and not rough_flag
            and not color_follow_flag
            and not save_blend_flag
            and not blend_output
            and blender_exe == DEFAULT_BLENDER_EXE
            and default_image.is_file()
            and sys.platform != "darwin"
        ):
            # Make no-arg blender_render resilient on Linux hosts where local
            # Blender/MN stacks vary, while preserving macOS local behavior.
            use_apptainer_flag = True
            apptainer_image = str(default_image)
    if sys.platform == "darwin" and use_apptainer_flag:
        print("blender_render: apptainer is unavailable on macOS; using host Blender execution.")
        use_apptainer_flag = False
        apptainer_image = ""

    if style_json:
        style_path = Path(style_json).expanduser().resolve()
        if not style_path.exists():
            raise RuntimeError(f"style_json file not found: {style_path}")
    else:
        if preset not in SUPPORTED_PRESETS:
            raise RuntimeError(
                f"Unknown preset: {preset}. Expected one of: {', '.join(sorted(SUPPORTED_PRESETS))}"
            )

    payload = build_render_payload(
        _PYMOL_CMD,
        selection,
        include_color_hints=color_follow_flag,
    )
    summary = payload.get("structure_summary", {})
    if isinstance(summary, dict):
        atom_count = summary.get("atom_count")
        residue_count = summary.get("residue_count")
        chain_ids = summary.get("chain_ids")
        if isinstance(chain_ids, list):
            chain_label = ",".join(str(item) for item in chain_ids if str(item).strip()) or "<none>"
        else:
            chain_label = "<none>"
        if isinstance(atom_count, int) and isinstance(residue_count, int):
            print(
                "blender_render: payload "
                f"atoms={atom_count} residues={residue_count} chains={chain_label}"
            )
    payload_path = _write_payload(payload)

    runner_subcommand = "interactive-submit" if render_mode == "interactive" else render_mode
    command = [
        sys.executable,
        str(_runner_path()),
        runner_subcommand,
        "--payload-json",
        str(payload_path),
        "--blender-exe",
        str(blender_exe),
    ]

    if style_json:
        command.extend(["--style-json", str(Path(style_json).expanduser().resolve())])
    else:
        command.extend(["--preset", preset])
    command.extend(["--rough", "1" if rough_flag else "0"])
    command.extend(["--color-follow", "1" if color_follow_flag else "0"])
    command.extend(["--save-blend", "1" if save_blend_flag else "0"])
    command.extend(["--keep-temp", "1" if keep_temp_flag else "0"])
    if blend_output:
        command.extend(["--blend-output", str(Path(blend_output).expanduser())])

    if output_dir:
        command.extend(["--output-dir", str(Path(output_dir).expanduser())])
    if output_name:
        command.extend(["--output-name", str(output_name)])

    if use_apptainer_flag:
        command.extend(["--use-apptainer", "1"])
        if apptainer_image:
            command.extend(["--apptainer-image", str(Path(apptainer_image).expanduser())])

    if render_mode == "slurm":
        command.extend(["--poll", "1" if _parse_bool(poll) else "0"])
        command.extend(["--poll-interval", str(max(1, _parse_int(poll_interval, 5)))])
        command.extend(["--slurm-gres", slurm_gres])
        command.extend(["--slurm-time", slurm_time])
        if slurm_partition:
            command.extend(["--slurm-partition", slurm_partition])
        if slurm_account:
            command.extend(["--slurm-account", slurm_account])
    elif render_mode == "interactive":
        active_session = interactive_session or _get_active_interactive_session_id()
        if not active_session:
            opened = bridge_render_interactive_open(
                backend="qlogin",
                interactive_launcher=interactive_launcher,
                poll_interval=2,
            )
            active_session = str(opened.get("session_id", "")).strip()
            if not active_session:
                raise RuntimeError(
                    "Interactive mode could not open a session automatically. "
                    "Use mode='interactive_open' or pass interactive_session='...'."
                )
        command.extend(["--session-id", active_session])
        command.extend(["--poll", "1" if _parse_bool(poll) else "0"])
        command.extend(["--poll-interval", str(max(1, _parse_int(poll_interval, 5)))])

    result = _run_runner(command)

    output_png = result.get("output_png")
    status = str(result.get("status", "")).lower()
    if isinstance(output_png, str) and output_png and status in {
        "completed",
        "finished",
        "finished_no_output",
    }:
        if try_load_preview(_PYMOL_CMD, output_png):
            print(f"blender_render: preview loaded into PyMOL: {output_png}")
        else:
            print(f"blender_render: render saved at {output_png} (preview load failed)")
    elif isinstance(output_png, str) and output_png:
        print(f"blender_render: render path: {output_png}")

    runtime_seconds = result.get("runtime_seconds")
    if isinstance(runtime_seconds, (int, float)):
        print(f"blender_render: runtime {float(runtime_seconds):.2f}s")

    gpu_info = result.get("gpu_info")
    if isinstance(gpu_info, list) and gpu_info:
        for line in gpu_info:
            print(f"blender_render: {line}")

    if "job_id" in result:
        print(f"blender_render: SLURM job id {result['job_id']}")
    output_blend = result.get("output_blend")
    if isinstance(output_blend, str) and output_blend:
        print(f"blender_render: blend session saved at {output_blend}")
    temp_dir = result.get("temp_dir")
    if isinstance(temp_dir, str) and temp_dir:
        if bool(result.get("temp_kept", False)):
            print(f"blender_render: kept temp session dir: {temp_dir}")
        else:
            print(f"blender_render: temp session dir cleaned: {temp_dir}")

    return result


def register_plugin(cmd_obj: Any | None = None) -> None:
    target_cmd = cmd_obj if cmd_obj is not None else _PYMOL_CMD
    if target_cmd is None:
        raise RuntimeError("PyMOL cmd API is unavailable; cannot register blender_render.")

    # Primary command name.
    target_cmd.extend("blender_render", bridge_render)


def bridge_render_interactive(
    selection: str = "(all)",
    preset: str = "preset_1_interface",
    style_json: str = "",
    interactive_session: str = "",
    rough: int | str = 0,
    color_follow: int | str = 0,
    output_dir: str = "",
    output_name: str = "",
    blender_exe: str = DEFAULT_BLENDER_EXE,
    use_apptainer: int | str = 0,
    apptainer_image: str = "",
    poll: int | str = 1,
    poll_interval: int | str = 5,
    save_blend: int | str = 0,
    blend_output: str = "",
    keep_temp: int | str = 0,
) -> dict[str, Any]:
    return bridge_render(
        selection=selection,
        preset=preset,
        style_json=style_json,
        mode="interactive",
        interactive_session=interactive_session,
        rough=rough,
        color_follow=color_follow,
        output_dir=output_dir,
        output_name=output_name,
        blender_exe=blender_exe,
        use_apptainer=use_apptainer,
        apptainer_image=apptainer_image,
        poll=poll,
        poll_interval=poll_interval,
        save_blend=save_blend,
        blend_output=blend_output,
        keep_temp=keep_temp,
    )


def bridge_render_interactive_open(
    backend: str = "qlogin",
    interactive_launcher: str = "qlogin -p gpu --gres=gpu:a4000:1 --mem=16g -c 2 -t 120",
    job_id: str = "",
    job_launcher: str = "srun --jobid {job_id}",
    output_dir: str = "",
    session_id: str = "",
    startup_timeout: int | str = 900,
    poll_interval: int | str = 2,
) -> dict[str, Any]:
    backend = _normalize_cli_string(backend).lower()
    interactive_launcher = _normalize_cli_string(interactive_launcher)
    job_id = _normalize_cli_string(job_id)
    job_launcher = _normalize_cli_string(job_launcher)
    output_dir = _normalize_cli_string(output_dir)
    session_id = _normalize_cli_string(session_id)

    if backend not in SUPPORTED_INTERACTIVE_BACKENDS:
        raise RuntimeError(
            f"Unsupported backend: {backend}. Expected one of: {', '.join(sorted(SUPPORTED_INTERACTIVE_BACKENDS))}"
        )

    command = [
        sys.executable,
        str(_runner_path()),
        "interactive-open",
        "--backend",
        backend,
        "--startup-timeout",
        str(max(1, _parse_int(startup_timeout, 900))),
        "--poll-interval",
        str(max(1, _parse_int(poll_interval, 2))),
    ]
    if output_dir:
        command.extend(["--output-dir", str(Path(output_dir).expanduser())])
    if session_id:
        command.extend(["--session-id", session_id])

    if backend == "qlogin":
        command.extend(["--interactive-launcher", interactive_launcher])
    else:
        if not job_id:
            raise RuntimeError("blender_render_interactive_open backend='jobid' requires job_id.")
        command.extend(["--job-id", job_id])
        if job_launcher:
            command.extend(["--job-launcher", job_launcher])

    result = _run_runner(command)
    new_session_id = str(result.get("session_id", "")).strip()
    if new_session_id:
        _set_active_interactive_session_id(new_session_id)
        print(f"blender_render_interactive_open: active session set to {new_session_id}")
    return result


def bridge_render_interactive_status(session_id: str = "") -> dict[str, Any]:
    active = _normalize_cli_string(session_id) or _get_active_interactive_session_id()
    if not active:
        raise RuntimeError(
            "No interactive session selected. "
            "Pass session_id='...' or open one with blender_render_interactive_open(...)."
        )

    command = [
        sys.executable,
        str(_runner_path()),
        "interactive-status",
        "--session-id",
        active,
    ]
    result = _run_runner(command)
    print(
        "blender_render_interactive_status: "
        f"session={active} status={result.get('status', 'unknown')} backend={result.get('backend', '')}"
    )
    return result


def bridge_render_interactive_close(
    session_id: str = "",
    wait: int | str = 1,
    wait_timeout: int | str = 30,
    force: int | str = 0,
) -> dict[str, Any]:
    active = _normalize_cli_string(session_id) or _get_active_interactive_session_id()
    if not active:
        raise RuntimeError(
            "No interactive session selected. "
            "Pass session_id='...' or open one with blender_render_interactive_open(...)."
        )

    command = [
        sys.executable,
        str(_runner_path()),
        "interactive-close",
        "--session-id",
        active,
        "--wait",
        "1" if _parse_bool(wait) else "0",
        "--wait-timeout",
        str(max(0, _parse_int(wait_timeout, 30))),
        "--force",
        "1" if _parse_bool(force) else "0",
    ]
    result = _run_runner(command)
    if active == _get_active_interactive_session_id():
        _set_active_interactive_session_id("")
    print(f"blender_render_interactive_close: closed session {active}")
    return result


if _PYMOL_CMD is not None:  # pragma: no cover - exercised in live PyMOL usage
    register_plugin(_PYMOL_CMD)
