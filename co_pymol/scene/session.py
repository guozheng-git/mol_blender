"""PyMOL access and the shared thread lock.

`pymol_session` is the entry point tools use — `with pymol_session() as cmd:`
resolves `cmd` and holds the lock for the block. `ensure_pymol` and `pymol_lock`
are the primitives it's built from.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import threading

pymol_lock = threading.Lock()


def ensure_pymol():
    """Import pymol.cmd, raising a clear error if unavailable."""
    try:
        from pymol import cmd

        return cmd
    except ImportError as err:
        raise RuntimeError(
            "PyMOL is not installed. Install it with: "
            "/Applications/PyMOL.app/Contents/bin/python -m pip install -e ."
        ) from err


class pymol_session:
    """Acquire `pymol_lock` and yield `cmd` for the duration of the block.

    Folds the usual `cmd = ensure_pymol()` + `with pymol_lock:` pair into one:

        with pymol_session() as cmd:
            cmd.some_operation(arg)

    `cmd` is resolved once and cached on the class, so `ensure_pymol` only runs
    its import check the first time.
    """

    _cmd = None

    def __enter__(self):
        if pymol_session._cmd is None:
            pymol_session._cmd = ensure_pymol()
        pymol_lock.acquire()
        return pymol_session._cmd

    def __exit__(self, exc_type, exc, tb):
        pymol_lock.release()
        return False


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


LIGANDS = "enabled and organic and not solvent"


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
