"""PyMOL extraction and bridge bundle export."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bundle_schema import BundleMeta, ViewData, read_bundle_meta, read_view_data, write_bundle_meta, write_view_data


class ExportError(RuntimeError):
    """Raised when PyMOL bundle export fails."""


def _sanitize_filename(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return sanitized or "object"


def _prepare_output_dir(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    structures_dir = out_dir / "structures"
    structures_dir.mkdir(parents=True, exist_ok=True)
    for child in structures_dir.iterdir():
        if child.is_file():
            child.unlink()
    return structures_dir


def _get_setting_float(cmd: Any, name: str) -> float | None:
    getter = getattr(cmd, "get_setting_float", None)
    if getter is None:
        return None
    try:
        return float(getter(name))
    except Exception:
        return None


def _get_setting_bool(cmd: Any, name: str) -> bool | None:
    for getter_name in ("get_setting_boolean", "get_setting_int"):
        getter = getattr(cmd, getter_name, None)
        if getter is None:
            continue
        try:
            return bool(getter(name))
        except Exception:
            continue
    return None


def _get_viewport(cmd: Any) -> tuple[int, int] | None:
    getter = getattr(cmd, "get_viewport", None)
    if getter is None:
        return None
    try:
        viewport = getter()
    except Exception:
        return None
    if not isinstance(viewport, (tuple, list)) or len(viewport) != 2:
        return None
    try:
        w = int(viewport[0])
        h = int(viewport[1])
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return (w, h)


def _build_object_selection(global_selection: str, object_name: str) -> str:
    if global_selection and global_selection != "all":
        return f"({global_selection}) and ({object_name})"
    return object_name


def _save_object_state(
    cmd: Any,
    output_path: Path,
    selection: str,
    state: int,
    file_format: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd.save(str(output_path), selection, state=state, format=file_format)


def _export_from_live_cmd(
    cmd: Any,
    session_pse: Path,
    out_dir: Path,
    state_mode: str,
    selection: str,
    file_format: str,
    notes: str,
) -> tuple[BundleMeta, ViewData]:
    structures_dir = _prepare_output_dir(out_dir)

    try:
        pymol_view = [float(x) for x in cmd.get_view(quiet=1)]
    except Exception as exc:
        raise ExportError(f"Failed to read PyMOL view from session '{session_pse}': {exc}") from exc
    if len(pymol_view) != 18:
        raise ExportError(f"Expected 18 values from cmd.get_view(), got {len(pymol_view)}.")

    viewport = _get_viewport(cmd)
    fov_deg = _get_setting_float(cmd, "field_of_view")
    orthoscopic = _get_setting_bool(cmd, "orthoscopic")

    try:
        object_names = list(cmd.get_object_list(selection))
    except Exception as exc:
        raise ExportError(f"Could not enumerate objects for selection '{selection}': {exc}") from exc
    if not object_names:
        raise ExportError(
            f"No exportable objects found in selection '{selection}'. "
            "Check your selection or session contents."
        )

    current_state = 1
    if state_mode == "current":
        try:
            current_state = int(cmd.get_state())
        except Exception:
            current_state = 1

    for object_name in object_names:
        safe_name = _sanitize_filename(object_name)
        object_selection = _build_object_selection(selection, object_name)
        if state_mode == "all":
            try:
                state_count = int(cmd.count_states(object_name))
            except Exception:
                state_count = 1
            state_count = max(1, state_count)
            for state_idx in range(1, state_count + 1):
                output_name = f"{safe_name}_state_{state_idx}.{file_format}"
                _save_object_state(
                    cmd=cmd,
                    output_path=structures_dir / output_name,
                    selection=object_selection,
                    state=state_idx,
                    file_format=file_format,
                )
        else:
            state = current_state if state_mode == "current" else 1
            output_name = f"{safe_name}.{file_format}"
            _save_object_state(
                cmd=cmd,
                output_path=structures_dir / output_name,
                selection=object_selection,
                state=state,
                file_format=file_format,
            )

    bundle_meta = BundleMeta(
        created_at=datetime.now(timezone.utc).isoformat(),
        source_pse=session_pse.name,
        exported_objects=object_names,
        format=file_format,
        state_mode=state_mode,
        selection=selection,
    )
    view_data = ViewData(
        pymol_view=pymol_view,
        fov_deg=fov_deg,
        orthoscopic=orthoscopic,
        viewport_px=viewport,
        notes=notes,
    )
    write_bundle_meta(out_dir / "bundle.json", bundle_meta)
    write_view_data(out_dir / "view.json", view_data)
    return bundle_meta, view_data


def _export_with_pymol2(
    session_pse: Path,
    out_dir: Path,
    state_mode: str,
    selection: str,
    file_format: str,
) -> tuple[BundleMeta, ViewData]:
    try:
        from pymol2 import PyMOL  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ExportError("pymol2 is not available in this Python environment.") from exc

    try:
        with PyMOL() as pymol:
            cmd = pymol.cmd
            cmd.reinitialize()
            cmd.load(str(session_pse))
            return _export_from_live_cmd(
                cmd=cmd,
                session_pse=session_pse,
                out_dir=out_dir,
                state_mode=state_mode,
                selection=selection,
                file_format=file_format,
                notes="Exported via pymol2 API.",
            )
    except Exception as exc:
        raise ExportError(f"pymol2 export failed for '{session_pse}': {exc}") from exc


def _fallback_export_command() -> str:
    return "\n".join(
        [
            "python",
            "import json",
            "import os",
            "import re",
            "from datetime import datetime, timezone",
            "from pathlib import Path",
            "from pymol import cmd",
            "",
            "payload = json.loads(os.environ['PMBB_PAYLOAD'])",
            "out_dir = Path(payload['out_dir'])",
            "state_mode = payload['state_mode']",
            "selection = payload['selection']",
            "file_format = payload['file_format']",
            "",
            "def sanitize(name):",
            "    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '_', name).strip('._')",
            "    return cleaned or 'object'",
            "",
            "structures = out_dir / 'structures'",
            "structures.mkdir(parents=True, exist_ok=True)",
            "for child in structures.iterdir():",
            "    if child.is_file():",
            "        child.unlink()",
            "",
            "view = [float(x) for x in cmd.get_view(quiet=1)]",
            "if len(view) != 18:",
            "    raise RuntimeError(f'Expected 18 values from cmd.get_view(); got {len(view)}')",
            "",
            "viewport = None",
            "if hasattr(cmd, 'get_viewport'):",
            "    try:",
            "        vp = cmd.get_viewport()",
            "        if isinstance(vp, (tuple, list)) and len(vp) == 2:",
            "            viewport = [int(vp[0]), int(vp[1])]",
            "    except Exception:",
            "        viewport = None",
            "",
            "fov_deg = None",
            "if hasattr(cmd, 'get_setting_float'):",
            "    try:",
            "        fov_deg = float(cmd.get_setting_float('field_of_view'))",
            "    except Exception:",
            "        fov_deg = None",
            "",
            "orthoscopic = None",
            "for getter in ('get_setting_boolean', 'get_setting_int'):",
            "    fn = getattr(cmd, getter, None)",
            "    if fn is None:",
            "        continue",
            "    try:",
            "        orthoscopic = bool(fn('orthoscopic'))",
            "        break",
            "    except Exception:",
            "        continue",
            "",
            "objects = list(cmd.get_object_list(selection))",
            "if not objects:",
            "    raise RuntimeError(f'No exportable objects found in selection: {selection!r}')",
            "",
            "current_state = 1",
            "if state_mode == 'current':",
            "    try:",
            "        current_state = int(cmd.get_state())",
            "    except Exception:",
            "        current_state = 1",
            "",
            "for obj in objects:",
            "    safe = sanitize(obj)",
            "    obj_sel = f'({selection}) and ({obj})' if selection != 'all' else obj",
            "    if state_mode == 'all':",
            "        try:",
            "            n_states = max(1, int(cmd.count_states(obj)))",
            "        except Exception:",
            "            n_states = 1",
            "        for idx in range(1, n_states + 1):",
            "            cmd.save(str(structures / f'{safe}_state_{idx}.{file_format}'), obj_sel, state=idx, format=file_format)",
            "    else:",
            "        state = current_state if state_mode == 'current' else 1",
            "        cmd.save(str(structures / f'{safe}.{file_format}'), obj_sel, state=state, format=file_format)",
            "",
            "bundle = {",
            "    'created_at': datetime.now(timezone.utc).isoformat(),",
            "    'source_pse': Path(payload['session_pse']).name,",
            "    'exported_objects': objects,",
            "    'format': file_format,",
            "    'state_mode': state_mode,",
            "    'selection': selection,",
            "}",
            "",
            "view_data = {",
            "    'pymol_view': view,",
            "    'fov_deg': fov_deg,",
            "    'orthoscopic': orthoscopic,",
            "    'notes': 'Exported via pymol executable fallback (-d).',",
            "}",
            "if viewport is not None:",
            "    view_data['viewport_px'] = viewport",
            "",
            "(out_dir / 'bundle.json').write_text(json.dumps(bundle, indent=2) + '\\n', encoding='utf-8')",
            "(out_dir / 'view.json').write_text(json.dumps(view_data, indent=2) + '\\n', encoding='utf-8')",
            "python end",
            "quit",
        ]
    )


def _export_with_pymol_executable(
    session_pse: Path,
    out_dir: Path,
    state_mode: str,
    selection: str,
    file_format: str,
) -> tuple[BundleMeta, ViewData]:
    pymol_exe = shutil.which("pymol")
    if not pymol_exe:
        raise ExportError(
            "pymol2 is unavailable and no 'pymol' executable was found in PATH. "
            "Install PyMOL or use a Python environment with pymol2."
        )

    payload = {
        "session_pse": str(session_pse),
        "out_dir": str(out_dir.resolve()),
        "state_mode": state_mode,
        "selection": selection,
        "file_format": file_format,
    }
    env = os.environ.copy()
    env["PMBB_PAYLOAD"] = json.dumps(payload)
    command = [pymol_exe, "-cq", str(session_pse), "-d", _fallback_export_command()]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ExportError(
            "Could not run PyMOL fallback executable. Ensure 'pymol' is installed and in PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        raise ExportError(
            "PyMOL fallback export failed.\n"
            f"Command: {' '.join(command)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        ) from exc

    if result.stderr:
        # Keep this visible but non-fatal for PyMOL startup warnings.
        print(result.stderr.strip())

    return read_bundle_meta(out_dir / "bundle.json"), read_view_data(out_dir / "view.json")


def export_pse_to_bundle(
    session_pse: str | Path,
    out_dir: str | Path,
    state_mode: str = "current",
    selection: str = "all",
    file_format: str = "pdb",
) -> tuple[BundleMeta, ViewData]:
    session_path = Path(session_pse).expanduser().resolve()
    output_dir = Path(out_dir).expanduser().resolve()

    if not session_path.exists():
        raise ExportError(f"Session file not found: {session_path}")
    if session_path.suffix.lower() != ".pse":
        raise ExportError(f"Expected a .pse file, got: {session_path}")
    if state_mode not in {"current", "1", "all"}:
        raise ExportError("state_mode must be one of: current, 1, all.")
    if file_format not in {"pdb", "cif"}:
        raise ExportError("file_format must be one of: pdb, cif.")
    if not selection:
        raise ExportError("selection must be a non-empty PyMOL selection string.")

    _prepare_output_dir(output_dir)

    try:
        return _export_with_pymol2(
            session_pse=session_path,
            out_dir=output_dir,
            state_mode=state_mode,
            selection=selection,
            file_format=file_format,
        )
    except ExportError as pymol2_error:
        if "pymol2 is not available" not in str(pymol2_error):
            raise
        return _export_with_pymol_executable(
            session_pse=session_path,
            out_dir=output_dir,
            state_mode=state_mode,
            selection=selection,
            file_format=file_format,
        )
