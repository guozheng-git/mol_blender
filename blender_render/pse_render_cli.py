#!/usr/bin/env python3
"""Render directly from a PyMOL .pse file via command line."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blender_render.pymol_bridge_plugin import (
    DEFAULT_BLENDER_EXE,
    SUPPORTED_PRESETS,
    _parse_bool,
    _run_runner,
    _runner_path,
    _write_payload,
    build_render_payload,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pse_render_cli",
        description="Render a .pse session using the Blender bridge without interactive PyMOL UI.",
    )
    parser.add_argument("--pse", required=True, help="Path to .pse file")
    parser.add_argument("--selection", default="(all)")
    parser.add_argument("--state", type=int, default=-1)
    parser.add_argument("--mode", default="local", choices=["local", "interactive", "slurm"])
    parser.add_argument("--preset", default="preset_1_interface", choices=sorted(SUPPORTED_PRESETS))
    parser.add_argument("--style-json", default="")
    parser.add_argument("--rough", type=_parse_bool, default=False)
    parser.add_argument("--color-follow", type=_parse_bool, default=False)
    parser.add_argument("--save-blend", type=_parse_bool, default=False)
    parser.add_argument("--blend-output", default="")
    parser.add_argument("--keep-temp", type=_parse_bool, default=False)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--output-name", default="")
    parser.add_argument("--blender-exe", default=DEFAULT_BLENDER_EXE)
    parser.add_argument("--use-apptainer", type=_parse_bool, default=False)
    parser.add_argument("--apptainer-image", default="")
    parser.add_argument("--interactive-session", default="")
    parser.add_argument("--slurm-partition", default="")
    parser.add_argument("--slurm-account", default="")
    parser.add_argument("--slurm-gres", default="gpu:1")
    parser.add_argument("--slurm-time", default="00:30:00")
    parser.add_argument("--poll", type=_parse_bool, default=True)
    parser.add_argument("--poll-interval", type=int, default=5)
    return parser.parse_args(argv)


def _load_payload_from_pse(
    *,
    pse_path: Path,
    selection: str,
    state: int,
    include_color_hints: bool,
) -> dict[str, Any]:
    try:
        import pymol2  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "pymol2 is unavailable in this Python interpreter. "
            "Run with PyMOL's Python, for example /opt/pymol/bin/python."
        ) from exc

    with pymol2.PyMOL() as pymol_app:
        pymol_app.start()
        cmd = pymol_app.cmd
        cmd.load(str(pse_path))
        return build_render_payload(
            cmd,
            selection=selection,
            state=state,
            include_color_hints=include_color_hints,
        )


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = _parse_args(argv)
    pse_path = Path(args.pse).expanduser().resolve()
    if not pse_path.is_file():
        raise RuntimeError(f"PSE file not found: {pse_path}")

    if args.style_json:
        style_path = Path(args.style_json).expanduser().resolve()
        if not style_path.is_file():
            raise RuntimeError(f"style-json file not found: {style_path}")
    else:
        style_path = None

    payload = _load_payload_from_pse(
        pse_path=pse_path,
        selection=args.selection,
        state=int(args.state),
        include_color_hints=bool(args.color_follow),
    )
    payload_path = _write_payload(payload)

    runner_subcommand = "interactive-submit" if args.mode == "interactive" else args.mode
    command = [
        sys.executable,
        str(_runner_path()),
        runner_subcommand,
        "--payload-json",
        str(payload_path),
        "--blender-exe",
        str(args.blender_exe),
        "--rough",
        "1" if bool(args.rough) else "0",
        "--color-follow",
        "1" if bool(args.color_follow) else "0",
        "--save-blend",
        "1" if bool(args.save_blend) else "0",
        "--keep-temp",
        "1" if bool(args.keep_temp) else "0",
    ]
    if args.blend_output:
        command.extend(["--blend-output", str(Path(args.blend_output).expanduser())])

    if style_path is not None:
        command.extend(["--style-json", str(style_path)])
    else:
        command.extend(["--preset", str(args.preset)])

    if args.output_dir:
        command.extend(["--output-dir", str(Path(args.output_dir).expanduser())])
    if args.output_name:
        command.extend(["--output-name", str(args.output_name)])
    if bool(args.use_apptainer):
        command.extend(["--use-apptainer", "1"])
        if args.apptainer_image:
            command.extend(["--apptainer-image", str(Path(args.apptainer_image).expanduser())])

    if args.mode == "interactive":
        session_id = str(args.interactive_session).strip()
        if not session_id:
            raise RuntimeError("--interactive-session is required when --mode interactive.")
        command.extend(["--session-id", session_id])
        command.extend(["--poll", "1" if bool(args.poll) else "0"])
        command.extend(["--poll-interval", str(max(1, int(args.poll_interval)))])

    if args.mode == "slurm":
        command.extend(["--poll", "1" if bool(args.poll) else "0"])
        command.extend(["--poll-interval", str(max(1, int(args.poll_interval)))])
        command.extend(["--slurm-gres", str(args.slurm_gres)])
        command.extend(["--slurm-time", str(args.slurm_time)])
        if args.slurm_partition:
            command.extend(["--slurm-partition", str(args.slurm_partition)])
        if args.slurm_account:
            command.extend(["--slurm-account", str(args.slurm_account)])

    return _run_runner(command)


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(argv)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
