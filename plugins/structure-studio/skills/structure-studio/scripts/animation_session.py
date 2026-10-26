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
import numpy as np
import pymol2
from animation_recipes import Keys, View, frames_for
from pymol_session import ligand_records, residue_selection


def _rotation(view: View) -> np.ndarray:
    return np.array(view[:9]).reshape(3, 3).T


def _framed(cmd, view: View, selection: str) -> View:
    """Zoom on selection at view's orientation, without clipping the rest."""
    cmd.set_view(view)
    cmd.zoom(selection, 1, complete=1)
    framed = list(cmd.get_view())
    distance = -framed[11]
    extent = np.ptp(cmd.get_coords("enabled"), axis=0).max()
    framed[15], framed[16] = max(1.0, distance - extent), distance + extent
    return tuple(framed)


def pocket(focus: str) -> str:
    return f"({focus}) or byres (enabled and polymer within 4 of ({focus}))"


def opening_view(cmd, view: View, focus: str) -> View:
    """Look down into the pocket: from the surrounding bulk toward the focus."""
    centre = cmd.get_coords(focus).mean(axis=0)
    shell = cmd.get_coords(f"enabled and polymer within 12 of ({focus})")
    rotation = _rotation(view)
    if shell is not None:
        outward = centre - shell.mean(axis=0)
        if np.linalg.norm(outward) > 0.5:
            z = outward / np.linalg.norm(outward)
            up = rotation[1] - np.dot(rotation[1], z) * z
            if np.linalg.norm(up) < 1e-3:  # looking straight up/down already
                up = rotation[0] - np.dot(rotation[0], z) * z
            y = up / np.linalg.norm(up)
            rotation = np.array([np.cross(y, z), y, z])
    oriented = tuple(rotation.T.flatten()) + tuple(view[9:])
    return _framed(cmd, oriented, pocket(focus))


def keys_for(cmd, recipe: str, focus: str | None) -> Keys:
    overview = tuple(cmd.get_view())
    scenes = []
    for name in cmd.get_scene_list() if recipe == "scenes" else ():
        cmd.scene(name, "recall", animate=0, color=0, active=0, rep=0, frame=0)
        scenes.append(tuple(cmd.get_view()))
    target = None
    if focus and recipe == "reveal":
        target = opening_view(cmd, overview, focus)
    elif focus and recipe == "cutaway":
        # Push straight in: the fade, not a turn, opens the way to the site.
        target = _framed(cmd, overview, pocket(focus))
    cmd.set_view(overview)
    return Keys(
        overview=overview,
        focus=target,
        scenes=tuple(scenes),
        states=cmd.count_states("enabled"),
    )



def prepare(cmd, config, *, width=None, height=None):
    from blender_render.export import build_render_payload
    if any(value is not None and (type(value) is not int or value < 1) for value in (width, height)):
        raise ValueError("Animation dimensions must be positive integers")
    recipe = config["recipe"]
    focus = config.get("focus")
    if recipe in {"reveal", "cutaway"}:
        if not focus:
            ligands = [l for l in ligand_records(cmd, "enabled") if not l["additive"]]
            if not ligands:
                raise ValueError("This recipe needs --focus or an enabled non-additive ligand")
            focus = residue_selection("enabled", ligands[0])
        focus = f"enabled and ({focus})"
        if not cmd.count_atoms(focus):
            raise ValueError("The animation focus matches no enabled atoms")
    keys = keys_for(cmd, recipe, focus)
    frames = frames_for(recipe, keys, config["fps"], duration=config.get("duration"),
                        rock_angle=config.get("rock_angle"))
    animation = {"mode": "path", "path": [[float(v) for v in f.view] for f in frames],
                 "fps": config["fps"], "gif": config["gif"]}
    anchor = 0
    if recipe == "cutaway":
        lid = [round(f.lid_opacity, 6) for f in frames]
        anchor = next(i for i, opacity in enumerate(lid) if opacity == 0)
        animation.update(lid=lid, anchor=anchor)
    if recipe == "states":
        animation["states"] = [f.state for f in frames]
        if set(animation["states"]) != set(range(1, keys.states + 1)):
            raise ValueError("The movie is too short to show every state; increase duration or FPS")
    cmd.set_view(frames[anchor].view)
    if frames[anchor].state:
        # A saved object-level state overrides the global state in PyMOL.
        for name in cmd.get_object_list("enabled"):
            cmd.set("state", frames[anchor].state, name)
    payload = build_render_payload(cmd, include_color_hints=True, all_states=recipe == "states")
    viewport = cmd.get_viewport()
    width = width or (round(height * viewport[0] / viewport[1]) if height else 640)
    height = height or max(1, round(width * viewport[1] / viewport[0]))
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
