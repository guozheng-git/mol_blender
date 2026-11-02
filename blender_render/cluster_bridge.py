"""Stdlib-only PyMOL adapter to the optional cluster client in controller Python."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from .config import load_config
from .local_bridge import controller_environment


def command(arguments):
    python = load_config().get('local_python', sys.executable)
    result = subprocess.run([python, '-m', 'blender_render.cli', 'cluster', *arguments],
                            env=controller_environment(), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return json.loads(result.stdout)


def submit(payload, *, options, output, profile, wait=False, timeout=600):
    with tempfile.TemporaryDirectory(prefix='blender-cluster-export-') as temporary:
        root = Path(temporary)
        (root / 'bundle.json').write_text(json.dumps(payload, allow_nan=False))
        (root / 'options.json').write_text(json.dumps(options, allow_nan=False))
        args = ['submit', str(root / 'bundle.json'), '--options-json', str(root / 'options.json'),
                '--profile', profile, '--output', str(output), '--timeout', str(timeout)]
        if wait:
            args.append('--wait')
        return command(args)
