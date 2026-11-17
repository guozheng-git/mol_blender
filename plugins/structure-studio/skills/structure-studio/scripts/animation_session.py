# SPDX-License-Identifier: MIT
# Adapted from co-pymol; Copyright (c) 2026 Soo-Jeong Kim.
"""Measure and export a trusted session for the shared Blender animation renderer."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import pymol2
from render_defaults import ANIMATION


def prepare(cmd, config, *, width=None, height=None):
    from co_pymol.scene.animation import fit_viewport, frames_for, template
    from co_pymol.scene.camera import keys_for
    from co_pymol.scene.capture import capture_animation

    config = template(config)
    if any(value is not None and (type(value) is not int or value < 1) for value in (width, height)):
        raise ValueError("Animation dimensions must be positive integers")
    recipe = config["recipe"]
    focus = config.get("focus")
    if recipe in {"reveal", "cutaway"}:
        if not focus:
            from co_pymol.scene.ligands import default_focus
            focus = default_focus(cmd, "enabled")  # The same choice as `co-pymol animate`.
            if not focus:
                raise ValueError("This recipe needs --focus or an enabled non-additive ligand")
        focus = f"enabled and ({focus})"
        if not cmd.count_atoms(focus):
            raise ValueError("The animation focus matches no enabled atoms")
    keys = keys_for(cmd, recipe, focus)
    frames = frames_for(recipe, keys, config["fps"], duration=config.get("duration"),
                        rock_angle=config.get("rock_angle"))
    payload, animation = capture_animation(cmd, recipe, frames, config["fps"], gif=config["gif"])
    anchor = animation.get("anchor", 0)
    if width is None and height is None:
        width, height = ANIMATION["preview"]["width"], ANIMATION["preview"]["height"]
    width, height = fit_viewport(cmd.get_viewport(), width, height)
    output = {"width": width, "height": height, "animation": animation}
    from blender_render.animation import normalize
    animation = output["animation"] = normalize(animation, output)
    summary = {"recipe": recipe, "fps": config["fps"], "frames": len(frames),
               "duration": len(frames) / config["fps"], "anchor": anchor,
               "width": width, "height": height, "focus": focus,
               "template": config}
    return payload, {"output": output}, summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("session")
    p.add_argument("--wheel", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--payload", required=True)
    p.add_argument("--overrides", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    args = p.parse_args()
    # Read the exporter from the same bundled wheel as the renderer, without
    # requiring a second renderer installation inside PyMOL's environment.
    sys.path.insert(0, args.wheel)
    config = json.loads(Path(args.config).read_text())
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        cmd.load(str(Path(args.session).resolve()))
        if not cmd.count_atoms("enabled"):
            raise ValueError("The session has no enabled atoms")
        payload, overrides, summary = prepare(cmd, config, width=args.width, height=args.height)
        payload.setdefault("provenance", {}).update(
            filename=Path(args.session).name,
            sha256=hashlib.sha256(Path(args.session).read_bytes()).hexdigest())
    for path, data in ((args.payload, payload), (args.overrides, overrides), (args.report, summary)):
        Path(path).write_text(json.dumps(data, allow_nan=False))


if __name__ == "__main__":
    # PyMOL feedback belongs on stderr, not the JSON command channel.
    os.dup2(2, 1)
    main()
