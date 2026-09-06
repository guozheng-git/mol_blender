# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build-time verification and CCD provisioning, run by Blender."""
import hashlib
import json
import os
from pathlib import Path

import bpy
import biotite
from biotite.structure.info import ccd
import molecularnodes
import numpy

if bpy.app.version[:3] != (5, 0, 1) or numpy.__version__ != "1.26.4" or biotite.__version__ != "1.6.0":
    raise RuntimeError("Runtime version drift")
try:
    ccd.get_ccd()
except FileNotFoundError:
    import biotite.setup_ccd
    biotite.setup_ccd.main()
path = Path(ccd._CCD_FILE)
with path.open("rb") as source:
    digest = hashlib.file_digest(source, "sha256").hexdigest()
expected = os.environ.get("BLENDER_RENDER_CCD_SHA256") or json.loads(Path(__file__).with_name("runtime.lock.json").read_text())["ccd_sha256"]
if digest != expected:
    raise RuntimeError("CCD snapshot changed; update the runtime lock only after ligand fidelity validation.")
print("CCD verified", digest)
