"""Offline application contracts and an outside-checkout installed-wheel check.

Run with the prepared test interpreter. Build/installation are explicit runner
activities; every pytest process loads its audit guard before application imports.
No dependency fetching, render, SSH, scheduler or provider action is authorized.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ['test_execution_envelope.py', 'test_gateway_recovery.py', 'test_hpc_negotiation.py',
             'test_hpc_lifecycle.py', 'test_runtime_contracts.py', 'test_worker_http.py',
             'test_operation_lifecycle.py', 'test_result_recovery.py', 'test_jobs_api.py',
             'test_cli_operation_routing.py', 'test_gateway_isolation.py', 'test_c3_inprocess.py',
             'test_engine_package.py']


def run(command, cwd, env, log):
    with log.open('w') as stream:
        subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if out.is_relative_to(ROOT):
        raise ValueError('Use temporary storage outside the checkout')
    out.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'BLENDER_RENDER_HOME':str(out/'home'),
           'PYMOL_BRIDGE_SESSION_ROOT':str(out/'sessions'), 'PYMOL_BRIDGE_TMP_ROOT':str(out/'runs'),
           'PYTHONPATH':str(ROOT/'tests')}
    pytest = [sys.executable, '-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider', '-p', 'c3_boundary_guard']
    run(pytest, ROOT, env, out/'repository.log')
    staging = out/'source'
    staging.mkdir()
    for name in ('pyproject.toml', 'setup.py', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT/name, staging/name)
    shutil.copytree(ROOT/'LICENSES', staging/'LICENSES')
    # Stage only package source/resources, never ignored render caches or SIFs.
    import runpy
    generate = runpy.run_path(str(ROOT/'blender_render/engine.py'))['generate_manifest']
    (staging/'blender_render').mkdir()
    for name in generate(ROOT/'blender_render')['files']:
        shutil.copy2(ROOT/'blender_render'/name, staging/'blender_render'/name)
    run([sys.executable, '-m', 'build', '--wheel', '--no-isolation', '--outdir', str(out/'wheels')],
        staging, env, out/'build.log')
    wheel = next((out/'wheels').glob('*.whl'))
    site = out/'site'
    run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps', '--target', str(site), str(wheel)],
        out, env, out/'install.log')
    shutil.copytree(ROOT/'tests', out/'tests', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    env['PYTHONPATH'] = os.pathsep.join([str(site), str(out/'tests')])
    env['C3_INSTALLED_SITE'] = str(site)
    run([*pytest, *(str(out/'tests'/name) for name in CONTRACTS)], out, env, out/'installed.log')
    with zipfile.ZipFile(wheel) as archive:
        manifest = json.loads(archive.read('blender_render/engine-manifest.json'))
        for name, digest in manifest['files'].items():
            data = archive.read('blender_render/'+name)
            assert hashlib.sha256(data).hexdigest() == digest
            assert data == (ROOT/'blender_render'/name).read_bytes() == (site/'blender_render'/name).read_bytes()
    evidence = {'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
                'engine_build':manifest['engine_build'], 'verified_package_files':len(manifest['files']),
                'repository_log':'repository.log', 'installed_log':'installed.log',
                'application_boundaries':'guarded; no real compute',
                'installed_dependencies':'prepared runner dependencies; not a clean dependency install'}
    (out/'evidence.json').write_text(json.dumps(evidence, indent=2)+'\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
