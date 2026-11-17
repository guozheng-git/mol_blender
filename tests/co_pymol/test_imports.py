"""Shared scene helpers must remain usable outside the MCP/PyMOL host."""
import subprocess
import sys
from pathlib import Path

import co_pymol
import pytest


@pytest.mark.parametrize("native_preview", [False, True])
def test_scene_imports_do_not_load_host_or_renderer_services(native_preview, tmp_path):
    # A fresh process catches accidental dependencies hidden by the test host.
    script = '''
import sys
sys.path.insert(0, sys.argv[1])
from co_pymol.scene import animation, capture, session
from co_pymol.render import client
assert 'numpy' not in sys.modules
if sys.argv[2] == 'True':
    from co_pymol.scene import camera, preview, triage
for name in sys.modules:
    assert name.split('.')[0] not in {'blender_render', 'pymol', 'pymol2', 'mcp', 'uvicorn', 'yaml'}, name
    assert name not in {'co_pymol.cli', 'co_pymol.server', 'co_pymol.proxy'}, name
'''
    package_root = Path(co_pymol.__file__).resolve().parent.parent
    subprocess.run([sys.executable, '-I', '-c', script, str(package_root), str(native_preview)],
                   cwd=tmp_path, check=True, capture_output=True, text=True)
