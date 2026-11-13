"""A single authored change must reach every independently packaged consumer."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from sync_render_defaults import STUDIO, compile_defaults, synchronize


def test_generated_defaults_and_schema_are_current():
    synchronize(check=True)


@pytest.mark.parametrize(('section', 'key', 'value'), [
    ('animation', 'fps', True),
    ('animation', 'fps', 0),
    ('animation', 'fps', 61),
    ('image', 'unknown_setting', 12),
])
def test_invalid_authored_defaults_fail_before_packaging(tmp_path, section, key, value):
    data = compile_defaults()
    data[section][key] = value
    (tmp_path / 'render_defaults.yaml').write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        compile_defaults(tmp_path)


def test_changed_yaml_reaches_renderer_pymol_and_studio_without_losing_overrides(tmp_path):
    for package in ('blender_render', 'co_pymol'):
        shutil.copytree(ROOT / package, tmp_path / package,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(ROOT / STUDIO / 'scripts', tmp_path / STUDIO / 'scripts',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for relative in ('assets/molecular-data.json', 'references/presets.json'):
        target = tmp_path / STUDIO / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / STUDIO / relative, target)
    data = compile_defaults()
    data['image']['preset'] = 'northern_nights'
    data['image']['preview'].update(width=920, samples=27)
    data['image']['cpu'].update(long_edge=1000, samples=40)
    data['animation']['fps'] = 20
    data['animation']['preview'].update(width=720, samples=29)
    data['animation']['recipes']['rock'].update(duration=8, rock_angle=22)
    data['animation']['orbits']['rock'].update(duration=4, rock_angle=15)
    data['limits']['animation'].update(max_frames=400, max_rgba_bytes=12 * 1024**3,
                                       max_archive_bytes=640 * 1024**2)
    (tmp_path / 'render_defaults.yaml').write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match='Stale'):
        synchronize(tmp_path, check=True)
    synchronize(tmp_path)
    synchronize(tmp_path, check=True)
    script = r'''
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from co_pymol import cli
from co_pymol.scene import animation as pymol_animation
assert not any(name == 'blender_render' or name.startswith('blender_render.') for name in sys.modules)
assert 'yaml' not in sys.modules
still = cli.build_parser().parse_args(['render', 'example.pse'])
movie = cli.build_parser().parse_args(['animate', 'example.pse'])
assert (still.width, still.height, still.samples) == (920, 0, 27)
assert (movie.width, movie.fps, movie.samples) == (720, 20, 29)
assert cli.DEFAULT_PRESET == 'northern_nights'
assert pymol_animation.RECIPE_DEFAULTS['rock'] == {'duration': 8, 'rock_angle': 22}
view = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -50, 0, 0, 0, 40, 60, 0)
assert len(pymol_animation.frames_for('rock', pymol_animation.Keys(overview=view))) == 160

from blender_render import animation, core
from blender_render.scene import export
from blender_render.cluster.config import Limits
from blender_render.spec import resolve_options
resolved = resolve_options({'animation': 'rock', 'width': 160, 'height': 120}, {})
assert resolved['preset'] == 'northern_nights'
assert resolved['style_doc']['output']['animation'] == {
    'mode': 'rock', 'duration': 4, 'fps': 20, 'rock_angle': 15, 'gif': False}
explicit = resolve_options({'animation': 'rock', 'width': 180, 'height': 130,
                            'duration': 2, 'fps': 8, 'samples': 13, 'rock_angle': 18}, {})
output = explicit['style_doc']['output']
assert (output['width'], output['height'], output['samples']) == (180, 130, 13)
assert output['animation']['duration'] == 2 and output['animation']['fps'] == 8
assert output['animation']['rock_angle'] == 18
assert animation.MAX_RGBA == 12 * 1024**3 and animation.MAX_ARCHIVE == 640 * 1024**2
assert animation.MAX_FRAMES == export.MAX_STATES == Limits().max_frames == 400
assert core._state_coordinate_errors({'base': 1, 'coordinates': [[0, 0, 0]] * 400}) == []
assert animation.normalize({'mode': 'path', 'fps': 40, 'path': [list(view)] * 400},
                           {'width': 10, 'height': 10})['duration'] == 10
schema = json.loads((root / 'blender_render/style.schema.json').read_text())
assert schema['properties']['output']['properties']['animation']['properties']['path']['maxItems'] == 400

sys.path.insert(0, str(root / 'plugins/structure-studio/skills/structure-studio/scripts'))
import studio
from studio import animation_recipes
assert len(animation_recipes.frames_for('rock', animation_recipes.Keys(overview=view))) == 160
template = animation_recipes.template({'version': 1, 'recipe': 'rock'})
assert template['fps'] == 20 and template['rock_angle'] == 22
explicit = animation_recipes.template({'version': 1, 'recipe': 'rock', 'fps': 8, 'rock_angle': 17})
assert explicit['fps'] == 8 and explicit['rock_angle'] == 17
studio.studio_env.doctor = lambda: {'gpu_likely': False}
args = studio.parser().parse_args(['render', 'x.cif', '--preset', 'mint_sky_assembly'])
assert studio.cpu_defaults(args)['width'] == 1000
assert studio.cpu_defaults(args)['samples'] == 40
args.full_quality = True
assert studio.cpu_defaults(args) == {}
assert studio.ANIMATION['preview']['samples'] == 29
assert 'yaml' not in sys.modules
'''
    subprocess.run([sys.executable, '-I', '-c', script, str(tmp_path)], check=True,
                   capture_output=True, text=True, cwd=tmp_path)
