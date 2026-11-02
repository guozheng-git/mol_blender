"""One Slurm job, one existing local render. Runs in the image's uv environment."""
import argparse
import fcntl
import os
from pathlib import Path
import time
import traceback
import json
import platform

from .common import ARTIFACTS, Limits, atomic_json, digest_file, read_json, validate_request


def run(directory):
    from ..engine import generate_manifest
    from ..local import render_payload
    directory = Path(directory).resolve()
    with (directory / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (directory / 'result.json').exists() or (directory / 'cancel.requested').exists():
            return 0
        receipt = {'status': 'failed', 'finished_at': time.time()}
        try:
            envelope = read_json(directory / 'request.json', 20 * 1024**2 + 64 * 1024)
            limits = Limits(**envelope['limits'])
            identity = generate_manifest(Path(__file__).resolve().parents[1])['engine_build']
            if identity != envelope['engine_build']:
                raise ValueError('Gateway and image renderer builds differ; rebuild from the same revision')
            request = validate_request(envelope['request'], limits)
            gpu = envelope.get('execution', {}).get('gpus', 0) == 1
            request['style']['renderer'].update(device='GPU' if gpu else 'CPU', require_gpu=gpu)
            if gpu:
                request['style']['renderer']['compute_device_type'] = 'CUDA'
            # A restarted/requeued batch must not render a second time.
            if (directory / 'started.json').exists():
                raise RuntimeError('Previous execution was interrupted; no automatic rerender')
            atomic_json(directory / 'started.json', {'started_at': time.time(), 'engine_build': identity})
            result = render_payload(request['payload'], output=directory / 'out/render.png',
                                    options={'style_doc': request['style']}, timeout=limits.render_timeout)
            artifacts = {}
            for name, filename in result['outputs'].items():
                path = Path(filename).resolve()
                if name not in ARTIFACTS or not path.is_relative_to(directory / 'out') or not path.is_file():
                    raise ValueError('Invalid render artifact')
                size = path.stat().st_size
                if not 0 < size <= limits.max_artifact_bytes:
                    raise ValueError('Render artifact exceeds transfer limit')
                artifacts[name] = {'path': str(path.relative_to(directory)), 'bytes': size,
                                   'sha256': digest_file(path)}
            if 'render.png' not in artifacts:
                raise ValueError('Renderer returned no PNG')
            receipt = {'status': 'completed', 'finished_at': time.time(), 'engine_build': identity,
                       'artifacts': artifacts}
        except Exception:
            (directory / 'worker-error.log').write_text(traceback.format_exc())
            receipt['finished_at'] = time.time()
        finally:
            (directory / 'request.json').unlink(missing_ok=True)
            atomic_json(directory / 'result.json', receipt)
        return 0 if receipt['status'] == 'completed' else 1


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_directory', type=Path, nargs='?')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        from ..engine import generate_manifest
        from ..local import runtime_settings
        settings = runtime_settings()
        if any(not Path(settings[key]).is_file() for key in ('blender', 'ccd')):
            raise SystemExit('Configured Blender or CCD is missing inside the container')
        print(json.dumps({'engine_build': generate_manifest(Path(__file__).resolve().parents[1])['engine_build'],
                          'platform': platform.system(), 'machine': platform.machine(),
                          'python': platform.python_version()}))
    elif args.job_directory:
        raise SystemExit(run(args.job_directory))
    else:
        parser.error('Specify a job directory or --check')


if __name__ == '__main__':
    main()
