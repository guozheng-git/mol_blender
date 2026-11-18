"""Stdlib-only file helpers shared by PyMOL and its detached worker."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

# The lab receipt mol_blender.jobs writes for a request's render.png
# (jobs.receipt_for); PyMOL's Python cannot import the renderer to ask it.
RECEIPT = "render.cluster.json"


def write_json(path: Path, value: dict) -> None:
    """Replace a receipt atomically, keeping intermediate bytes private."""
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
