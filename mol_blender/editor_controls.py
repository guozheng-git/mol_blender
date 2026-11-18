# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit editable controls on existing mol-blender scenes (Blender only)."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import re

import bpy
from .scene import capture
from .editor_protocol import MAX_CHANGES, checked_value


def metadata():
    text = bpy.data.texts.get(capture.TEXT)
    if not text or len(text.as_string().encode()) > capture.LIMIT:
        raise ValueError('This scene needs mol-blender editing metadata; render it with --save-blend first')
    value = json.loads(text.as_string())
    checksum = value.pop('sha256')
    if value.get('format') != 1 or capture.digest(value) != checksum:
        raise ValueError('Scene editing metadata changed or is unsupported')
    return value


def revision(scene, data):
    from .editor_interaction import desktop_state
    bpy.context.view_layer.update()
    return capture.digest({'scene': scene.name, 'file': bpy.data.filepath,
        'frame': scene.frame_current, 'metadata': capture.digest(data), 'state': capture.snapshot(scene),
        'desktop': desktop_state(scene)})


def schema_for(path):
    document = json.loads(Path(__file__).with_name('style.schema.json').read_text())
    schema = document
    def resolve(value):
        if '$ref' in value:
            keys = value['$ref'].removeprefix('#/').split('/')
            value = document
            for key in keys:
                value = value[key]
        return value
    for key in path:
        schema = resolve(schema)
        if isinstance(key, int):
            schema = schema.get('items', {})
        else:
            schema = schema.get('properties', {}).get(key, schema.get('additionalProperties', {}))
        if not isinstance(schema, dict):
            return {}
    return resolve(schema)


class Control:
    def __init__(self, name, target, attribute, spec, *, kind='value', label='', style_path=None):
        self.name, self.target, self.attribute, self.spec = name, target, attribute, spec
        self.kind, self.label, self.style_path = kind, label or name, style_path

    def raw(self):
        return capture.plain(getattr(self.target, self.attribute))

    def value(self):
        value = self.raw()
        if self.kind in ('hex', 'rgb', 'gray'):
            return capture.convert(value, self.kind)
        if self.kind == 'degrees':
            return [math.degrees(x) for x in value]
        return value

    def set_raw(self, value):
        setattr(self.target, self.attribute, value)

    def set(self, value):
        value = checked_value(value, self.spec)
        if self.kind == 'hex':
            value = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)] + ([1] if len(self.raw()) == 4 else [])
        elif self.kind == 'rgb':
            value = value + ([1] if len(self.raw()) == 4 else [])
        elif self.kind == 'gray':
            value = [value] * 3 + ([1] if len(self.raw()) == 4 else [])
        elif self.kind == 'degrees':
            value = [math.radians(x) for x in value]
        self.set_raw(value)


def specification(target, attribute, kind='value', path=None):
    value = capture.plain(getattr(target, attribute))
    schema = schema_for(path) if path else {}
    if kind == 'hex':
        return {'type': 'color'}
    if kind == 'gray':
        return {'type': 'number', 'minimum': 0, 'maximum': 1}
    if kind == 'rgb':
        return {'type': 'vector', 'length': 3, 'minimum': 0, 'maximum': 1}
    prop = target.bl_rna.properties.get(attribute)
    if type(value) is bool:
        return {'type': 'boolean'}
    if isinstance(value, str):
        choices = schema.get('enum') or [i.identifier for i in prop.enum_items]
        return {'type': 'enum', 'choices': choices}
    lower, upper = getattr(prop, 'hard_min', -1000000), getattr(prop, 'hard_max', 1000000)
    if hasattr(target, 'min_value'):
        lower, upper = target.min_value, target.max_value
    lower, upper = max(lower, schema.get('minimum', -1000000)), min(upper, schema.get('maximum', 1000000))
    lower, upper = max(lower, -1000000), min(upper, 1000000)
    if isinstance(value, list):
        return {'type': 'vector', 'length': len(value), 'minimum': lower, 'maximum': upper}
    return {'type': 'integer' if type(value) is int else 'number', 'minimum': lower, 'maximum': upper}


