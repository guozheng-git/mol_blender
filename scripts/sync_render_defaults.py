"""Generate stdlib-readable defaults from render_defaults.yaml; --check detects drift.

Only this build/authoring tool needs PyYAML. Runtime clients read JSON, and the
renderer captures that JSON with its other immutable engine inputs.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NUMBER = (int, float)
PREVIEW = {'width': int, 'height': int, 'samples': int}
SHAPE = {
    'version': int,
    'image': {
        'preset': str, 'cutaway_preset': str, 'preview': PREVIEW,
        'cpu': {'long_edge': int, 'samples': int},
        'rough': {key: int for key in ('scale', 'min_width', 'min_height', 'min_samples', 'max_samples')},
    },
    'animation': {
        'fps': int, 'gif': bool, 'preview': PREVIEW,
        'orbits': {'rock': {'duration': NUMBER, 'rock_angle': NUMBER}, '360': {'duration': NUMBER}},
        'recipes': {
            'turntable': {'duration': NUMBER},
            'rock': {'duration': NUMBER, 'rock_angle': NUMBER},
            'reveal': {key: NUMBER for key in ('overview_hold', 'approach', 'focus_hold', 'rock_duration', 'rock_angle')},
            'cutaway': {key: NUMBER for key in ('overview_hold', 'approach', 'before_fade_hold', 'fade_duration',
                                              'after_fade_hold', 'rock_duration', 'rock_angle', 'pymol_shell_transparency')},
            'states': {key: NUMBER for key in ('min_duration', 'seconds_per_state', 'rock_angle')},
            'scenes': {'hold_duration': NUMBER, 'transition_duration': NUMBER},
        },
    },
    'limits': {'animation': {key: int for key in ('min_frames', 'max_frames', 'max_fps', 'max_duration',
                                                'max_rock_angle', 'max_rgba_bytes', 'max_archive_bytes')}},
}


def validate(value, shape, path='defaults'):
    if isinstance(shape, dict):
        if not isinstance(value, dict) or value.keys() != shape.keys():
            raise ValueError(f'{path}: expected fields {", ".join(shape)}')
        for key, expected in shape.items():
            validate(value[key], expected, f'{path}.{key}')
    elif type(value) not in (shape if isinstance(shape, tuple) else (shape,)):
        raise ValueError(f'{path}: incorrect value type')
    elif type(value) in NUMBER and (not math.isfinite(value) or value < 0 or (value == 0 and not path.endswith('.height'))):
        raise ValueError(f'{path}: expected a positive finite value (height may be zero)')


def compile_defaults(root=ROOT):
    value = yaml.safe_load((root / 'render_defaults.yaml').read_text())
    validate(value, SHAPE)
    limits, animation = value['limits']['animation'], value['animation']
    if value['version'] != 1 or not limits['min_frames'] <= limits['max_frames']:
        raise ValueError('Invalid defaults version or frame bounds')
    if animation['fps'] > limits['max_fps']:
        raise ValueError('Default FPS exceeds the animation limit')
    if not 0 < animation['recipes']['cutaway']['pymol_shell_transparency'] <= 1:
        raise ValueError('PyMOL shell transparency must be in (0, 1]')
    return value


def generated_files(root=ROOT):
    value = compile_defaults(root)
    data = (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()
    # The one runtime copy. co-pymol and Structure Studio (via its bundled wheel) read it too.
    files = {Path('blender_render/render_defaults.json'): data}
    # The published JSON schema enforces the same bounds as the runtime.
    schema_path = Path('blender_render/style.schema.json')
    schema = json.loads((root / schema_path).read_text())
    properties = schema['properties']['output']['properties']['animation']['properties']
    limits = value['limits']['animation']
    for key in ('path', 'states', 'lid'):
        properties[key].update(minItems=limits['min_frames'], maxItems=limits['max_frames'])
    properties['anchor']['maximum'] = limits['max_frames'] - 1
    properties['states']['items']['maximum'] = limits['max_frames']
    for key in ('fps', 'duration', 'rock_angle'):
        properties[key]['maximum'] = limits['max_' + key]
    files[schema_path] = (json.dumps(schema, indent=2) + '\n').encode()
    return files


def synchronize(root=ROOT, *, check=False):
    for relative, data in generated_files(root).items():
        path = root / relative
        if check:
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError(f'Stale {relative}; run python scripts/sync_render_defaults.py')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    synchronize(check=parser.parse_args().check)
