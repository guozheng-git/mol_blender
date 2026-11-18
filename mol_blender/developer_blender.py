# SPDX-License-Identifier: GPL-3.0-or-later
"""Fresh-preview entrypoint: verify source/runtime, then call the shared renderer."""
import json
import os
import sys
from pathlib import Path

package = Path(__file__).resolve().parent
sys.path[:0] = [str(package), str(package.parent)]
from mol_blender.blender_runtime import SnapshotRuntime
from mol_blender import blender_custom_render as render

cfg = json.loads(Path(os.environ['MOL_BLENDER_DEV_CONFIG']).read_text())
runtime = SnapshotRuntime(cfg)
try:
    runtime.render(render._parse_argv(sys.argv))
finally:
    runtime.close()