def controls(scene, data):
    """Resolve only known capture bindings. No eval, RNA paths or arbitrary attributes."""
    trees = {capture.ident(mat.node_tree): mat.node_tree for mat in bpy.data.materials if mat.node_tree}
    if scene.world and scene.world.node_tree:
        trees[capture.ident(scene.world.node_tree)] = scene.world.node_tree
    allowed = {'render': {'resolution_x', 'resolution_y', 'film_transparent'},
               'cycles': {'samples', 'use_denoising', 'seed', 'use_adaptive_sampling', 'adaptive_threshold'},
               'view': {'exposure', 'gamma', 'view_transform', 'look'}}
    owners = {'render': scene.render, 'cycles': scene.cycles, 'view': scene.view_settings}
    result = {}
    for key, binding in data['bindings'].items():
        parts = key.split('/')
        target = attribute = None
        if len(parts) == 2 and parts[0] in allowed and parts[1] in allowed[parts[0]]:
            target, attribute = owners[parts[0]], parts[1]
        elif len(parts) in (5, 6) and parts[0] == 'tree' and parts[1] in trees:
            tree = trees[parts[1]]
            node = next((n for n in tree.nodes if capture.ident(n) == parts[2]), None)
            if node and parts[3] == 'input' and len(parts) == 5:
                index = int(parts[4])
                if 0 <= index < len(node.inputs) and not node.inputs[index].is_linked and hasattr(node.inputs[index], 'default_value'):
                    target, attribute = node.inputs[index], 'default_value'
            elif node and parts[3] == 'ramp' and len(parts) == 6 and parts[5] == 'color':
                index = int(parts[4])
                if node.color_ramp and 0 <= index < len(node.color_ramp.elements):
                    target, attribute = node.color_ramp.elements[index], 'color'
        if target is not None:
            path = binding['path']
            name = '/'.join(str(x).replace('~', '~0').replace('/', '~1') for x in path)
            result.setdefault(name, []).append(Control(name, target, attribute,
                specification(target, attribute, binding['kind'], path), kind=binding['kind'], style_path=path))

    def add(name, target, attribute, *, kind='value', spec=None, label=''):
        result[name] = [Control(name, target, attribute, spec or specification(target, attribute, kind), kind=kind, label=label)]

    for obj in scene.objects:
        if obj.type != 'LIGHT' or capture.ident(obj) not in data['lights']:
            continue
        prefix = 'light/' + capture.ident(obj) + '/'
        for field, attr in [('power', 'energy'), ('size', 'size'), ('color', 'color')]:
            if hasattr(obj.data, attr):
                add(prefix + field, obj.data, attr, kind='hex' if field == 'color' else 'value',
                    spec={'type': 'number', 'minimum': 0, 'maximum': 1000000} if field != 'color' else None,
                    label=obj.name + ': ' + field)
        add(prefix + 'location', obj, 'location', label=obj.name + ': location (Blender units)')
        add(prefix + 'rotation_deg', obj, 'rotation_euler', kind='degrees', spec={'type': 'vector', 'length': 3, 'minimum': -360, 'maximum': 360})
    camera = scene.camera
    if camera and capture.ident(camera) == data['camera']:
        add('camera/location', camera, 'location', label='Camera location (Blender units)')
        add('camera/rotation_deg', camera, 'rotation_euler', kind='degrees', spec={'type': 'vector', 'length': 3, 'minimum': -360, 'maximum': 360})
        for field, attr in [('projection', 'type'), ('lens_mm', 'lens'), ('ortho_scale', 'ortho_scale'),
                            ('shift_x', 'shift_x'), ('shift_y', 'shift_y')]:
            add('camera/' + field, camera.data, attr,
                spec={'type': 'enum', 'choices': ['ORTHO', 'PERSP']} if field == 'projection' else None)
        for field, attr in [('enabled', 'use_dof'), ('focus_distance', 'focus_distance'), ('f_stop', 'aperture_fstop')]:
            add('camera/dof/' + field, camera.data.dof, attr)
    from .editor_interaction import append_controls
    append_controls(scene, data, result, Control)
    return result


def inspect(scene):
    data = metadata()
    current = capture.snapshot(scene)
    exported = capture.extract(scene, data, current, 'preserve_input')
    items = []
    for name, group in controls(scene, data).items():
        try:
            values = [c.value() for c in group]
            items.append({'id': name, 'label': group[0].label, **group[0].spec,
                          'value': values[0], 'mixed': any(capture.changed(values[0], x) for x in values[1:])})
        except ValueError:
            continue
    from .editor_interaction import views
    return {'revision': revision(scene, data), 'scene': scene.name, 'file': bpy.data.filepath,
            'dirty': bpy.data.is_dirty, 'frame': scene.frame_current,
            'playing': any(w.scene == scene and w.screen.is_animation_playing for w in bpy.context.window_manager.windows), 'controls': items,
            'animation_loops': [item['id'] for item in items if item['id'].startswith('animation/loop/')],
            'viewport_count': sum(1 for _ in views(scene)),
            'unsupported': exported['unsupported'], 'ignored': exported['ignored']}


def record_edit(result, label):
    # Direct RNA assignments do not set bpy.data.is_dirty. Record a native undo
    # step so closing the window also offers Blender's normal save warning.
    if 'FINISHED' not in bpy.ops.ed.undo_push(message=label):
        raise RuntimeError('Blender could not record this edit in its undo history')
    result['dirty'] = bpy.data.is_dirty
    return result


def edit(scene, changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_CHANGES:
        raise ValueError('Supply 1 to 64 explicit control changes')
    available = controls(scene, metadata())
    pending, seen = [], set()
    for item in changes:
        if not isinstance(item, dict) or set(item) != {'control', 'value'}:
            raise ValueError('Each change needs only control and value')
        key = item['control']
        if not isinstance(key, str) or key not in available or key in seen:
            raise ValueError('Unknown or repeated control; inspect the scene again')
        seen.add(key)
        for control in available[key]:
            value = checked_value(item['value'], control.spec)
            pending.append((control, value, control.raw()))
    try:
        for control, value, _ in pending:
            control.set(value)
            if capture.changed(control.value(), value):
                raise ValueError('Blender clamped or rejected the control: ' + control.name)
        bpy.context.view_layer.update()
        for screen in bpy.data.screens:
            for area in screen.areas:
                area.tag_redraw()
        result = record_edit(inspect(scene), 'mol-blender chat edit')
    except BaseException:
        for control, _, old in reversed(pending):
            control.set_raw(old)
        bpy.context.view_layer.update()
        raise
    return ({**result, 'applied': [{'control': c.name, 'value': value} for c, value, _ in pending]},
            [{'control': c.name, 'index': available[c.name].index(c), 'value': old} for c, _, old in pending])


def restore(scene, before):
    # Resolve fresh datablocks: a manual Blender undo/redo may replace RNA objects.
    available = controls(scene, metadata())
    pending = [(available[item['control']][item['index']], item['value']) for item in before]
    rollback = [(control, control.raw()) for control, _ in pending]
    try:
        for control, value in pending:
            control.set_raw(value)
        bpy.context.view_layer.update()
        return record_edit(inspect(scene), 'Undo mol-blender chat edit')
    except BaseException:
        for control, value in reversed(rollback):
            control.set_raw(value)
        bpy.context.view_layer.update()
        raise
