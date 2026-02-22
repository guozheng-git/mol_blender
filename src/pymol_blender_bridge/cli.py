"""Command line interface for pymol_blender_bridge."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .blender_script import render_import_script
from .bundle_schema import read_bundle_meta, read_view_data
from .export_pse import ExportError, export_pse_to_bundle
from .view_math import compute_blender_camera_from_pymol_view


class CliError(RuntimeError):
    """Raised when CLI invocation cannot be completed."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pymol_blender_bridge",
        description="Export PyMOL sessions and build Blender scenes via Molecular Nodes.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export a .pse session into a bridge bundle.")
    export_parser.add_argument("session_pse", help="Input PyMOL .pse session file.")
    export_parser.add_argument("--out", required=True, help="Output bundle directory.")
    export_parser.add_argument(
        "--state",
        default="current",
        choices=["current", "1", "all"],
        help="State export mode. 'all' exports separate files per state.",
    )
    export_parser.add_argument(
        "--selection",
        default="all",
        help="Optional PyMOL selection to export (default: all).",
    )
    export_parser.add_argument(
        "--format",
        default="pdb",
        choices=["pdb", "cif"],
        help="Structure export format (default: pdb).",
    )

    blend_parser = subparsers.add_parser(
        "build-blend", help="Build a Blender .blend scene from an exported bridge bundle."
    )
    blend_parser.add_argument("bundle_dir", help="Bundle directory produced by 'export'.")
    blend_parser.add_argument("--blender", required=True, help="Path or command name for Blender executable.")
    blend_parser.add_argument("--out", required=True, help="Output .blend file path.")
    blend_parser.add_argument(
        "--background",
        dest="background",
        action="store_true",
        default=True,
        help="Run Blender in background mode (default).",
    )
    blend_parser.add_argument(
        "--no-background",
        dest="background",
        action="store_false",
        help="Run Blender with UI (debugging only).",
    )
    return parser


def _resolve_blender_executable(blender_arg: str) -> str:
    blender_path = Path(blender_arg).expanduser()
    if blender_path.exists():
        if not blender_path.is_file():
            raise CliError(f"Blender path is not a file: {blender_path}")
        if not blender_path.stat().st_mode & 0o111:
            raise CliError(f"Blender executable is not executable: {blender_path}")
        return str(blender_path.resolve())

    resolved = shutil.which(blender_arg)
    if resolved:
        return resolved
    raise CliError(
        f"Could not find Blender executable '{blender_arg}'. "
        "Provide a full path via --blender or add Blender to PATH."
    )


def _run_export(args: argparse.Namespace) -> int:
    try:
        bundle_meta, view_data = export_pse_to_bundle(
            session_pse=args.session_pse,
            out_dir=args.out,
            state_mode=args.state,
            selection=args.selection,
            file_format=args.format,
        )
    except ExportError as exc:
        raise CliError(str(exc)) from exc

    out_dir = Path(args.out).expanduser().resolve()
    print(f"Exported bundle to: {out_dir}")
    print(f"Objects: {', '.join(bundle_meta.exported_objects)}")
    print(f"FOV: {view_data.fov_deg!r}  Orthoscopic: {view_data.orthoscopic!r}")
    return 0


def _run_build_blend(args: argparse.Namespace) -> int:
    bundle_dir = Path(args.bundle_dir).expanduser().resolve()
    if not bundle_dir.exists():
        raise CliError(f"Bundle directory not found: {bundle_dir}")
    bundle_path = bundle_dir / "bundle.json"
    view_path = bundle_dir / "view.json"
    if not bundle_path.exists():
        raise CliError(f"Missing bundle metadata: {bundle_path}")
    if not view_path.exists():
        raise CliError(f"Missing view metadata: {view_path}")

    try:
        _bundle_meta = read_bundle_meta(bundle_path)
        view_data = read_view_data(view_path)
        camera_spec = compute_blender_camera_from_pymol_view(
            pymol_view=view_data.pymol_view,
            fov_deg=view_data.fov_deg,
            orthoscopic=view_data.orthoscopic,
        )
        script_text = render_import_script(camera_spec)
    except Exception as exc:
        raise CliError(f"Failed to read/validate bundle metadata: {exc}") from exc

    script_path = bundle_dir / "generated_build_scene.py"
    try:
        script_path.write_text(script_text, encoding="utf-8")
    except OSError as exc:
        raise CliError(f"Could not write Blender script at {script_path}: {exc}") from exc

    blender_exe = _resolve_blender_executable(args.blender)
    out_blend = Path(args.out).expanduser().resolve()

    command = [blender_exe]
    if args.background:
        command.append("--background")
    command.extend(["--python", str(script_path), "--", str(bundle_dir), str(out_blend)])

    try:
        result = subprocess.run(command, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        raise CliError(
            "Blender build failed.\n"
            f"Command: {' '.join(command)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        ) from exc

    if not out_blend.exists():
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        raise CliError(
            "Blender reported completion but output .blend file was not created. "
            "This usually means the Blender script raised an error while Blender still exited 0.\n"
            f"Expected output: {out_blend}\n"
            f"Command: {' '.join(command)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )

    print(f"Generated Blender script: {script_path}")
    print(f"Saved Blender scene: {out_blend}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            return _run_export(args)
        if args.command == "build-blend":
            return _run_build_blend(args)
        raise CliError(f"Unknown command: {args.command}")
    except CliError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
