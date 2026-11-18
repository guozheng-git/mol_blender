"""Bounded real-render acceptance for source or a wheel installed outside the checkout.

Uses only the public 1STP fixture. All sessions are generated locally by this
script and are trusted. Requires configured Blender/CCD, PyMOL and FFmpeg.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--skill', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--pymol', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--browser', help='Chrome executable for real MP4 playback')
    args = p.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    script = args.skill.resolve() / 'scripts/studio.py'
    cli = Path(sys.executable).parent / 'mol-blender'
    config = json.loads(args.config.read_text())
    config.update(local_python=sys.executable, pymol_python=str(args.pymol.resolve().parent / args.pymol.name))
    # Keep the venv interpreter symlink, not its resolved base Python.
    config['pymol_python'] = str(args.pymol.absolute())
    (root / 'config').mkdir()
    (root / 'config/config.json').write_text(json.dumps(config))
    (root / 'studio').mkdir()
    (root / 'studio/state.json').write_text(json.dumps({
        'external_renderer': True, 'mol_blender': str(cli),
        'mol_blender_home': str(root / 'config'), 'pymol_python': str(args.pymol.absolute())}))
    env = dict(os.environ, MOL_BLENDER_HOME=str(root / 'config'), MOLBLENDER_STUDIO_HOME=str(root / 'studio'))
    def run(name, command):
        result = subprocess.run(list(map(str, command)), env=env, capture_output=True, text=True, timeout=900)
        (root / (name + '.log')).write_text(result.stdout + '\n' + result.stderr)
        if result.returncode:
            raise RuntimeError(f'{name} failed; see {root / (name + ".log")}')
        return result.stdout

    make = root / 'make_fixture.py'
    make.write_text("""import sys, pymol2
with pymol2.PyMOL() as p:
 c=p.cmd
 c.load(sys.argv[1], 'molecule'); c.remove('solvent'); c.dss('all')
 for s in (2,3):
  c.create('molecule','molecule',1,s); c.translate([s-1,0,0],'molecule and resi 20-40',state=s)
 c.orient('all'); c.viewport(192,144); c.scene('first','store')
 c.turn('y',40); c.scene('second','store'); c.scene('first','recall')
 c.save(sys.argv[2])
""")
    session = root / 'public-1stp.pse'
    run('fixture', [args.pymol, make, args.fixture.resolve(), session])
    still = json.loads(run('still', [cli, 'render', session, '--width', '192', '--height', '144',
                                     '--samples', '4', '--save-blend', '-o', root / 'still.png']))
    evidence = {'python': sys.executable, 'still': still, 'recipes': {}}
    from mol_blender.animation import unpack
    from mol_blender import engine
    evidence['package'] = str(Path(engine.__file__).resolve())
    evidence['engine_build'] = engine.engine_identity()['engine_build']
    sys.path.insert(0, str(script.parent))
    import artifact_files
    for recipe in ('turntable', 'rock', 'reveal', 'cutaway', 'states', 'scenes'):
        command = [sys.executable, script, 'render', session, '--recipe', recipe,
                   '--duration', '1', '--fps', '4', '--width', '192', '--height', '144',
                   '--samples', '4', '--gif', '--save-blend', '--out-dir', root / recipe,
                   '-o', root / f'{recipe}.png']
        if recipe in ('reveal', 'cutaway'):
            command += ['--focus', 'resn BTN']
        result = json.loads(run(recipe, command))
        outputs = result['outputs']
        with unpack(outputs['frames.zip']) as (frames, metadata):
            assert metadata['frames'] == 4
            hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(frames.glob('*.png'))]
            assert len(set(hashes)) > 1, 'Animation frames did not change'
        probe = json.loads(run(recipe + '-ffprobe', ['ffprobe', '-v', 'error', '-count_frames',
            '-select_streams', 'v:0', '-show_entries', 'stream=width,height,nb_read_frames,duration',
            '-of', 'json', outputs['render.mp4']]))['streams'][0]
        assert probe['nb_read_frames'] == '4' and float(probe['duration']) == 1
        from PIL import Image
        with Image.open(outputs['render.gif']) as gif:
            assert gif.n_frames == 4 and gif.info.get('loop') == 0
        files = {role: outputs[name] for role, name in [('pse','session.pse'), ('blend','render.blend'),
                 ('mp4','render.mp4'), ('gif','render.gif'), ('frames','frames.zip'), ('animation','animation.json')]}
        bundle = artifact_files.bundle(files, str(root / recipe / 'saved'))
        restored = artifact_files.restore(bundle['parts'], str(root / recipe / 'restored'))
        assert {p['sha256'] for p in restored} == {p['sha256'] for p in bundle['files']}
        result.update(frame_hashes=hashes, probe=probe, restored=restored)
        evidence['recipes'][recipe] = result
        print(recipe + ': rendered, encoded and restored', flush=True)
    run('reencode', [cli, 'movie-export', evidence['recipes']['rock']['outputs']['frames.zip'],
                     '-o', root / 'reencoded.gif'])
    reopened = root / 'reopen.py'
    reopened.write_text("""import sys, pymol2
with pymol2.PyMOL() as p:
 p.cmd.load(sys.argv[1]); assert p.cmd.count_atoms('all') > 100
 assert p.cmd.get_coords('all').shape[1] == 3
 print(p.cmd.count_atoms('all'), len(p.cmd.get_view()))
""")
    restored = evidence['recipes']['rock']['restored']
    pse = next(p['path'] for p in restored if p['role'] == 'pse')
    blend = next(p['path'] for p in restored if p['role'] == 'blend')
    run('pse-reopen', [args.pymol, reopened, pse])
    reopen_blend = root / 'reopen_blend.py'
    reopen_blend.write_text("import bpy, sys\nbpy.ops.wm.open_mainfile(filepath=sys.argv[-1], load_ui=False, use_scripts=False)\n"
                            "assert bpy.context.scene.camera\nassert len(bpy.data.objects)>1\nprint('REOPEN_OK')\n")
    # The same script works with native Blender and Studio's bpy command shim.
    run('blend-reopen', [config['blender'], '--background', '--disable-autoexec',
                         '--python-exit-code', '1', '--python', reopen_blend, '--', blend])
    workspace = json.loads(run('workspace-reopen', [sys.executable, script, 'workspace', pse,
                                                    '--out-dir', root / 'workspace']))
    evidence['workspace'] = workspace
    if args.browser:
        from playwright.sync_api import sync_playwright
        video = Path(evidence['recipes']['turntable']['outputs']['render.mp4'])
        page_path = root / 'playback.html'
        page_path.write_text('<video id="movie" muted loop src="' + video.as_uri() + '"></video>')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
            page = browser.new_page()
            page.goto(page_path.as_uri())
            page.evaluate("document.getElementById('movie').play()")
            page.wait_for_function("document.getElementById('movie').currentTime > 0.2")
            evidence['playback'] = page.evaluate("({time:movie.currentTime,width:movie.videoWidth,height:movie.videoHeight,paused:movie.paused})")
            assert evidence['playback']['width'] == 192 and not evidence['playback']['paused']
            browser.close()
    (root / 'acceptance.json').write_text(json.dumps(evidence, indent=2))
    print(root / 'acceptance.json')


if __name__ == '__main__':
    main()
