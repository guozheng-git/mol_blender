"""Capture with this checkout's stdlib exporter without importing its services."""

from __future__ import annotations

import importlib.util
from importlib.machinery import PathFinder
from pathlib import Path

from co_pymol.defaults import ANIMATION


def capture_pse(cmd) -> bytes:
    """Freeze the full session while the caller holds pymol_lock.

    Use PyMOL's own PSE serializer without cmd.save's session_file side effect.
    Exclude disposable rendering caches; retain all objects, states and views.
    The caller writes the frozen bytes after releasing the PyMOL lock.
    """
    from chempy.pkl import cPickle

    session = cmd.get_session(names="", partial=0, compress=0, cache=0)
    return cPickle.dumps(session, 1)


def capture_scene(
    cmd, selection: str, state: int, *, all_states: bool = False
) -> dict:
    """Caller holds pymol_lock. Preserve DSS assignments and the live camera.

    all_states adds every state's coordinates, for state animations.
    """
    path = Path(__file__).parents[2] / "blender_render/scene"
    # PathFinder supports both installed files and Studio's bundled wheel.
    # Load the standalone exporter without importing the renderer package.
    spec = PathFinder.find_spec("export", [str(path)])
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the Blender Render scene exporter")
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    previous_ss = {}
    cmd.iterate(
        "all",
        "previous_ss[(model, index)] = ss",
        space={
            "previous_ss": previous_ss,
        },
    )
    view = cmd.get_view()
    try:
        return exporter.build_render_payload(
            cmd,
            selection,
            state,
            include_color_hints=True,
            enabled_only=True,
            all_states=all_states,
        )
    finally:
        # The upstream exporter runs DSS. Its computed hints belong to the
        # frozen export, not the user's manually annotated live scene.
        cmd.alter(
            "all",
            "ss = previous_ss.get((model, index), ss)",
            space={
                "previous_ss": previous_ss,
            },
        )
        cmd.set_view(view)


def set_animation_frame(cmd, frame) -> None:
    """Apply a compiled frame, including objects pinned to a saved state."""
    cmd.set_view(frame.view)
    if frame.state:
        cmd.set("state", frame.state)
        for name in cmd.get_object_list("enabled"):
            cmd.set("state", frame.state, name)


def capture_animation(cmd, recipe: str, frames, fps: int, *, gif: bool = ANIMATION["gif"]) -> tuple[dict, dict]:
    """Export at the recipe's anchor while preserving the caller's view and states."""
    from co_pymol.scene.animation import animation_payload

    animation = animation_payload(recipe, frames, fps, gif=gif)
    anchor = frames[animation.get("anchor", 0)]
    view = cmd.get_view()
    state = cmd.get("state")
    object_states = {}
    if anchor.state:
        from pymol.setting import index_dict

        # get("state", name) resolves inherited settings. Restoring that value
        # would accidentally pin an unpinned object to the old global state.
        for name in cmd.get_object_list("enabled"):
            settings = {index: value for index, _, value in cmd.get_object_settings(name) or []}
            object_states[name] = settings.get(index_dict["state"])
    try:
        set_animation_frame(cmd, anchor)
        payload = capture_scene(cmd, "all", -1, all_states=recipe == "states")
        return payload, animation
    finally:
        if anchor.state:
            cmd.set("state", state)
            for name, value in object_states.items():
                if value is None:
                    cmd.unset("state", name)
                else:
                    cmd.set("state", value, name)
        cmd.set_view(view)
