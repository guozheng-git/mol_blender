"""Capture with this checkout's stdlib exporter without importing its services."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def capture_pse(cmd) -> bytes:
    """Freeze the full session while the caller holds pymol_lock.

    Use PyMOL's own PSE serializer without cmd.save's session_file side effect.
    Exclude disposable rendering caches; retain all objects, states and views.
    The caller writes the frozen bytes after releasing the PyMOL lock.
    """
    from chempy.pkl import cPickle

    session = cmd.get_session(names="", partial=0, compress=0, cache=0)
    return cPickle.dumps(session, 1)


@contextmanager
def open_session(path: Path) -> Iterator:
    """Yield the cmd of a private headless PyMOL holding a saved session.

    PSE files are pickles: only open sessions the user trusts. The instance is
    separate from any GUI PyMOL, so callers need not hold pymol_lock.
    """
    import pymol2

    with pymol2.PyMOL() as instance:
        instance.cmd.load(str(path))
        yield instance.cmd


def capture_scene(
    cmd, selection: str, state: int, *, all_states: bool = False
) -> dict:
    """Caller holds pymol_lock. Preserve DSS assignments and the live camera.

    all_states adds every state's coordinates, for state animations.
    """
    path = Path(__file__).resolve().parents[3] / "blender_render/export.py"
    spec = importlib.util.spec_from_file_location("_co_pymol_blender_export", path)
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
