"""Real PyMOL, warm/fresh Blender, animation and restoration acceptance.

Run with a configured native Blender or Linux Studio runtime. Uses a public
structure fixture and a generated, trusted PSE. No installation or Slurm job.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--skill', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-gpu', action='store_true')
    parser.add_argument('--browser', help='Optional Chrome executable for real movie playback')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text())
    config['local_python'] = sys.executable
    home = root / 'config'
    home.mkdir(mode=0o700)
    (home / 'config.json').write_text(json.dumps(config))
    os.environ['BLENDER_RENDER_HOME'] = str(home)
    from blender_render import warm_worker, engine
    from blender_render.animation import unpack
    from PIL import Image, ImageChops, ImageStat
    env = dict(os.environ)
    cli = [sys.executable, '-m', 'blender_render.cli']
    evidence = {'passed': False, 'package': str(Path(warm_worker.__file__).resolve()),
                'engine_build': engine.engine_identity()['engine_build'], 'renders': {}}
    (root / 'evidence.json').write_text(json.dumps(evidence, indent=2))

    def run(name, command, timeout=600):
        started = time.monotonic()
        result = subprocess.run(list(map(str, command)), env=env, capture_output=True, text=True, timeout=timeout)
        (root / (name + '.log')).write_text(result.stdout + '\n' + result.stderr)
        if result.returncode:
            raise RuntimeError(name + ' failed; inspect its log')
        return result.stdout, time.monotonic() - started

    def inspect(name, elapsed):
        folder = root / (name + '.render')
        preview = json.loads((folder / 'preview.json').read_text())
        manifest = json.loads((folder / 'manifest.json').read_text())
        with Image.open(root / (name + '.png')) as png:
            assert png.size == (192, 144) and png.convert('RGBA').getbbox()
        if args.require_gpu:
            assert manifest['cycles_device']['device'] == 'GPU'
        observed = {'worker': preview['worker'], 'wall_seconds': elapsed,
                    'device': manifest['cycles_device'], 'png_sha256': preview['png_sha256']}
        assert observed['png_sha256'] == hashlib.sha256((root / (name + '.png')).read_bytes()).hexdigest()
        evidence['renders'][name] = observed
        print(json.dumps({'render': name, **observed}), flush=True)
        return observed

    session = root / 'public-fixture.pse'
    make = root / 'from_pymol.py'
    make.write_text('''import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pymol2
from blender_render.pymol_bridge_plugin import bridge_render
with pymol2.PyMOL() as p:
 c = p.cmd
 c.load(sys.argv[2], 'molecule'); c.remove('solvent'); c.dss('all'); c.orient('all')
 c.viewport(192,144); c.save(sys.argv[3])
 # Exercise the real embedded-Python handoff with the problematic launcher setting.
 os.environ['PYTHONEXECUTABLE'] = sys.executable
 bridge_render(selection='all', warm=1, width=192, height=144, samples=4,
               save_blend=1, auto_preview=0, output_dir=sys.argv[4], output_name='warm-first.png', _self=c)
''')
    try:
        _, elapsed = run('pymol-warm', [config['pymol_python'], make, Path(warm_worker.__file__).parent.parent,
                                      args.fixture.resolve(), session, root])
        first = inspect('warm-first', elapsed)
        assert first['worker']['request_index'] == 1
        for name, extra in (
            ('warm-second', ['--warm']),
            ('warm-paint', ['--warm', '--preset', 'dried_paint_green']),
            ('fresh', []),
            ('warm-rock', ['--warm', '--animation', 'rock', '--duration', '1', '--fps', '4', '--gif']),
        ):
            _, elapsed = run(name, [*cli, 'render', session, '--width', '192', '--height', '144',
                                   '--samples', '4', '--save-blend', '-o', root / (name + '.png'), *extra])
            observed = inspect(name, elapsed)
            if name.startswith('warm-'):
                assert observed['worker']['pid'] == first['worker']['pid']
                assert observed['worker']['id'] == first['worker']['id']
                assert observed['worker']['reused_process']
            else:
                assert observed['worker']['pid'] != first['worker']['pid']
                assert observed['worker']['fresh_process']
        assert warm_worker.status()['completed'] == 4
        # Rebuilding the default style in a warm process must agree with fresh Blender.
        with Image.open(root / 'warm-second.png') as a, Image.open(root / 'fresh.png') as b:
            rms = ImageStat.Stat(ImageChops.difference(a.convert('RGBA'), b.convert('RGBA'))).rms
            assert max(rms) < 2, rms
            evidence['warm_fresh_pixel_rms'] = rms
        assert evidence['renders']['warm-paint']['png_sha256'] != first['png_sha256']
        with unpack(root / 'warm-rock.frames.zip') as (frames, metadata):
            hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(frames.glob('*.png'))]
            assert metadata['frames'] == 4 and len(set(hashes)) > 1
            evidence['animation_frame_hashes'] = hashes
        run('decode-movie', ['ffmpeg', '-v', 'error', '-i', root / 'warm-rock.mp4', '-f', 'null', '-'])
        with Image.open(root / 'warm-rock.gif') as gif:
            assert gif.n_frames == 4 and gif.info.get('loop') == 0
        sys.path.insert(0, str(args.skill.resolve() / 'scripts'))
        import artifact_files
        saved = artifact_files.bundle({'pse': str(session), 'blend': str(root / 'warm-rock.render/render.blend'),
            'mp4': str(root / 'warm-rock.mp4'), 'gif': str(root / 'warm-rock.gif'),
            'frames': str(root / 'warm-rock.frames.zip')}, str(root / 'saved'))
        restored = artifact_files.restore(saved['parts'], str(root / 'restored'))
        assert {x['sha256'] for x in restored} == {x['sha256'] for x in saved['files']}
        evidence['restored'] = restored
        reopen = root / 'reopen.py'
        reopen.write_text("import sys, pymol2\nwith pymol2.PyMOL() as p:\n p.cmd.load(sys.argv[-1]); assert p.cmd.count_atoms('all') > 100\n assert p.cmd.get_coords('all').shape[1] == 3\n")
        run('pse-reopen', [config['pymol_python'], reopen, next(x['path'] for x in restored if x['role'] == 'pse')])
        reopen.write_text("import bpy, sys\nbpy.ops.wm.open_mainfile(filepath=sys.argv[-1], load_ui=False, use_scripts=False)\nassert bpy.context.scene.camera\nassert len(bpy.data.objects)>1\n")
        run('blend-reopen', [config['blender'], '--background', '--disable-autoexec', '--python-exit-code', '1',
                            '--python', reopen, '--', next(x['path'] for x in restored if x['role'] == 'blend')])
        if args.browser:
            from playwright.sync_api import sync_playwright
            page_path = root / 'playback.html'
            page_path.write_text('<video id="movie" muted loop src="' + (root / 'warm-rock.mp4').as_uri() + '"></video>')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
                page = browser.new_page()
                page.goto(page_path.as_uri())
                page.evaluate("movie.play()")
                page.wait_for_function('movie.currentTime > 0.2')
                evidence['playback'] = page.evaluate('({time:movie.currentTime,width:movie.videoWidth,height:movie.videoHeight,paused:movie.paused})')
                assert not evidence['playback']['paused']
                browser.close()
        stopped = warm_worker.stop()
        assert stopped['id'] == first['worker']['id']
        idle = warm_worker.start(idle_timeout=1)
        assert idle['id'] != stopped['id']
        deadline = time.monotonic() + 20
        while warm_worker.status()['status'] != 'stopped' and time.monotonic() < deadline:
            time.sleep(.2)
        assert warm_worker.status()['status'] == 'stopped'
        assert not Path(idle['root']).exists()
        evidence['idle_shutdown'] = True
        evidence['passed'] = True
    finally:
        warm_worker.stop(force=True)
        (root / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    print('Warm/fresh GPU, PyMOL, animation, restoration and shutdown acceptance passed.', flush=True)


if __name__ == '__main__':
    main()
