"""Measure PyMOL camera keys without changing the caller's scene."""
from __future__ import annotations

import numpy as np

from .animation import Keys, View


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
