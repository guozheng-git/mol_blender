"""Compatibility adapter for the previous --pse command-line interface."""
from __future__ import annotations
import argparse
from .cli import main

def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pse", required=True)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--output-name", default="render.png")
    parser.add_argument("--mode", default="local")
    parser.add_argument("--preset", default="preset_1_interface")
    parser.add_argument("--style-json")
    parser.add_argument("--rough", default="0")
    parser.add_argument("--save-blend", default="0")
    parser.add_argument("--color-follow", default="0")
    parser.add_argument("--selection", default="(all)")
    args = parser.parse_args(argv)
    from pathlib import Path
    from .export import _parse_bool
    command = ["render", args.pse, "--pymol-python", __import__("sys").executable, "--profile", args.mode, "--preset", args.preset, "--selection", args.selection, "-o", str(Path(args.output_dir) / args.output_name)]
    for name in ("rough", "save_blend", "color_follow"):
        if _parse_bool(getattr(args, name)):
            command.append("--" + name.replace("_", "-"))
    if args.style_json:
        command += ["--style-json", args.style_json]
    return main(command)

if __name__ == "__main__":
    raise SystemExit(run())
