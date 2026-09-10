"""Authorized local host-Blender and private Slurm parity from one reviewed commit.

No Modal actions. Prerequisites fail before conversion or allocation. Each run
owns isolated services, credentials, operations and an isolated HPC registry.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prerequisites(config, fixture, blender, profile):
    reasons = []
    if not fixture.is_file():
        reasons.append('Required public examples/1sps_pose.pse is unavailable')
    if not blender or not Path(blender).is_file() or not os.access(blender, os.X_OK):
        reasons.append('A suitable local host Blender executable is required; container execution cannot substitute')
    if not config.get('pymol_python') or not Path(config['pymol_python']).is_file():
        reasons.append('Configure the existing PyMOL Python interpreter for public-fixture export')
    if not profile or profile.get('kind') != 'hpc':
        reasons.append('An explicit private Slurm profile is required')
    else:
        from blender_render.hpc import validate_profile
        try:
            validate_profile(profile)
        except ValueError as exc:
            reasons.append(str(exc))
    return reasons


def main():
    from blender_render.config import load_config, save_config
    from blender_render.core import _detect_host_blender_executable
    from blender_render.engine import engine_identity, reviewed_checkout
    from blender_render.execution import freeze_execution
    from blender_render.service import prepare_options, validate_input
    from blender_render.client import Client
    from blender_render import hpc
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile')
    parser.add_argument('--blender')
    parser.add_argument('--prerequisites-only', action='store_true')
    args = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if commit != args.commit:
        raise ValueError('Acceptance requires the exact reviewed commit')
    if not reviewed_checkout(ROOT)['clean']:
        raise ValueError('Acceptance requires clean reviewed inputs, including relevant untracked/ignored files')
    config = load_config()
    profile = config.get('profiles', {}).get(args.profile)
    blender = args.blender or config.get('blender') or _detect_host_blender_executable()
    fixture = ROOT/'examples/1sps_pose.pse'
    out = args.output.resolve()
    if out.is_relative_to(ROOT):
        raise ValueError('Acceptance outputs must be outside the checkout')
    out.mkdir(parents=True, exist_ok=False)
    report = {'commit':commit, 'engine_build':engine_identity()['engine_build'],
              'status':'prerequisites', 'blocked_reasons':prerequisites(config, fixture, blender, profile),
              'modal':'Modal implemented and contract-tested; real backend execution unverified.'}
    def save():
        (out/'acceptance.json').write_text(json.dumps(report, indent=2)+'\n')
    save()
    if report['blocked_reasons'] or args.prerequisites_only:
        print(json.dumps(report, indent=2))
        return 2 if report['blocked_reasons'] else 0
    # A unique registry prevents adoption or termination of somebody else's service.
    private_profile = {**profile, 'registry':profile['registry']+'-c3-'+secrets.token_hex(8),
                       'expected_commit':commit, 'expected_engine':engine_identity()}
    if hpc.remote(private_profile, 'status')['status'] != 'absent':
        raise RuntimeError('Validation registry is not empty')
    isolated_home = out/'home'
    os.environ['BLENDER_RENDER_HOME'] = str(isolated_home)
    save_config({**config, 'profiles': {'c3-private':private_profile}})
    export_script = out/'export_fixture.py'
    export_script.write_text('''import json, sys
from pathlib import Path
import pymol2
from blender_render.export import build_render_payload
with pymol2.PyMOL() as instance:
    cmd = instance.cmd
    cmd.load(sys.argv[1])
    assert cmd.count_atoms('chain A') > 0, 'Chain A absent'
    assert cmd.count_atoms('chain D') > 0, 'Chain D absent'
    assert cmd.count_atoms('chain A and polymer.protein') > 0, 'Expected protein in A absent'
    assert cmd.count_atoms('chain D and polymer.protein') > 0, 'Expected peptide in D absent'
    assert cmd.count_atoms('chain D and resn PTR') > 0, 'Expected PTR in D absent'
    view = list(cmd.get_view())
    bundle = build_render_payload(cmd, '(chain A) or (chain D)', include_color_hints=True)
    assert bundle['view']['pymol_view'] == view, 'Saved camera changed'
    assert bundle['dss']['ran']
    Path(sys.argv[2]).write_text(json.dumps(bundle, allow_nan=False))
''')
    env = {**os.environ, 'PYTHONPATH':str(ROOT)}
    with (out/'conversion.log').open('w') as log:
        subprocess.run([config['pymol_python'], str(export_script), str(fixture), str(out/'bundle.json')],
                       env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    bundle = validate_input(json.loads((out/'bundle.json').read_text()), max_atoms=10000000, max_bytes=50*1024**2)
    request = prepare_options({'rough':True, 'width':1024, 'save_blend':False,
                               'overrides':{'renderer':{'device':'GPU','compute_device_type':'AUTO','require_gpu':True}}},
                              bundle, public=False, timeout=600)
    frozen = freeze_execution(bundle, request)
    (out/'execution.json').write_text(json.dumps(frozen, indent=2)+'\n')
    ordinary, controller = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for name, value in (('token', ordinary), ('controller-token', controller)):
        path = out/name
        path.write_text(value)
        path.chmod(0o600)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    process = None
    allocation = None
    allocation_attempted = False
    try:
        with (out/'local-service.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-m', 'blender_render.cli', 'serve', '--port',str(port),
                '--root',str(out/'local'), '--token-file',str(out/'token'), '--controller-token-file',str(out/'controller-token'),
                '--blender',blender, '--job-timeout','600'], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        admin = Client('http://127.0.0.1:'+str(port), ordinary)
        deadline = time.monotonic()+30
        while True:
            try:
                admin.request('GET', '/health')
                break
            except Exception:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Local service failed to start')
                time.sleep(.2)
        report['backends'] = {}
        def render(name, client):
            client.controller_owner = 'validation'
            envelope = {**frozen, 'controller_id':'controller', 'owner_id':'validation', 'operation_id':'validation-'+name}
            job = client.request('POST', '/internal/v1/resolved', envelope)
            client.wait(job['job_id'], timeout=1800)
            result = client.download(job['job_id'], out/name/'render.png')
            assert result['complete'], result
            manifest = result['manifest']
            assert manifest['bundle_sha256'] == frozen['bundle_sha256']
            assert manifest['render_spec'] == frozen['request']['render_spec']
            assert manifest['engine']['engine_build'] == frozen['expected_engine']['engine_build']
            assert manifest['structure_summary'] == bundle['structure_summary']
            assert manifest['worker_render_count'] == 1
            assert len(client.jobs()['jobs']) == 1
            assert manifest['polymer_backend'] == 'mn_cartoon' and manifest['ligand_backend'] == 'mn_native_sticks'
            assert manifest['ligand_atom_spheres'] is False and not (out/name/'render.blend').exists()
            actual = manifest['cycles_device']
            assert actual['device'] == 'GPU' and actual['devices']
            assert actual['compute_type'] == ('METAL' if name == 'local' and sys.platform == 'darwin' else 'CUDA')
            from PIL import Image
            with Image.open(out/name/'render.png') as image:
                image.verify()
            assert result['timings']
            report['backends'][name] = {'job_id':job['job_id'], 'bundle_sha256':manifest['bundle_sha256'],
                'render_spec_sha256':manifest['render_spec']['sha256'], 'engine_build':manifest['engine']['engine_build'],
                'device':actual, 'timings':result['timings'], 'leaf_renders':manifest['worker_render_count']}
            save()
        render('local', Client(admin.url, controller))
        allocation_attempted = True
        allocation = hpc.connect('c3-private', private_profile)
        render('slurm', Client(allocation['url'], Path(allocation['controller_token_file']).read_text()))
        report['status'] = 'renders_passed_visual_inspection_required'
        save()
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if allocation_attempted:
            # connect can fail after sbatch succeeded. This registry was proven
            # absent and was generated solely for this validation invocation.
            state = hpc.remote(private_profile, 'status')
            if state.get('session_id'):
                hpc.remote(private_profile, 'stop', session_id=state['session_id'])
            if allocation and allocation.get('tunnel_pid'):
                try:
                    os.killpg(allocation['tunnel_pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for name in ('token','controller-token'):
            (isolated_home/'hpc'/'c3-private'/name).unlink(missing_ok=True)
        for name in ('token','controller-token'):
            (out/name).unlink(missing_ok=True)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
