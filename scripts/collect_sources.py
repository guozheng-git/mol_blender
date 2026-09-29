"""Download verified corresponding sources and collect their unmodified notices.

Run from a release source checkout. The generated directory is an artifact,
not repository content. No downloaded code or archive member is executed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor


def collect(root, destination):
    packages = json.loads((root / 'blender_render/runtime-sources.json').read_text())
    packages = list({(p['name'].lower().replace('_','-'),p['version']):p for p in packages}.values())
    packages += [{'name':'blender','version':'5.0.1','license':'GPL-3.0-or-later',
                  'sources':[{'url':'https://download.blender.org/source/blender-5.0.1.tar.xz',
                              'sha256':'0077a41f59843433154fdf2d8aeb6c43f6bf6951cd46987305e67f89cb4f1fbf'}]}]
    destination.mkdir(parents=True, exist_ok=True)
    archives = destination / 'archives'
    archives.mkdir(exist_ok=True)
    notices = destination / 'notices'
    notices.mkdir(exist_ok=True)
    curl = '/usr/bin/curl' if sys.platform == 'darwin' else 'curl'
    def fetch(package):
        if not package['sources']:
            raise RuntimeError('No source distribution for ' + package['name'])
        source = package['sources'][0]
        filename = source['url'].rsplit('/',1)[1]
        path = archives / filename
        if not path.exists():
            subprocess.run([curl,'--fail','--location','--silent','--show-error','--proto','=https',
                            '--output',str(path),source['url']],check=True)
        digest = hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
        if source.get('sha256') and digest != source['sha256']:
            raise RuntimeError('Source checksum mismatch: ' + filename)
        extracted = []
        def notice(name):
            path = PurePosixPath(name)
            return (not path.is_absolute() and '..' not in path.parts and
                (any(part.lower() in {'licenses','licences','license','licence'} for part in path.parts)
                 or re.search(r'copying|licen[cs]e|notice|authors|copyright',path.name,re.I)))
        def write_notice(name, data):
            out = notices / package['name'] / PurePosixPath(name)
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_bytes(data)
            extracted.append(str(out.relative_to(destination)))
        if tarfile.is_tarfile(path):
            with tarfile.open(path) as archive:
                for member in archive:
                    member_path = PurePosixPath(member.name)
                    name = member_path.name.upper()
                    if not member.isfile() or member.size > 2 * 1024**2 or member_path.is_absolute() or '..' in member_path.parts:
                        continue
                    if not notice(member.name):
                        continue
                    write_notice(member.name, archive.extractfile(member).read())
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if not member.is_dir() and member.file_size <= 2 * 1024**2 and notice(member.filename):
                        write_notice(member.filename, archive.read(member))
        return {'name':package['name'],'version':package['version'],'license_metadata':package.get('license'),
                'url':source['url'],'archive':str(path.relative_to(destination)),'sha256':digest,'notices':extracted}
    with ThreadPoolExecutor(max_workers=4) as pool:
        manifest = list(pool.map(fetch,packages))
    (destination / 'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'source_packages':len(manifest),'notice_files':sum(len(p['notices']) for p in manifest),
                      'manifest':str(destination / 'manifest.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination',type=Path)
    args = parser.parse_args()
    collect(Path(__file__).resolve().parents[1], args.destination)
