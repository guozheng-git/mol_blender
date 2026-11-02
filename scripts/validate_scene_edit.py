"""Real Blender edit/save/preset/re-render acceptance with a public structure.

Requires an already configured local runtime; installs nothing. The generated
Blender editing script is a test fixture, not code supplied by the input scene.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


EDIT = '''import bpy, math, sys
from pathlib import Path
from mathutils import Matrix, Vector
root = Path(sys.argv[-2]); kind = sys.argv[-1]
bpy.ops.wm.open_mainfile(filepath=str(root / (kind + '.render/render.blend')), load_ui=False, use_scripts=False)
scene = bpy.context.scene
if kind == 'base':
    mat = bpy.data.materials['blue']
    nodes = mat.node_tree.nodes
    for name, index in [('Clay shadow', 1), ('Clay shadow', 2), ('Clay highlight', 2)]:
        nodes[name].inputs[index].default_value = (230/255, 105/255, 55/255, 1)
    bsdf = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Roughness'].default_value = .73
    # Node renaming and rearrangement are UI edits, not a different shader.
    nodes['Clay shadow'].name = 'My orange pigment'
    nodes['My orange pigment'].location.x += 100
    scene.world.node_tree.nodes['Background color'].inputs['Color'].default_value = (33/255, 44/255, 66/255, 1)
    light = next(obj for obj in scene.objects if obj.type == 'LIGHT')
    light.data.energy *= .65
    light.data.size *= 1.15
    light.location.x += .15
    light.rotation_euler.z += .1
    scene.camera.matrix_world = Matrix.Translation(Vector((.04, -.02, 0))) @ scene.camera.matrix_world @ Matrix.Rotation(.12, 4, 'Z')
    scene.camera.data.shift_x = .035
    scene.camera.data.ortho_scale *= 1.1
    scene.view_settings.exposure = .25
else:
    mat = next(mat for mat in bpy.data.materials if mat.node_tree and mat.node_tree.nodes.get('Opaque dried acrylic paint'))
    nodes = mat.node_tree.nodes
    nodes['Opaque dried acrylic paint'].inputs['Specular IOR Level'].default_value = .2
    nodes['Uneven dry satin roughness'].inputs['To Max'].default_value = .85
    nodes['Dried paint surface relief'].inputs['Distance'].default_value *= .7
    nodes['Forest green pigment tint'].inputs[2].default_value = (.13, .25, .18, 1)
# A registered text block must not run on extraction or ordinary opening.
trap = bpy.data.texts.new('do_not_run.py'); trap.use_module = True
trap.write("from pathlib import Path\\nPath(" + repr(str(root / 'AUTOEXEC_RAN')) + ").write_text('unexpected')\\n")
bpy.context.view_layer.update()
bpy.ops.wm.save_as_mainfile(filepath=str(root / (kind + '-edited.blend')))
# CPU here avoids changing or depending on a user's saved GPU preferences.
# The shared renderer replays the preset on the configured local GPU separately.
scene.cycles.device = 'CPU'
scene.render.filepath = str(root / (kind + '-direct.png'))
bpy.ops.render.render(write_still=True)
if kind == 'base':
    bpy.ops.mesh.primitive_cube_add()
    bpy.ops.wm.save_as_mainfile(filepath=str(root / 'unsupported.blend'))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True, help='Public PDB/CIF or explicitly trusted local PSE')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--open-blender', action='store_true', help='Also launch a desktop window for manual inspection')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    config = json.loads(args.config.read_text())
    config['local_python'] = sys.executable
    home = root / 'config'
    home.mkdir(mode=0o700)
    (home / 'config.json').write_text(json.dumps(config))
    os.environ['BLENDER_RENDER_HOME'] = str(home)
    from blender_render import engine, warm_worker
    from blender_render.local import render_file
    from blender_render.scene_edit import inspect_scene, save_from_blend
    from PIL import Image, ImageChops, ImageStat
    evidence = {'passed': False, 'engine_build': engine.engine_identity()['engine_build'], 'cases': {}}
    evidence_path = root / 'evidence.json'
    script = root / 'edit.py'
    script.write_text(EDIT)
    try:
        for kind, preset in [('base', 'mint_sky_assembly'), ('paint', 'dried_paint_green')]:
            started = time.monotonic()
            rendered = render_file(args.fixture, output=root / (kind + '.png'),
                options={'preset': preset, 'save_blend': True, 'width': 192, 'height': 144, 'samples': 8})
            original = inspect_scene(rendered['outputs']['render.blend'])
            assert not original['preserve_input']['unsupported'], original['preserve_input']['unsupported']
            assert not original['preserve_input']['changes']
            assert not original['authored']['unsupported'], original['authored']['unsupported']
            manifest = json.loads((root / (kind + '.render/manifest.json')).read_text())
            with (root / (kind + '-edit.log')).open('w') as log:
                result = subprocess.run([config['blender'], '--background', '--factory-startup', '--disable-autoexec',
                                         '--python-exit-code', '1', '--python', str(script), '--', str(root), kind],
                                        stdout=log, stderr=subprocess.STDOUT, timeout=180)
            assert result.returncode == 0, kind + ' edit failed; see log'
            edited = root / (kind + '-edited.blend')
            before_hash = hashlib.sha256(edited.read_bytes()).hexdigest()
            saved = save_from_blend(edited, 'user/' + kind, camera_policy='authored', report=root / (kind + '-report.json'))
            assert not saved['partial'] and saved['changes']
            assert before_hash == hashlib.sha256(edited.read_bytes()).hexdigest()
            assert not (root / 'AUTOEXEC_RAN').exists()
            replayed = render_file(args.fixture, output=root / (kind + '-replayed.png'),
                                   options={'preset': 'user/' + kind, 'save_blend': True}, warm=True)
            with Image.open(root / (kind + '-direct.png')) as direct, Image.open(root / (kind + '-replayed.png')) as replay:
                assert direct.size == replay.size == (192, 144)
                rms = ImageStat.Stat(ImageChops.difference(direct.convert('RGBA'), replay.convert('RGBA'))).rms
            assert max(rms) < 2.0, (kind, rms)
            evidence['cases'][kind] = {'pixel_rms': rms, 'elapsed_seconds': time.monotonic() - started,
                                       'changes': saved['changes'], 'source_device': manifest['cycles_device']['device'],
                                       'replay_pid': replayed['worker']['pid'], 'png': replayed['outputs']['render.png']}
            evidence_path.write_text(json.dumps(evidence, indent=2))
            print(json.dumps({'case': kind, 'pixel_rms': rms, 'passed': True}), flush=True)
        preserved = save_from_blend(root / 'base-edited.blend', 'user/preserve', report=root / 'preserve-report.json')
        assert preserved['ignored'] and preserved['camera_policy'] == 'preserve_input'
        # The real embedded PyMOL interpreter uses the configured controller for
        # extraction and then renders with the newly saved named preset.
        bridge = root / 'embedded_bridge_check.py'
        bridge.write_text('''import os, sys, pymol2
sys.path.insert(0, sys.argv[1])
from blender_render.pymol_bridge_plugin import bridge_preset_from_blend, bridge_render
os.environ['PYTHONEXECUTABLE'] = sys.executable
bridge_preset_from_blend(sys.argv[3], 'user/from-pymol')
with pymol2.PyMOL() as p:
 p.cmd.load(sys.argv[2]); p.cmd.orient('all')
 bridge_render(preset='user/from-pymol', width=192, height=144, samples=4, save_blend=1,
               auto_preview=0, output_dir=sys.argv[4], output_name='pymol.png', _self=p.cmd)
''')
        with (root / 'pymol.log').open('w') as log:
            result = subprocess.run([config['pymol_python'], str(bridge), str(Path(engine.__file__).parent.parent),
                                     str(args.fixture.resolve()), str(root / 'base-edited.blend'), str(root)],
                                    stdout=log, stderr=subprocess.STDOUT, timeout=180)
        assert result.returncode == 0, 'PyMOL bridge failed; see pymol.log'
        assert (root / 'pymol.png').is_file()
        evidence['real_pymol_bridge'] = True
        try:
            save_from_blend(root / 'unsupported.blend', 'user/unsupported', report=root / 'unsupported-report.json')
        except ValueError as exc:
            assert 'No preset saved' in str(exc)
            assert not (home / 'presets/user/unsupported.json').exists()
            evidence['unsupported_rejected'] = True
        else:
            raise AssertionError('Geometry edits were silently accepted')
        if args.open_blender:
            opened = render_file(args.fixture, output=root / 'desktop.png',
                                 options={'preset': 'user/base', 'width': 192, 'height': 144, 'samples': 4}, open_blender=True)
            assert opened['editor']['status'] == 'launched'
            evidence['desktop_launch'] = opened['editor']
        evidence['passed'] = True
    finally:
        warm_worker.stop(force=True)
        evidence_path.write_text(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
