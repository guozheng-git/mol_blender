"""Audit every locked version, including platform variants and render runtimes.

Run with ``uv run --locked --group security python scripts/audit_dependencies.py``.
Only public package names and versions are sent to PyPI's advisory service.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_LOCKS = (
    'blender_render/runtime-requirements.txt',
    'plugins/structure-studio/skills/structure-studio/references/render-requirements.txt',
    'plugins/structure-studio/skills/structure-studio/references/pymol-requirements.txt',
)


def locked_packages(root: Path) -> dict[str, list[str]]:
    versions = defaultdict(set)

    def add(name, version):
        versions[re.sub(r'[-_.]+', '-', name).lower()].add(version)

    for package in tomllib.loads((root / 'uv.lock').read_text())['package']:
        if 'registry' in package.get('source', {}):
            add(package['name'], package['version'])
    project = tomllib.loads((root / 'pyproject.toml').read_text())
    for requirement in project['build-system']['requires']:
        match = re.fullmatch(r'([\w.-]+)==([\w.+!-]+)', requirement)
        if not match:
            raise ValueError(f'Build requirement must be pinned for auditing: {requirement}')
        add(*match.groups())
    for filename in RUNTIME_LOCKS:
        pins = re.findall(r'^([\w.-]+)==([^\s;\\]+)', (root / filename).read_text(), re.M)
        if not pins:
            raise ValueError(f'No package pins found in {filename}')
        for name, version in pins:
            add(name, version)
    return {name: sorted(values) for name, values in sorted(versions.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write combined JSON evidence')
    args = parser.parse_args()
    packages = locked_packages(ROOT)
    reports = []
    failed = False
    # pip-audit rejects two versions of one name in a requirements file.
    # Audit each variant, including ones not selected on this OS/Python.
    with tempfile.TemporaryDirectory(prefix='blender-dependency-audit-') as temporary:
        directory = Path(temporary)
        for index in range(max(map(len, packages.values()))):
            requirements = directory / f'packages-{index}.txt'
            requirements.write_text(''.join(
                f'{name}=={versions[index]}\n' for name, versions in packages.items()
                if len(versions) > index))
            output = directory / f'audit-{index}.json'
            result = subprocess.run([
                sys.executable, '-m', 'pip_audit', '--disable-pip', '--no-deps',
                '--progress-spinner', 'off', '-r', str(requirements),
                '--format', 'json', '--output', str(output),
            ], check=False)
            if not output.is_file():
                raise SystemExit(result.returncode or 'Advisory service returned no report')
            report = json.loads(output.read_text())
            reports.append(report)
            failed |= result.returncode != 0
            for package in report['dependencies']:
                if package.get('skip_reason'):
                    failed = True
                    print(f"UNVERIFIED {package['name']}: {package['skip_reason']}", file=sys.stderr)
                for vulnerability in package.get('vulns', []):
                    failed = True
                    print(f"{package['name']}=={package['version']}: {vulnerability['id']} "
                          f"(fixes: {', '.join(vulnerability['fix_versions']) or 'none listed'})")
    evidence = {'package_versions': sum(map(len, packages.values())), 'reports': reports}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(f"Audited {evidence['package_versions']} locked package versions")
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
