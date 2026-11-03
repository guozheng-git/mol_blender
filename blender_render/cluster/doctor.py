"""Gateway preflight: configuration, image, container and scheduler checks without rendering."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

# Provisional floor. The job flags (--containall, --no-mount hostfs,bind-paths,cwd,
# --cleanenv) passed real acceptance on a newer Apptainer; the exact oldest
# compatible release has not been established on real hardware.
MINIMUM_APPTAINER = (1, 1)


def apptainer_version(output):
    """Parse `apptainer --version` / `singularity --version` output."""
    match = re.match(r'\s*(apptainer|singularity-ce|singularity)\s+version\s+v?(\d+)\.(\d+)(?:\.(\d+))?',
                     output or '', re.IGNORECASE)
    if not match:
        raise ValueError('Could not determine the container runtime version from: '
                         + ' '.join((output or '').split())[:200])
    product = match.group(1).lower()
    version = tuple(int(part or 0) for part in match.group(2, 3, 4))
    warnings = []
    if product == 'apptainer':
        if version[:2] < MINIMUM_APPTAINER:
            raise ValueError('Apptainer ' + '.'.join(map(str, version)) + ' is too old; '
                             + '.'.join(map(str, MINIMUM_APPTAINER)) + ' or newer is required')
    else:
        warnings.append(product + ' ' + '.'.join(map(str, version))
                        + ' is untested; Apptainer ' + '.'.join(map(str, MINIMUM_APPTAINER))
                        + ' or newer is the supported runtime')
    return {'product': product, 'version': '.'.join(map(str, version)),
            'raw': ' '.join(output.split())[:200]}, warnings


def run(config, scheduler, identity):
    from .common import digest_file
    if digest_file(config.image) != config.image_sha256:
        raise ValueError('SIF checksum mismatch')
    for name in ('apptainer', 'sbatch', 'squeue', 'sacct', 'scancel', 'srun'):
        if not os.access(getattr(config, name), os.X_OK):
            raise ValueError(name + ' executable is missing')
    try:
        result = scheduler.run([config.apptainer, '--version'])
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Could not run ' + str(config.apptainer) + ' --version') from exc
    runtime, warnings = apptainer_version(result.stdout or result.stderr)
    with tempfile.TemporaryDirectory(prefix='blender-cluster-check-') as temporary:
        result = scheduler.run(scheduler.container_command(Path(temporary)) + ['--check'])
    check = json.loads(result.stdout)
    if check.get('engine_build') != identity or check.get('platform') != 'Linux' or check.get('python') != '3.11.15':
        raise ValueError('Gateway and container must use the same renderer build and pinned Linux Python')
    with tempfile.TemporaryDirectory(prefix='blender-cluster-dry-run-') as temporary:
        dry_run = scheduler.dry_run(Path(temporary))
    return {**check, 'status': 'configured', 'image_sha256': config.image_sha256,
            'runtime': str(config.runtime_home or '/opt/studio-runtime'),
            'apptainer': runtime, 'slurm_dry_run': dry_run, 'warnings': warnings,
            'render_verified': False}
