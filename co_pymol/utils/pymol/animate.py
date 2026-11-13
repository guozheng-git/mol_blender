"""Measure a session for animation recipes and render quick PyMOL previews.

Runs against a throwaway headless session (see blender.open_session): the
cutaway recipe adds objects and representations without saving them back.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from co_pymol.core.animation import Frame, Keys, View
from co_pymol.defaults import ANIMATION

LIGANDS = "enabled and organic and not solvent"
SHELL_FLAG = 8  # a user flag PyMOL leaves free; marks the lid of the cutaway
SHELL_OBJECT = "cutaway_lid"


def scene_facts(cmd) -> dict:
    """What a recipe choice depends on: content, states, scenes, ligands."""
    residues: dict[tuple, dict] = {}
    cmd.iterate(
        LIGANDS,
        "_ = rows.setdefault((model, chain, resn, resi), index)",
        space={"rows": residues},
    )
    ligands = []
    for (model, chain, resn, resi), index in residues.items():
        selection = f"byres ({model} and index {index})"
        ligands.append(
            {
                "name": f"{resn} {chain}{resi}".strip(),
                "object": model,
                "atoms": cmd.count_atoms(selection),
                "selection": selection,
            }
        )
    ligands.sort(key=lambda ligand: -ligand["atoms"])
    return {
        "objects": cmd.get_names("objects", enabled_only=1),
        "chains": cmd.get_chains("enabled and polymer"),
        "polymer_atoms": cmd.count_atoms("enabled and polymer"),
        "ligands": ligands,
        "states": cmd.count_states("enabled"),
        "scenes": cmd.get_scene_list(),
        "viewport": list(cmd.get_viewport()),
    }


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
    # The coordinate extent is a NumPy scalar. Keep camera views plain Python
    # numbers so Reveal/Cutaway requests survive strict JSON serialization.
    return tuple(float(value) for value in framed)


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
    try:
        for name in cmd.get_scene_list() if recipe == "scenes" else ():
            cmd.scene(name, "recall", animate=0, color=0, active=0, rep=0, frame=0)
            scenes.append(tuple(cmd.get_view()))
        target = None
        if focus and recipe in {"reveal", "cutaway"}:
            focus = f"enabled and ({focus})"
            if not cmd.count_atoms(focus):
                raise ValueError("The animation focus matches no enabled atoms")
            if recipe == "reveal":
                target = opening_view(cmd, overview, focus)
            else:
                # Push straight in: the fade opens the way to the site.
                target = _framed(cmd, overview, pocket(focus))
    finally:
        cmd.set_view(overview)
    return Keys(
        overview=overview,
        focus=target,
        scenes=tuple(scenes),
        states=cmd.count_states("enabled"),
    )


def build_cutaway(cmd, focus: str, view: View) -> str:
    """Surface the polymer, splitting off the lid between camera and focus.

    Both surfaces are computed over every polymer atom, so the lid fits the
    hole it leaves exactly. Returns the lid object, whose transparency fades.
    """
    rotation, origin = _rotation(view), np.array(view[12:15])
    site = (cmd.get_coords(focus) - origin) @ rotation.T
    centre = site.mean(axis=0)
    reach = np.linalg.norm(site[:, :2] - centre[:2], axis=1).max() + 6
    rows: list[tuple] = []
    cmd.iterate_state(
        -1,
        "enabled and polymer",
        "rows.append((model, index, x, y, z))",
        space={"rows": rows},
    )
    lid: dict[str, list[int]] = {}
    for model, index, *xyz in rows:
        x, y, z = (np.array(xyz) - origin) @ rotation.T
        if z > centre[2] and np.hypot(x - centre[0], y - centre[1]) < reach:
            lid.setdefault(model, []).append(index)
    for model, indices in lid.items():
        for start in range(0, len(indices), 500):
            chunk = "+".join(map(str, indices[start : start + 500]))
            cmd.flag(SHELL_FLAG, f"byres ({model} and index {chunk})", "set")
    cmd.create(SHELL_OBJECT, "enabled and polymer")
    cmd.hide("everything", SHELL_OBJECT)
    cmd.show("surface", f"{SHELL_OBJECT} and flag {SHELL_FLAG}")
    cmd.show(
        "surface",
        f"enabled and polymer and not {SHELL_OBJECT} and not flag {SHELL_FLAG}",
    )
    cmd.show(
        "sticks",
        f"({focus}) or (byres (polymer and not {SHELL_OBJECT} within 5 of "
        f"({focus})) and not name N+C+O)",
    )
    cmd.set("transparency", 0)
    return SHELL_OBJECT


def render_preview(
    cmd,
    frames: list[Frame],
    output: Path,
    *,
    fps: int,
    width: int,
    height: int,
    shell: str | None = None,
) -> Path:
    """Ray-trace every frame at preview quality and encode an H.264 MP4."""
    from co_pymol.utils.pymol.blender import set_animation_frame

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise OSError("ffmpeg is required for animation previews (brew install ffmpeg)")
    width, height = width // 2 * 2, height // 2 * 2  # H.264 needs even sizes
    cmd.viewport(width, height)
    cmd.set("ray_shadows", 0)
    with tempfile.TemporaryDirectory(prefix="co-pymol-frames-") as directory:
        for number, frame in enumerate(frames):
            set_animation_frame(cmd, frame)
            if shell:
                # PyMOL previews retain a ghost of the shell. The shared
                # timeline records full lid opacity for Blender's cutaway.
                transparency = (1 - frame.lid_opacity) * ANIMATION["recipes"]["cutaway"]["pymol_shell_transparency"]
                cmd.set("transparency", transparency, shell)
            cmd.png(
                f"{directory}/frame_{number:04d}.png",
                width=width,
                height=height,
                ray=1,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                str(fps),
                "-i",
                f"{directory}/frame_%04d.png",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "20",
                "-movflags",
                "+faststart",
                str(output),
            ],
            check=True,
        )
    return output
