"""Check release hashes and every packaged source byte against its recorded Git revision."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile


def verify(directory, repo):
    release = json.loads((directory / 'release.json').read_text())
    revision = release['source_revision']
    source_archive = subprocess.check_output(['git', '-C', str(repo), 'archive', revision,
                                              'mol_blender', 'plugins/molblender_studio'])
    with tarfile.open(fileobj=io.BytesIO(source_archive)) as archive:
        sources = {p.name: archive.extractfile(p).read() for p in archive if p.isfile()}
    for filename, expected in release['artifacts'].items():
        data = (directory / filename).read_bytes()
        assert len(data) == expected['bytes'] and hashlib.sha256(data).hexdigest() == expected['sha256'], filename
    wheel = next(directory.glob('*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        manifest = json.loads(archive.read('mol_blender/engine-manifest.json'))
        assert manifest['engine_build'] == release['engine_build']
        for name, digest in manifest['files'].items():
            content = archive.read('mol_blender/' + name)
            assert hashlib.sha256(content).hexdigest() == digest
            assert content == sources['mol_blender/' + name], name
        assert set(manifest['files']) == {n.removeprefix('mol_blender/') for n in sources if n.startswith('mol_blender/')}
    for filename, prefix in [('molblender-studio-plugin.zip', 'plugins/molblender_studio/'),
                              ('molblender-studio-skill.zip', 'plugins/molblender_studio/skills/molblender-studio/')]:
        with zipfile.ZipFile(directory / filename) as archive:
            for name, content in sources.items():
                if name.startswith(prefix):
                    assert archive.read('molblender-studio/' + name.removeprefix(prefix)) == content, name
            wheels = [n for n in archive.namelist() if n.endswith('.whl')]
            assert len(wheels) == 1 and archive.read(wheels[0]) == wheel.read_bytes()
            provenance = [n for n in archive.namelist() if n.endswith('/source-revision.json')]
            assert len(provenance) == 1
            assert json.loads(archive.read(provenance[0]))['revision'] == revision
    return {'verified': True, 'source_revision': revision, 'artifacts': list(release['artifacts'])}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(verify(args.directory.resolve(), args.repo.resolve()), indent=2))
