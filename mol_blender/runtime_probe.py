# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Executed by Blender, including extension installs without dist-info metadata."""
import importlib.metadata
import json
import sys
from pathlib import Path

import bpy
import biotite
import molecularnodes
import numpy
import jsonschema

try:
    version = importlib.metadata.version("molecularnodes")
except importlib.metadata.PackageNotFoundError:
    import tomllib
    version = tomllib.loads(Path(molecularnodes.__file__).with_name("blender_manifest.toml").read_text())["version"]

print("MOL_BLENDER_RUNTIME=" + json.dumps({"blender": bpy.app.version_string,
    "python": ".".join(map(str, sys.version_info[:2])), "molecularnodes": version,
    "numpy": numpy.__version__, "biotite": biotite.__version__}))
