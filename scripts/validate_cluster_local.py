"""Real PyMOL -> HTTP -> worker -> Blender smoke, with a LOCAL scheduler substitute.

This does not validate Slurm, Apptainer or Linux. Run with source or an isolated
installed wheel and an already configured local runtime. Uses only a public CIF.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time


class LocalValidationScheduler:
    def __init__(self):
        self.processes = {}
        self.handles = []

    def submit(self, directory, identifier):
        handle = (directory / 'local-worker.log').open('w')
        self.handles.append(handle)
        process = subprocess.Popen([sys.executable, '-I', '-m', 'blender_render.cluster.worker', str(directory)],
                                   stdout=handle, stderr=subprocess.STDOUT, cwd=directory)
        self.processes[str(process.pid)] = process
        return str(process.pid)

    def state(self, identifier):
        code = self.processes[identifier].poll()
        return 'RUNNING' if code is None else 'COMPLETED' if code == 0 else 'FAILED'

    def close(self):
        for process in self.processes.values():
            process.wait(timeout=900)
        for handle in self.handles:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--pymol', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    root.chmod(0o700)
    runtime = json.loads(args.config.read_text())
    runtime.update(local_python=sys.executable, pymol_python=str(args.pymol.absolute()))
    config_home = root / 'config'
    config_home.mkdir(mode=0o700)
    (config_home / 'config.json').write_text(json.dumps(runtime))
    os.environ['BLENDER_RENDER_HOME'] = str(config_home)
    from blender_render.cluster.common import atomic_json, digest_file
    from blender_render.cluster.config import ServerConfig
    from blender_render.cluster.auth import add_user
    from blender_render.cluster.client import Client, configure
    from blender_render.cluster.server import create_app
    from blender_render import engine, pymol_bridge_plugin
    import uvicorn
    from PIL import Image
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    fake_image = root / 'NOT-A-CONTAINER.sif'
    fake_image.write_bytes(b'local scheduler substitute: not an Apptainer test')
    credential = root / 'credential.json'
    tokens = root / 'users.json'
    add_user(tokens, 'acceptance', url, credential)
    configure('acceptance', credential)
    scheduler = LocalValidationScheduler()
    config = ServerConfig(spool=root / 'jobs', image=fake_image,
                          image_sha256=digest_file(fake_image), tokens_file=tokens)
    app = create_app(config, scheduler=scheduler)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    started = time.monotonic()
    deadline = started + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    evidence = {'engine_build': engine.engine_identity()['engine_build'],
                'package': str(Path(engine.__file__).resolve()),
                'execution': 'real local PyMOL/HTTP/Blender; LOCAL scheduler substitute',
                'slurm_verified': False, 'apptainer_verified': False, 'renders': {}}
    export = root / 'submit_from_pymol.py'
    export.write_text('''import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parents[1]))
import pymol2
from blender_render.pymol_bridge_plugin import bridge_render
with pymol2.PyMOL() as p:
    p.cmd.load(sys.argv[2], 'molecule')
    p.cmd.remove('solvent'); p.cmd.dss('all'); p.cmd.orient('all'); p.cmd.viewport(192,144)
    motion = dict(animation='rock', duration=1, fps=4, gif=1) if sys.argv[4]=='rock' else {}
    result = bridge_render(cluster='acceptance', preset='mint_sky_assembly',
        output_dir=sys.argv[3], output_name=sys.argv[4]+'.png', width=192, height=144,
        samples=4, save_blend=1, auto_preview=0, _self=p.cmd, **motion)
    Path(sys.argv[3], sys.argv[4]+'.submitted.json').write_text(json.dumps(result))
''')
    reopen = root / 'reopen_blend.py'
    reopen.write_text("import bpy, sys\nbpy.ops.wm.open_mainfile(filepath=sys.argv[-1], load_ui=False, use_scripts=False)\n"
                      "assert bpy.context.scene.camera\nassert len(bpy.data.objects)>1\nprint('REOPEN_OK')\n")
    try:
        client = Client('acceptance')
        for name in ('still', 'rock'):
            result = subprocess.run([str(args.pymol.absolute()), str(export), str(Path(pymol_bridge_plugin.__file__).resolve()),
                                     str(args.fixture.resolve()), str(root), name], capture_output=True, text=True, timeout=120)
            (root / (name + '-pymol.log')).write_text(result.stdout + '\n' + result.stderr)
            if result.returncode:
                raise RuntimeError('PyMOL submission failed; see ' + name + '-pymol.log')
            submitted = json.loads((root / (name + '.submitted.json')).read_text())
            status = client.wait(submitted['job_id'], timeout=900)
            if status['status'] != 'completed':
                raise RuntimeError('Worker did not complete: ' + json.dumps(status))
            fetched = client.fetch(status['job_id'], root / (name + '.png'))
            with Image.open(fetched['outputs']['render.png']) as png:
                assert png.size == (192, 144)
                assert png.convert('RGB').getcolors(50) is None
            blend = fetched['outputs']['render.blend']
            reopened = subprocess.run([runtime['blender'], '--background', '--disable-autoexec',
                '--python-exit-code', '1', '--python', str(reopen), '--', blend],
                capture_output=True, text=True, timeout=120)
            (root / (name + '-reopen.log')).write_text(reopened.stdout + '\n' + reopened.stderr)
            assert reopened.returncode == 0 and 'REOPEN_OK' in reopened.stdout
            if name == 'rock':
                from blender_render.animation import unpack
                with unpack(fetched['outputs']['frames.zip']) as (frames, metadata):
                    assert len(list(frames.glob('*.png'))) == metadata['frames'] == 4
                subprocess.run(['ffmpeg', '-v', 'error', '-i', fetched['outputs']['render.mp4'],
                                '-f', 'null', '-'], check=True, capture_output=True, timeout=60)
            evidence['renders'][name] = {'job_id': status['job_id'], 'outputs': fetched['outputs'],
                'hashes': {n: digest_file(p) for n, p in fetched['outputs'].items()},
                'blend_reopened': True}
        assert len(scheduler.processes) == 2
        evidence['seconds'] = round(time.monotonic() - started, 2)
        atomic_json(root / 'evidence.json', evidence)
        print(json.dumps(evidence, indent=2))
    finally:
        scheduler.close()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        credential.unlink(missing_ok=True)
        tokens.unlink(missing_ok=True)
        (config_home / 'clusters/acceptance.json').unlink(missing_ok=True)


if __name__ == '__main__':
    main()
