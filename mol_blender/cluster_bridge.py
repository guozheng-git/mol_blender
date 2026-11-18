"""Stdlib-only PyMOL adapter to the shared job API in controller Python."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .config import cluster_root, load_config
from .local_bridge import controller_environment


def command(arguments, *, group='cluster'):
    """Run `mol-blender cluster|job …` in the controller Python; one JSON object comes back.

    `cluster` status/cancel/fetch also accept bare job IDs; `job` works from receipts.
    """
    python = load_config().get('local_python', sys.executable)
    result = subprocess.run([python, '-m', 'mol_blender.cli', group, *arguments],
                            env=controller_environment(), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-4000:])
    return json.loads(result.stdout)


def submit(payload, *, options, output, profile, wait=False, timeout=600, fetch_output=None):
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
            if fetch_output is not None:
                args += ['--fetch-output', str(fetch_output)]
        return command(args, group='job')


@contextmanager
def preview_output(profile):
    """Select one recoverable unnamed preview per profile, across PyMOL restarts.

    The active symlink points at the output whose existing .cluster.json receipt
    owns the request identity. It is not a second job record. Keep the pointer
    until presentation succeeds or the gateway confirms a terminal failure.
    """
    key = hashlib.sha256(profile.encode()).hexdigest()
    root = (cluster_root() / 'pymol-previews' / key).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / '.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A preview for this cluster profile is already active in another command.') from None
        active = root / 'active'
        if active.is_symlink():
            target = os.readlink(active)
            if Path(target).name != target or not target.startswith('preview-'):
                raise ValueError('Invalid cluster preview pointer: ' + str(active))
            directory = root / target
            if directory.is_symlink():
                raise ValueError('Cluster preview directory must not be a symlink')
            if not directory.exists():
                raise ValueError('Cluster preview files are missing; inspect the saved preview before submitting again: '
                                 + str(directory))
        elif active.exists():
            raise ValueError('Invalid cluster preview pointer: ' + str(active))
        else:
            directory = Path(tempfile.mkdtemp(prefix='preview-', dir=root))
            active.symlink_to(directory.name, target_is_directory=True)
        yield directory / 'render.png'


def finish_preview(output, *, keep_files=False):
    """Release the active pointer while preview_output's lock is held."""
    directory = Path(output).parent
    active = directory.parent / 'active'
    if not active.is_symlink() or active.resolve() != directory.resolve():
        raise ValueError('Cluster preview identity changed; keeping its files')
    active.unlink()
    if not keep_files:
        shutil.rmtree(directory)
