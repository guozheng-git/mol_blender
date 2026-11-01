"""Build wheel/plugin ZIPs from one clean, committed revision without editing it."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/release')
    args = parser.parse_args()
    if git('status', '--porcelain', '--untracked-files=normal').strip():
        raise SystemExit('Commit or isolate all source changes before building a release candidate.')
    revision = git('rev-parse', 'HEAD').decode().strip()
    tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
    epoch = git('show', '-s', '--format=%ct', revision).decode().strip()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise SystemExit('Release output already exists; use a new directory.')
    env = dict(os.environ, SOURCE_DATE_EPOCH=epoch, BUILD_SOURCE_REVISION=revision)
    with tempfile.TemporaryDirectory(prefix='blender-release-') as temporary:
        source = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(git('archive', revision))) as archive:
            archive.extractall(source, filter='data')
        subprocess.run([sys.executable, str(source / 'scripts/build_plugin.py'),
                        '--output', str(output)], cwd=source, env=env, check=True)
        version = tomllib.loads((source / 'pyproject.toml').read_text())['project']['version']
    wheel = next(output.glob('*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        engine = json.loads(archive.read('blender_render/engine-manifest.json'))
    files = [wheel, output / 'structure-studio-plugin.zip', output / 'structure-studio-skill.zip']
    record = {'source_revision': revision, 'source_tree': tree, 'version': version,
              'source_date_epoch': int(epoch), 'engine_build': engine['engine_build'],
              'artifacts': {p.name: {'bytes': p.stat().st_size,
                            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}}
    (output / 'release.json').write_text(json.dumps(record, indent=2) + '\n')
    if git('rev-parse', 'HEAD').decode().strip() != revision or git('status', '--porcelain', '--untracked-files=normal').strip():
        raise SystemExit('Source changed during the build; discard this candidate and rebuild.')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
