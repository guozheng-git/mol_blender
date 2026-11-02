"""Real PyMOL -> loopback HTTP -> Slurm -> Apptainer acceptance.

Run on an authorized Linux gateway with a configured SIF and local PyMOL.
Creates a private spool and temporary credentials under a NEW shared output
directory. Submits two small renders and one cancellation probe. No scheduler
substitute is used. Never run this against a production spool.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-config', type=Path, required=True)
    parser.add_argument('--pymol', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True, help='Public CIF fixture')
    parser.add_argument('--skill', type=Path, required=True, help='Staged Structure Studio skill')
    parser.add_argument('--output', type=Path, required=True, help='New directory on shared storage')
    parser.add_argument('--timeout', type=int, default=900, help='Queue plus render deadline per job')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output.absolute()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    config_home = root / 'client'
    config_home.mkdir(mode=0o700)
    os.environ['BLENDER_RENDER_HOME'] = str(config_home)

    from blender_render import engine, pymol_bridge_plugin
    from blender_render.animation import unpack
    from blender_render.cluster.auth import add_user, revoke_user
    from blender_render.cluster.client import Client, RemoteError, configure, profile_path
    from blender_render.cluster.common import TERMINAL, atomic_json, digest_file
    from blender_render.cluster.config import ServerConfig
    from blender_render.cluster.server import create_app
    from blender_render.cluster.slurm import Slurm
    from blender_render.spec import resolve_options
    from PIL import Image
    import uvicorn

    atomic_json(config_home / 'config.json', {'local_python': sys.executable,
                                             'pymol_python': str(args.pymol.absolute())})
    config = replace(ServerConfig.load(args.server_config), spool=root / 'jobs',
                     tokens_file=root / 'users.json')
    scheduler = Slurm(config)
    inspection = root / 'inspection'
    inspection.mkdir(mode=0o700)
    inspector = Slurm(replace(config, gpus=0))
    evidence = {'passed': False, 'execution': 'real Slurm and Apptainer',
                'engine_build': engine.engine_identity()['engine_build'],
                'package': str(Path(engine.__file__).resolve()),
                'image_sha256': config.image_sha256, 'runtime_home': str(config.runtime_home or '/opt/studio-runtime'),
                'gpus': config.gpus, 'cpus': config.cpus, 'renders': {}}
    started = time.monotonic()

    def run(name, command, timeout=120):
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=timeout)
        (root / (name + '.log')).write_text(result.stdout + '\n' + result.stderr)
        if result.returncode:
            raise RuntimeError(name + ' failed; inspect its private log')
        return result.stdout

    def wait(client, identifier, timeout=None):
        deadline, previous = time.monotonic() + (timeout or args.timeout), None
        while True:
            status = client.status(identifier)
            if status['status'] != previous:
                print(json.dumps({'job_id': identifier, 'slurm_id': status.get('slurm_id'),
                                  'status': status['status']}), flush=True)
                previous = status['status']
            if status['status'] in TERMINAL or status['status'] == 'submission_unknown':
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError('Acceptance deadline reached for ' + identifier)
            time.sleep(3)

    def denied(call, code):
        try:
            call()
        except RemoteError as exc:
            assert 'HTTP ' + str(code) + ':' in str(exc), str(exc)
        else:
            raise AssertionError('Expected HTTP ' + str(code))

    # Explicit preflight before opening a listener or submitting an allocation.
    assert digest_file(config.image) == config.image_sha256
    probe = json.loads(run('container-check', inspector.container_command(inspection) + ['--check']))
    assert probe['engine_build'] == evidence['engine_build']
    assert probe['platform'] == 'Linux' and probe['python'] == '3.11.15'
    evidence['container_check'] = probe
    atomic_json(root / 'evidence.json', evidence)

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    for owner in ('acceptance', 'other'):
        credential = root / (owner + '.credential.json')
        add_user(config.tokens_file, owner, url, credential)
        configure(owner, credential)
        credential.unlink()
    app = create_app(config)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    client, other = Client('acceptance'), Client('other')
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    export = root / 'submit_from_pymol.py'
    export.write_text('''import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parents[1]))
import pymol2
from blender_render.pymol_bridge_plugin import bridge_render, build_render_payload
with pymol2.PyMOL() as p:
    p.cmd.load(sys.argv[2], 'molecule')
    p.cmd.remove('solvent'); p.cmd.dss('all'); p.cmd.orient('all'); p.cmd.viewport(192,144)
    root = Path(sys.argv[3])
    if sys.argv[4] == 'still':
        p.cmd.save(str(root / 'public-fixture.pse'))
        (root / 'payload.json').write_text(json.dumps(build_render_payload(p.cmd, '(all)', 1)))
    motion = dict(animation='rock', duration=1, fps=4, gif=1) if sys.argv[4]=='rock' else {}
    result = bridge_render(cluster='acceptance', preset='mint_sky_assembly',
        output_dir=str(root), output_name=sys.argv[4]+'.png', width=192, height=144,
        samples=4, save_blend=1, auto_preview=0, _self=p.cmd, **motion)
    (root / (sys.argv[4]+'.submitted.json')).write_text(json.dumps(result))
''')
    reopen = inspection / 'reopen_blend.py'
    reopen.write_text("import bpy, sys\nbpy.ops.wm.open_mainfile(filepath=sys.argv[-1], load_ui=False, use_scripts=False)\n"
                      "assert bpy.context.scene.camera\nassert len(bpy.data.objects)>1\nprint('REOPEN_OK')\n")
    # Use the same fixed container/runtime, replacing only the worker entrypoint
    # with this administrator-authored read-only inspection script.
    inspect_command = inspector.container_command(inspection)[:-2] + ['/job/reopen_blend.py']

    def check_scene(name, blend):
        # Expose only the generated scene and inspection script, never gateway
        # credentials or other jobs. Opening a scene does not need a GPU.
        scene = inspection / 'scene.blend'
        os.link(blend, scene)
        try:
            assert 'REOPEN_OK' in run(name, inspect_command + ['/job/scene.blend'])
        finally:
            scene.unlink()
    try:
        assert server.started
        unauthorized = Client('other')
        unauthorized.token = 'invalid-' * 8
        denied(lambda: unauthorized.api('GET', '/v1/info'), 401)
        for name in ('still', 'rock'):
            run(name + '-pymol', [args.pymol.absolute(), export,
                Path(pymol_bridge_plugin.__file__).resolve(), args.fixture.resolve(), root, name])
            submitted = json.loads((root / (name + '.submitted.json')).read_text())
            identifier = submitted['job_id']
            assert client.recover(submitted['receipt'])['job_id'] == identifier
            denied(lambda: other.status(identifier), 404)
            status = wait(client, identifier)
            assert status['status'] == 'completed', status
            assert scheduler.find_submission(identifier, status['created_at']) == status['slurm_id']
            fetched = client.fetch(identifier, root / (name + '.png'))
            log = config.spool / identifier / 'slurm.log'
            if log.is_file():
                (root / (name + '-slurm.log')).write_bytes(log.read_bytes())
            manifest = json.loads(Path(fetched['outputs']['manifest.json']).read_text())
            device = manifest['cycles_device']
            assert device['device'] == ('GPU' if config.gpus else 'CPU'), device
            if config.gpus:
                assert device['compute_type'] == 'CUDA' and device['devices'], device
            assert manifest['runtime_versions']['python'] == '3.11.15'
            assert not (config.spool / identifier / 'request.json').exists()
            denied(lambda: other.api('GET', '/v1/jobs/' + identifier + '/artifacts/render.png'), 404)
            with Image.open(fetched['outputs']['render.png']) as png:
                assert png.size == (192, 144)
                assert png.convert('RGB').getcolors(50) is None
            blend = Path(fetched['outputs']['render.blend'])
            check_scene(name + '-reopen', blend)
            if name == 'rock':
                with unpack(fetched['outputs']['frames.zip']) as (frames, metadata):
                    hashes = [digest_file(p) for p in sorted(frames.glob('*.png'))]
                    assert len(hashes) == metadata['frames'] == 4 and len(set(hashes)) > 1
                run('movie-decode', ['ffmpeg', '-v', 'error', '-i', fetched['outputs']['render.mp4'], '-f', 'null', '-'])
                with Image.open(fetched['outputs']['render.gif']) as gif:
                    assert gif.n_frames == 4 and gif.info.get('loop') == 0
                sys.path.insert(0, str(args.skill.resolve() / 'scripts'))
                import artifact_files
                files = {role: fetched['outputs'][key] for role, key in (
                    ('blend', 'render.blend'), ('mp4', 'render.mp4'), ('gif', 'render.gif'), ('frames', 'frames.zip'))}
                files['pse'] = str(root / 'public-fixture.pse')
                bundle = artifact_files.bundle(files, str(root / 'saved'))
                restored = artifact_files.restore(bundle['parts'], str(root / 'restored'))
                assert {p['sha256'] for p in restored} == {p['sha256'] for p in bundle['files']}
                restored_blend = Path(next(p['path'] for p in restored if p['role'] == 'blend'))
                check_scene('restored-reopen', restored_blend)
                pse = next(p['path'] for p in restored if p['role'] == 'pse')
                run('pse-reopen', [args.pymol.absolute(), '-c',
                    "import pymol2,sys\nwith pymol2.PyMOL() as p:\n p.cmd.load(sys.argv[1]); assert p.cmd.count_atoms('all') > 100", pse])
                evidence['bundle_restored'] = True
            evidence['renders'][name] = {'job_id': identifier, 'slurm_id': status['slurm_id'],
                'hashes': {n: digest_file(p) for n, p in fetched['outputs'].items()}, 'blend_reopened': True,
                'cycles_device': device, 'runtime_versions': manifest['runtime_versions']}
            atomic_json(root / 'evidence.json', evidence)

        # Retry the exact same request, recover it, reject a conflicting retry,
        # then cancel the original real allocation. No retry can launch job #2.
        payload = json.loads((root / 'payload.json').read_text())
        document = {'protocol': 1, 'request_id': uuid.uuid4().hex, 'payload': payload,
                    'style': resolve_options({'width': 192, 'height': 144, 'samples': 4}, payload)['style_doc']}
        first = client.api('POST', '/v1/jobs', document)
        second = client.api('POST', '/v1/jobs', document)
        assert first['job_id'] == second['job_id'] and first['slurm_id'] == second['slurm_id']
        identifier = first['job_id']
        assert client.api('GET', '/v1/requests/' + document['request_id'])['job_id'] == identifier
        document['style']['output']['samples'] = 5
        denied(lambda: client.api('POST', '/v1/jobs', document), 409)
        denied(lambda: other.api('POST', '/v1/jobs/' + identifier + '/cancel'), 404)
        client.api('POST', '/v1/jobs/' + identifier + '/cancel')
        assert wait(client, identifier, 120)['status'] == 'cancelled'
        assert scheduler.find_submission(identifier, first['created_at']) == first['slurm_id']
        evidence['cancelled'] = {'job_id': identifier, 'slurm_id': first['slurm_id']}
        evidence['idempotency_verified'] = True
        identifiers = [r['slurm_id'] for r in evidence['renders'].values()] + [first['slurm_id']]
        evidence['accounting'] = run('accounting', [config.sacct, '--noheader', '--parsable2',
            '--allocations', '--jobs=' + ','.join(identifiers),
            '--format=JobIDRaw,State,ExitCode,AllocCPUS,ReqTRES%100,AllocTRES%100,Elapsed,NodeList'])
        for job in client.api('GET', '/v1/jobs'):
            assert client.api('DELETE', '/v1/jobs/' + job['job_id'])['status'] == 'expired'
            assert [p.name for p in (config.spool / job['job_id']).iterdir()] == ['job.json']
        evidence['cleanup_verified'] = True
        revoke_user(config.tokens_file, 'other')
        denied(lambda: other.api('GET', '/v1/info'), 401)
        evidence['ownership_and_revocation_verified'] = True
        evidence['passed'] = True
    finally:
        # Even a failing acceptance run must not leave its own allocations active.
        # Keep failed job logs for diagnosis; never touch other users' spools/jobs.
        cleanup_errors = []
        for path in config.spool.glob('*/job.json'):
            record = json.loads(path.read_text())
            if record['status'] not in TERMINAL and record.get('slurm_id'):
                try:
                    scheduler.cancel(record['slurm_id'])
                except Exception as exc:
                    cleanup_errors.append(type(exc).__name__ + ': cancellation needs inspection')
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        config.tokens_file.unlink(missing_ok=True)
        for owner in ('acceptance', 'other'):
            profile_path(owner).unlink(missing_ok=True)
        evidence['seconds'] = round(time.monotonic() - started, 2)
        evidence['cleanup_errors'] = cleanup_errors
        evidence['listener_closed'] = not thread.is_alive()
        if cleanup_errors or thread.is_alive():
            evidence['passed'] = False
        atomic_json(root / 'evidence.json', evidence)
    print(json.dumps(evidence, indent=2))
    if not evidence['passed']:
        raise SystemExit('Acceptance failed; inspect evidence.json and private logs')


if __name__ == '__main__':
    main()
