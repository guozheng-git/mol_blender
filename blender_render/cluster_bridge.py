"""Stdlib-only PyMOL adapter to the shared job API in controller Python."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from .config import load_config
from .local_bridge import controller_environment


def command(arguments, *, group='cluster'):
    """Run `blender-render cluster|job …` in the controller Python; one JSON object comes back.

    `cluster` status/cancel/fetch also accept bare job IDs; `job` works from receipts.
    """
    python = load_config().get('local_python', sys.executable)
    result = subprocess.run([python, '-m', 'blender_render.cli', group, *arguments],
                            env=controller_environment(), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return json.loads(result.stdout)


def submit(payload, *, options, output, profile, wait=False, timeout=600):
    """Submit to the lab, connecting once if needed; with wait, block until fetched.

    The PyMOL session may submit again soon, so a tunnel this opens stays open.
    """
    with tempfile.TemporaryDirectory(prefix='blender-cluster-export-') as temporary:
        root = Path(temporary)
        (root / 'bundle.json').write_text(json.dumps(payload, allow_nan=False))
        (root / 'options.json').write_text(json.dumps(options, allow_nan=False))
        args = ['submit', str(root / 'bundle.json'), '--options-json', str(root / 'options.json'),
                '--backend', 'lab', '--profile', profile, '--output', str(output), '--keep-connected']
        if wait:
            args += ['--wait', str(timeout)]
        return command(args, group='job')
