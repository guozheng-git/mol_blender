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


def python_matches_lock(version):
    """The container's Python must match runtime.lock.json, the package's runtime
    contract, at the same major.minor precision blender_runtime checks."""
    from ..config import runtime_versions
    locked = runtime_versions()['python']
    return isinstance(version, str) and (version == locked or version.startswith(locked + '.'))


def apptainer_version(output):
    """Parse `apptainer --version` / `singularity --version` output."""
    # Wrappers and modules may print WARNING/INFO lines first; find the version line.
    match = re.search(r'^\s*(apptainer|singularity-ce|singularity)\s+version\s+v?(\d+)\.(\d+)(?:\.(\d+))?',
                      output or '', re.IGNORECASE | re.MULTILINE)
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


ENVIRONMENT_HINT = (' Commands run with PATH limited to the configured tool directories plus '
                    '/usr/bin:/bin and no LD_LIBRARY_PATH; module-provided tools that need more '
                    'environment must be wrapped or installed with RPATH.')


def _failure(prefix, exc):
    if isinstance(exc, subprocess.TimeoutExpired):
        return ValueError(prefix + ' timed out after ' + str(exc.timeout) + ' seconds')
    output = getattr(exc, 'stderr', None) or getattr(exc, 'stdout', None) or str(exc)
    if isinstance(output, bytes):
        output = output.decode(errors='replace')
    return ValueError(prefix + ' failed: ' + (' '.join(output.split())[:500] or 'no message')
                      + ENVIRONMENT_HINT)


def run(config, scheduler, identity):
    from .common import digest_file
    if digest_file(config.image) != config.image_sha256:
        raise ValueError('SIF checksum mismatch')
    for name in ('apptainer', 'sbatch', 'squeue', 'sacct', 'scancel', 'srun'):
        if not os.access(getattr(config, name), os.X_OK):
            raise ValueError(name + ' executable is missing')
    if not config.spool.is_dir():
        raise ValueError('Create the private shared spool ' + str(config.spool) + ' before running doctor')
    try:
        result = scheduler.run([config.apptainer, '--version'])
    except (OSError, subprocess.SubprocessError) as exc:
        raise _failure(str(config.apptainer) + ' --version', exc) from exc
    runtime, warnings = apptainer_version((result.stdout or '') + '\n' + (result.stderr or ''))
    with tempfile.TemporaryDirectory(prefix='blender-cluster-check-') as temporary:
        try:
            result = scheduler.run(scheduler.container_command(Path(temporary)) + ['--check'])
        except (OSError, subprocess.SubprocessError) as exc:
            raise _failure('Container identity check', exc) from exc
    try:
        check = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError('Container identity check printed no JSON: '
                         + ' '.join((result.stdout or result.stderr or '').split())[:500]) from exc
    if check.get('engine_build') != identity or check.get('platform') != 'Linux':
        raise ValueError('Gateway and container must use the same renderer build on Linux')
    if not python_matches_lock(check.get('python')):
        raise ValueError('Container Python ' + str(check.get('python')) + ' does not match runtime.lock.json')
    # Same shared filesystem as real jobs, so site job_submit rules on
    # --chdir/--output see the real request shape.
    with tempfile.TemporaryDirectory(prefix='.doctor-', dir=config.spool) as temporary:
        dry_run = scheduler.dry_run(Path(temporary))
    return {**check, 'status': 'configured', 'image_sha256': config.image_sha256,
            'runtime': str(config.runtime_home or '/opt/studio-runtime'),
            'apptainer': runtime, 'slurm_dry_run': dry_run, 'warnings': warnings,
            'render_verified': False}
