"""Stdlib-only bridge from PyMOL's embedded Python into the installed CLI."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from .presets import DEFAULT_PRESET
from .config import load_config


def controller_environment():
    """Launch the configured Python independently of PyMOL's embedded runtime."""
    env = os.environ.copy()
    # macOS PyMOL/Conda launchers set PYTHONEXECUTABLE. CPython uses it to
    # locate its environment even when we invoke an explicit venv interpreter.
    # Keep PyMOL's own settings intact; sanitize only the child process.
    for name in ('PYTHONHOME', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
        env.pop(name, None)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
    return env


def render_payload(payload, *, output, options, blender='', timeout=600, keep_temp=False, warm=False):
    python = load_config().get('local_python', sys.executable)
    with tempfile.TemporaryDirectory(prefix='pymol-blender-') as temporary:
        root = Path(temporary)
        (root / 'bundle.json').write_text(json.dumps(payload, allow_nan=False))
        command = [python, '-m', 'blender_render.cli', 'render', str(root / 'bundle.json'),
                   '-o', str(output), '--timeout', str(timeout)]
        for key, flag in [('style_doc', '--style-json'), ('overrides', '--overrides-json')]:
            if key in options:
                path = root / (key + '.json')
                path.write_text(json.dumps(options[key], allow_nan=False))
                command.extend([flag, str(path)])
        if 'style_doc' not in options:
            command.extend(['--preset', options.get('preset', DEFAULT_PRESET)])
        for key in ('rough', 'color_follow', 'save_blend', 'preserve_view', 'transparent_background', 'gif'):
            if key in options:
                flag = 'transparent' if key == 'transparent_background' else key.replace('_', '-')
                command.append('--' + ('' if options[key] else 'no-') + flag)
        for key in ('width', 'height', 'samples', 'animation', 'duration', 'fps', 'rock_angle'):
            if key in options:
                command.extend(['--'+key.replace('_', '-'), str(options[key])])
        if blender:
            command.extend(['--blender', blender])
        if keep_temp:
            command.append('--keep-temp')
        if warm:
            command.append('--warm')
        result = subprocess.run(command, env=controller_environment(), capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('Local render failed. Run blender-render setup in the render environment.\n' + result.stderr[-4000:])
        return json.loads(result.stdout)
