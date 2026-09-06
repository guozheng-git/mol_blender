"""Mirror exact Debian source packages for a distributed runtime image.

Only archive bytes are downloaded; source code and archive members are never
executed. Input is the image's dpkg-query binary/source-version inventory.
"""
import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ORIGIN = 'https://snapshot.debian.org'


def request(url):
    for attempt in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'blender-render-source-archive/0.2'}), timeout=90)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def metadata(path):
    with request(ORIGIN + path) as response:
        return json.load(response)


def collect(inventory, destination):
    packages = set()
    for line in inventory.read_text().splitlines():
        binary, version, source, source_version = line.split('\t')
        packages.add((source or binary.split(':')[0], source_version or version))
    destination.mkdir(parents=True, exist_ok=True)

    def package_source(item):
        name, version = item
        encoded = '/'.join(urllib.parse.quote(part, safe='') for part in item)
        files = metadata('/mr/package/' + encoded + '/srcfiles')['result']
        records = []
        for entry in files:
            sha1 = entry['hash']
            info = metadata('/mr/file/' + sha1 + '/info')['result'][0]
            filename = Path(info['name']).name
            folder = destination / name / version.replace(':','_')
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / filename
            if not output.exists():
                temporary = output.with_name(output.name + '.partial')
                with request(ORIGIN + '/file/' + sha1) as response, temporary.open('wb') as stream:
                    while data := response.read(1024 ** 2):
                        stream.write(data)
                if hashlib.file_digest(temporary.open('rb'), 'sha1').hexdigest() != sha1:
                    raise ValueError('Debian source checksum mismatch: ' + filename)
                temporary.replace(output)
            if hashlib.file_digest(output.open('rb'), 'sha1').hexdigest() != sha1:
                raise ValueError('Debian source checksum mismatch: ' + filename)
            records.append({'file':str(output.relative_to(destination)), 'bytes':output.stat().st_size,
                'sha1':sha1, 'sha256':hashlib.file_digest(output.open('rb'),'sha256').hexdigest(),
                'url':ORIGIN + '/file/' + sha1})
        return {'package':name,'version':version,'sources':records}

    with ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(package_source, sorted(packages)))
    (destination / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'packages':len(result),'source_files':sum(len(p['sources']) for p in result)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('inventory', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    collect(args.inventory, args.destination)
