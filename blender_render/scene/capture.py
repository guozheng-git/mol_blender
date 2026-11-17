# SPDX-License-Identifier: GPL-3.0-or-later
"""Data-only scene provenance and supported Blender-to-style mappings.

Imported only in Blender. No scripts are stored in .blend files. Unmapped changes
to the captured render state are reported instead of silently discarded.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from blender_render.scene.edit import apply_bindings, changed, convert

TEXT = 'Blender Render - editable preset.json'
TAG = 'blender_render_edit_id'
LIMIT = 8 * 1024 * 1024
UI_PROPERTIES = {'rna_type', 'name', 'name_full', 'label', 'location', 'location_absolute', 'width', 'height',
                 'dimensions', 'select', 'show_options', 'show_preview', 'show_texture',
                 'hide', 'use_custom_color', 'color', 'warning_propagation', 'bl_idname',
                 'bl_label', 'bl_description', 'bl_icon', 'bl_static_type', 'type', 'bl_width_default',
                 'bl_width_min', 'bl_width_max', 'bl_height_default', 'bl_height_min', 'bl_height_max'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def ident(block):
    return block.get(TAG, 'added:' + block.name) if block else None


def plain(value):
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bpy.types.ID):
        return ident(value)
    if isinstance(value, set):
        return sorted(value)
    try:
        return [plain(x) for x in value]
    except TypeError:
        return str(value)


def properties(value, excluded=()):
    """Writable scalar RNA state only; pointer/collection state is handled below."""
    result = {}
    for prop in value.bl_rna.properties:
        key = prop.identifier
        if prop.is_readonly or key in excluded or key in {'rna_type', 'name', 'name_full'}:
            continue
        if prop.type in {'BOOLEAN', 'INT', 'FLOAT', 'STRING', 'ENUM'}:
            result[key] = plain(getattr(value, key))
    return result


def tree_state(tree, seen=None):
    if tree is None:
        return {}
    seen = set(seen or ())
    if tree.as_pointer() in seen:
        return {'recursive': ident(tree)}
    seen.add(tree.as_pointer())
    if len({ident(node) for node in tree.nodes}) != len(tree.nodes):
        raise ValueError('Duplicated shader or geometry nodes cannot be exported as a preset')
    values = {}
    for node in tree.nodes:
        prefix = ident(node)
        values[prefix + '/type'] = node.bl_idname
        for key, value in properties(node, UI_PROPERTIES).items():
            values[prefix + '/property/' + key] = value
        for index, socket in enumerate(node.inputs):
            if hasattr(socket, 'default_value'):
                values[prefix + '/input/' + str(index)] = plain(socket.default_value)
        # RGB/Value nodes use output defaults rather than input sockets.
        if node.type in ('RGB', 'VALUE'):
            values[prefix + '/output'] = plain(node.outputs[0].default_value)
        ramp = getattr(node, 'color_ramp', None)
        if ramp:
            values[prefix + '/ramp/settings'] = properties(ramp)
            values[prefix + '/ramp/count'] = len(ramp.elements)
            for index, element in enumerate(ramp.elements):
                values[prefix + '/ramp/' + str(index) + '/position'] = element.position
                values[prefix + '/ramp/' + str(index) + '/color'] = list(element.color)
        group = getattr(node, 'node_tree', None)
        if group:
            values[prefix + '/group'] = tree_state(group, seen)
        image = getattr(node, 'image', None)
        if image:
            values[prefix + '/image'] = {'file': image.filepath, 'size': list(image.size),
                                       'colorspace': image.colorspace_settings.name}
    values['links'] = sorted([ident(link.from_node), list(link.from_node.outputs).index(link.from_socket),
                              ident(link.to_node), list(link.to_node.inputs).index(link.to_socket)] for link in tree.links)
    return values


def geometry_digest(mesh):
    import numpy as np
    result = {}
    # Include atom coordinates/colors/secondary structure as well as mesh topology.
    for attr in mesh.attributes:
        if attr.name.startswith(('.select', '.hide', '.uv_select')):
            continue
        field, size = {'FLOAT_VECTOR': ('vector', 3), 'FLOAT_COLOR': ('color', 4),
                       'BYTE_COLOR': ('color', 4), 'FLOAT2': ('vector', 2),
                       'INT32_2D': ('value', 2)}.get(attr.data_type, ('value', 1))
        array = np.empty(len(attr.data) * size, dtype=np.float64)
        try:
            attr.data.foreach_get(field, array)
            result[attr.name] = [attr.domain, attr.data_type, hashlib.sha256(array.tobytes()).hexdigest()]
        except (TypeError, ValueError, RuntimeError):
            result[attr.name] = [attr.domain, attr.data_type, [str(x) for x in attr.data]]
    result['counts'] = [len(mesh.vertices), len(mesh.edges), len(mesh.polygons)]
    if mesh.shape_keys:
        result['shape_keys'] = [block.name for block in mesh.shape_keys.key_blocks]
        for block in mesh.shape_keys.key_blocks:
            coords = np.empty(len(block.data) * 3)
            block.data.foreach_get('co', coords)
            result['shape:' + block.name] = [block.value, hashlib.sha256(coords.tobytes()).hexdigest()]
    return digest(result)


def animation_state(block):
    data = getattr(block, 'animation_data', None)
    if data is None:
        return None
    # Adding animation is unsupported, even when its current frame looks the same.
    action = data.action
    return {'action': action.name if action else None, 'drivers': len(data.drivers),
            'tracks': len(data.nla_tracks)}


def snapshot(scene):
    state = {}
    for blocks in (list(scene.objects), list(bpy.data.materials)):
        if len({ident(block) for block in blocks}) != len(blocks):
            raise ValueError('Duplicated scene objects or materials cannot be exported as a preset')

    def fields(prefix, values):
        state.update({prefix + '/' + key: value for key, value in values.items()})

    fields('render', properties(scene.render, {'filepath', 'use_lock_interface'}))
    fields('image', properties(scene.render.image_settings))
    fields('view', properties(scene.view_settings))
    fields('cycles', properties(scene.cycles, {'device', 'preview_samples', 'use_preview_denoising'}))
    state['scene/camera'] = ident(scene.camera)
    state['scene/animation'] = animation_state(scene)
    compositor = getattr(scene, 'compositing_node_group', None)
    state['scene/compositor'] = tree_state(compositor)
    for mat in bpy.data.materials:
        if not mat.users and not mat.use_fake_user:
            continue
        fields('material/' + ident(mat), properties(mat, {'diffuse_color', 'roughness', 'metallic', 'specular_intensity'}))
        fields('tree/' + ident(mat.node_tree), tree_state(mat.node_tree)) if mat.node_tree else None
        state['material/' + ident(mat) + '/animation'] = animation_state(mat)
    if scene.world:
        fields('world', properties(scene.world, {'color'}))
        fields('tree/' + ident(scene.world.node_tree), tree_state(scene.world.node_tree))
    for obj in scene.objects:
        prefix = 'object/' + ident(obj)
        fields(prefix, {'type': obj.type, 'matrix': plain(obj.matrix_world), 'hide_render': obj.hide_render,
                        'parent': ident(obj.parent), 'animation': animation_state(obj),
                        'constraints': [properties(c) for c in obj.constraints],
                        'materials': [ident(slot.material) for slot in obj.material_slots]})
        if obj.type == 'MESH':
            state[prefix + '/geometry'] = geometry_digest(obj.data)
        elif obj.type in ('CAMERA', 'LIGHT'):
            fields(prefix + '/data', properties(obj.data))
            state[prefix + '/data/animation'] = animation_state(obj.data)
            if obj.type == 'CAMERA':
                fields(prefix + '/dof', properties(obj.data.dof))
                state[prefix + '/dof/focus_object'] = ident(obj.data.dof.focus_object)
            elif obj.data.use_nodes:
                fields(prefix + '/nodes', tree_state(obj.data.node_tree))
        else:
            state[prefix + '/unsupported_data'] = properties(obj.data) if obj.data else None
        modifiers = []
        for mod in obj.modifiers:
            values = properties(mod)
            if mod.type == 'NODES':
                values['group'] = tree_state(mod.node_group)
                values['inputs'] = {key: plain(mod[key]) for key in mod.keys() if not key.startswith('_')}
            modifiers.append(values)
        state[prefix + '/modifiers'] = digest(modifiers)
    return state


def tag_tree(tree, prefix, seen):
    if tree is None or tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    tree[TAG] = prefix
    for index, node in enumerate(tree.nodes):
        node[TAG] = 'n' + str(index)
        tag_tree(getattr(node, 'node_tree', None), prefix + '-g' + str(index), seen)


def material_bindings(bindings, material, name, cfg):
    tree = material.node_tree
    if tree is None:
        return
    prefix = ['materials', name]

    def socket(node, socket_name, field, kind='value'):
        if node is None:
            return
        value = node.inputs.get(socket_name) if isinstance(socket_name, str) else node.inputs[socket_name]
        if value is not None and not value.is_linked:
            key = 'tree/' + ident(tree) + '/' + ident(node) + '/input/' + str(list(node.inputs).index(value))
            bindings[key] = {'path': prefix + field.split('.'), 'kind': kind}

    def ramp(node, index, field, kind='hex'):
        key = 'tree/' + ident(tree) + '/' + ident(node) + '/ramp/' + str(index) + '/color'
        bindings[key] = {'path': prefix + field, 'kind': kind}

    bsdf = next((node for node in tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)
    if cfg.get('paint'):
        for node_name, input_name, field in (
            ('Gentler texture on connecting loops', 'To Min', 'loop_relief'),
            ('Rounded painted edge normals', 'Radius', 'edge_normal_radius'),
            ('Broad irregular paint deposition', 'Scale', 'coarse_scale'),
            ('Short brushed paint ridges', 1, 'brush_stretch'),
            ('Dry paint wrinkles', 'Scale', 'paint_scale'),
            ('Dry paint wrinkles', 'Detail', 'paint_detail'),
            ('Dry paint wrinkles', 'Distortion', 'paint_distortion'),
            ('Fine pigment stipple', 'Scale', 'micro_scale'),
            ('Subtle thick paint undulation', 'Distance', 'coarse_distance'),
            ('Dried paint surface relief', 'Distance', 'paint_distance'),
            ('Pigment grain relief', 'Distance', 'micro_distance'),
            ('Relief strength by content 0', 1, 'coarse_strength'),
            ('Relief strength by content 1', 1, 'paint_strength'),
            ('Relief strength by content 2', 1, 'micro_strength'),
            ('Uneven dry satin roughness', 'To Min', 'roughness_min'),
            ('Uneven dry satin roughness', 'To Max', 'roughness_max'),
        ):
            socket(tree.nodes.get(node_name), input_name, 'paint.' + field)
        for input_name, field in (('Specular IOR Level', 'specular'), ('Coat Weight', 'coat'),
                                  ('Coat Roughness', 'coat_roughness')):
            socket(bsdf, input_name, 'paint.' + field)
        socket(tree.nodes.get('Forest green pigment tint'), 2, 'paint.dark_paint_tint', 'rgb')
        ramp(tree.nodes['Subtle uneven pigment density'], 0, ['paint', 'pigment_min'], 'gray')
        return
    for input_name, field, kind in (
        ('Base Color', 'base_color', 'hex'), ('Roughness', 'roughness', 'value'),
        ('Specular IOR Level', 'specular', 'value'), ('Transmission Weight', 'transmission', 'value'),
        ('Coat Weight', 'coat', 'value'), ('Alpha', 'alpha', 'value'),
        ('Emission Color', 'emission_color', 'hex'), ('Emission Strength', 'emission_strength', 'value'),
    ):
        socket(bsdf, input_name, field, kind)
    for node_name, input_name, field, kind in (
        ('Clay shadow', 1, 'base_color', 'hex'), ('Clay shadow', 2, 'shadow_color', 'hex'),
        ('Clay highlight', 2, 'highlight_color', 'hex'), ('Clay AO strength', 1, 'ao_strength', 'value'),
        ('Clay AO', 'Distance', 'ao_distance', 'value'), ('Clay rim blend', 'Blend', 'rim_blend', 'value'),
        ('Clay rim power', 1, 'rim_power', 'value'), ('Clay rim strength', 1, 'rim_strength', 'value'),
        ('Frosted shell patches', 'Scale', 'frosted.noise_scale', 'value'),
        ('Frosted grid pigment', 1, 'base_color', 'hex'),
        ('Frosted grid pigment', 2, 'frosted.grid_color', 'hex'),
        ('Frosted base opacity', 1, 'alpha', 'value'),
    ):
        socket(tree.nodes.get(node_name), input_name, field, kind)
    gradient = tree.nodes.get('Reference color gradient')
    if gradient:
        for index in range(len(gradient.color_ramp.elements)):
            ramp(gradient, index, ['color_gradient', 'colors', index])
    opacity = tree.nodes.get('Frosted shell opacity')
    if opacity:
        for index, field in enumerate(('opacity_min', 'opacity_max')):
            ramp(opacity, index, ['frosted', field], 'gray')


def capture(scene, style, materials, target, radius):
    seen = set()
    # Keep all preset materials, including ones unused by this particular
    # structure. Otherwise Blender drops them on save and creates false edits.
    for material in materials.values():
        material.use_fake_user = True
    for index, obj in enumerate(scene.objects):
        obj[TAG] = 'o' + str(index)
        if obj.data:
            obj.data[TAG] = 'd' + str(index)
        for index_mod, mod in enumerate(obj.modifiers):
            if mod.type == 'NODES':
                tag_tree(mod.node_group, ident(obj) + '-mod' + str(index_mod), seen)
    for index, mat in enumerate(bpy.data.materials):
        mat[TAG] = 'm' + str(index)
        tag_tree(mat.node_tree, ident(mat), seen)
    if scene.world:
        scene.world[TAG] = 'world'
        tag_tree(scene.world.node_tree, 'world', seen)
    tag_tree(getattr(scene, 'compositing_node_group', None), 'compositor', seen)
    bindings = {}
    for key, field in (
        ('render/resolution_x', 'output.width'), ('render/resolution_y', 'output.height'),
        ('cycles/samples', 'output.samples'), ('render/engine', 'renderer.engine'),
        ('render/film_transparent', 'renderer.transparent_background'),
        ('view/exposure', 'renderer.exposure'), ('view/gamma', 'renderer.gamma'),
        ('view/view_transform', 'renderer.view_transform'), ('view/look', 'renderer.look'),
        ('cycles/use_denoising', 'renderer.denoise'), ('cycles/seed', 'renderer.seed'),
        ('cycles/use_adaptive_sampling', 'renderer.adaptive_sampling'),
        ('cycles/adaptive_threshold', 'renderer.adaptive_threshold'),
    ):
        bindings[key] = {'path': field.split('.'), 'kind': 'value'}
    for name, cfg in style['materials'].items():
        if name in materials:
            material_bindings(bindings, materials[name], name, cfg)
            variants = [key for key in materials if key not in style['materials'] and key.startswith(name + '__chain_')]
            for variant in variants:
                material_bindings(bindings, materials[variant], name, cfg)
            if variants and not cfg.get('paint'):
                # Chain palette colors are derived, not an editable uniform
                # pigment. Other fields must agree across the generated copies.
                bindings = {key: value for key, value in bindings.items()
                            if not (value['path'][:2] == ['materials', name] and value['kind'] == 'hex')}
    if style.get('color_follow', {}).get('enabled'):
        # PyMOL colors are resolved again on the next structure; do not pretend
        # that edits to their derived pigment sockets override color-follow.
        bindings = {key: value for key, value in bindings.items()
                    if not (value['path'][0] == 'materials' and value['kind'] == 'hex')}
    if scene.world and scene.world.node_tree:
        tree = scene.world.node_tree
        for name, field in (('Background color', ''), ('Ambient illumination', 'ambient_')):
            node = tree.nodes.get(name)
            if node:
                for index, suffix, kind in ((0, 'color', 'hex'), (1, 'strength', 'value')):
                    if not node.inputs[index].is_linked:
                        bindings['tree/' + ident(tree) + '/' + ident(node) + '/input/' + str(index)] = {
                            'path': ['world', field + suffix], 'kind': kind}
        ramp = tree.nodes.get('Background gradient')
        if ramp:
            for index, field in enumerate(('bottom_color', 'top_color')):
                bindings['tree/' + ident(tree) + '/' + ident(ramp) + '/ramp/' + str(index) + '/color'] = {
                    'path': ['world', field], 'kind': 'hex'}
    from blender_render.engine import engine_identity
    metadata = {'format': 1, 'style': copy.deepcopy(style), 'bindings': bindings,
                'baseline': snapshot(scene), 'camera': ident(scene.camera),
                'lights': {ident(obj): obj['blender_render_light_index'] for obj in scene.objects
                           if obj.type == 'LIGHT' and 'blender_render_light_index' in obj},
                'target': list(target), 'radius': max(float(radius), 1e-6),
                'source_engine': engine_identity()['engine_build']}
    metadata['sha256'] = digest(metadata)
    data = json.dumps(metadata, allow_nan=False)
    if len(data.encode()) > LIMIT:
        raise ValueError('Scene preset metadata exceeds the 8 MiB limit')
    text = bpy.data.texts.get(TEXT) or bpy.data.texts.new(TEXT)
    text.clear()
    text.write(data)
    help_text = bpy.data.texts.get('Blender Render - editing guide') or bpy.data.texts.new('Blender Render - editing guide')
    help_text.clear()
    help_text.write('Edit materials, lights, world and render settings, then File > Save As to a new .blend.\n'
                    'Export locally: blender-render dev from-blend edited.blend --preset user/my-look\n'
                    'Use --camera-policy authored to include the Blender camera. Default: preserve each input pose.\n'
                    'Unsupported geometry or shader graph edits are reported; keep the .blend for those changes.\n'
                    'This file contains data, not an auto-running add-on. Do not edit the preset metadata text.\n')
    # The first view should show the authored composition, not the default cube view.
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.region_3d.view_perspective = 'CAMERA'


def camera_style(scene, metadata):
    camera = scene.camera
    matrix = camera.matrix_world
    if any(abs(value - 1) > 1e-5 for value in matrix.to_scale()):
        raise ValueError('Scaled cameras cannot be captured; apply the camera scale first')
    data = camera.data
    if data.type not in ('ORTHO', 'PERSP'):
        raise ValueError('Only orthographic and perspective cameras are supported')
    if data.dof.focus_object:
        raise ValueError('Use a numeric focus distance instead of a focus object')
    result = {'mode': 'override', 'projection': data.type, 'world_scale': .01,
              'relative_pose': {'position': list((matrix.translation - Vector(metadata['target'])) / metadata['radius']),
                                'rotation_deg': [math.degrees(x) for x in matrix.to_euler('XYZ')],
                                'ortho_scale': data.ortho_scale / metadata['radius'],
                                'clip_start': data.clip_start / metadata['radius'],
                                'clip_end': data.clip_end / metadata['radius']},
              'lens_mm': data.lens, 'sensor_fit': data.sensor_fit,
              'sensor_width_mm': data.sensor_width, 'sensor_height_mm': data.sensor_height,
              'shift_x': data.shift_x, 'shift_y': data.shift_y}
    if data.dof.use_dof:
        result['depth_of_field'] = {'focus_distance_angstrom': data.dof.focus_distance / .01,
                                   'f_stop': data.dof.aperture_fstop}
    return result


def light_style(scene, obj, cfg, metadata, authored):
    result = copy.deepcopy(cfg)
    radius = metadata['radius']
    result['energy'] = obj.data.energy / radius ** 2 if cfg.get('normalize_energy') else obj.data.energy
    result['color'] = convert(list(obj.data.color), 'hex')
    if hasattr(obj.data, 'size'):
        result.update(size_scale=obj.data.size / radius, min_size=0)
        result.pop('size', None)
    matrix = obj.matrix_world
    if any(abs(value - 1) > 1e-5 for value in matrix.to_scale()):
        raise ValueError('Scaled lights cannot be captured; use the light Size control')
    vector = (matrix.translation - Vector(metadata['target'])) / radius
    if cfg.get('camera_space'):
        camera = scene.camera.matrix_world if authored else Matrix(metadata['baseline']['object/' + metadata['camera'] + '/matrix'])
        vector = camera.to_3x3().inverted() @ vector
    result.update(relative_vector=list(vector), distance_scale=1, rotate_with_pymol_yaw=False,
                  rotation_deg=[math.degrees(x) for x in matrix.to_euler('XYZ')], look_at_target=False)
    result.pop('location', None)
    return result


def extract(scene, metadata, current, camera_policy):
    style, edits, unsupported = apply_bindings(metadata, current)
    ignored = []
    if 'render/resolution_percentage' in unsupported:
        percentage = scene.render.resolution_percentage / 100
        style['output'].update(width=max(1, int(scene.render.resolution_x * percentage)),
                               height=max(1, int(scene.render.resolution_y * percentage)))
        unsupported.remove('render/resolution_percentage')
        edits.append({'setting': 'output.resolution', 'value': [style['output']['width'], style['output']['height']]})
    camera_prefix = 'object/' + metadata['camera'] + '/'
    camera_supported = {'matrix', *('data/' + key for key in ('type', 'lens', 'ortho_scale', 'sensor_fit',
                         'sensor_width', 'sensor_height', 'shift_x', 'shift_y', 'clip_start', 'clip_end')),
                        'dof/use_dof', 'dof/focus_distance', 'dof/aperture_fstop'}
    camera_changes = [key for key in unsupported if key.startswith(camera_prefix)
                      and key[len(camera_prefix):] in camera_supported]
    if camera_policy == 'preserve_input':
        ignored = camera_changes
        unsupported = [key for key in unsupported if key not in camera_changes]
        style['camera'] = {'mode': 'pymol', 'world_scale': .01, 'preserve_framing': True}
        if scene.camera and scene.camera.data.dof.use_dof:
            unsupported.append('camera.depth_of_field requires camera_policy=authored; preserving an input pose would discard focus')
    else:
        try:
            if ident(scene.camera) != metadata['camera']:
                raise ValueError('Replacing the active camera is unsupported')
            style['camera'] = camera_style(scene, metadata)
            unsupported = [key for key in unsupported if key not in camera_changes]
            edits.append({'setting': 'camera', 'value': style['camera']})
        except ValueError as exc:
            unsupported.append('camera: ' + str(exc))
    lights = {ident(obj): obj for obj in scene.objects if obj.type == 'LIGHT'}
    for identifier, index in metadata['lights'].items():
        prefix = 'object/' + identifier + '/'
        light_changes = [key for key in unsupported if key.startswith(prefix)
                         and key[len(prefix):] in {'matrix', 'data/energy', 'data/color', 'data/size'}]
        if identifier not in lights or (not light_changes and camera_policy != 'authored'):
            continue
        try:
            style['lights'][index] = light_style(scene, lights[identifier], style['lights'][index], metadata,
                                               camera_policy == 'authored')
            unsupported = [key for key in unsupported if key not in light_changes]
            if light_changes:
                edits.append({'setting': 'lights.' + str(index), 'value': style['lights'][index]})
        except ValueError as exc:
            unsupported.append(prefix + str(exc))
    # A reusable look should not carry a structure-specific movie/state path or
    # force a .blend on every future render. The original scene keeps both.
    style['output'].update(save_blend=False, raw_view=False, filename='render.png')
    style['output'].pop('blend_filename', None)
    if style['output'].pop('animation', None):
        ignored.append('output.animation (keep animation in the original scene or supply it at render time)')
    return {'style': style, 'changes': edits, 'unsupported': sorted(set(unsupported)),
            'ignored': ignored, 'source_engine': metadata['source_engine']}


def main():
    from blender_render.developer import source_files
    request = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text())
    package = Path(__file__).resolve().parents[1]
    if source_files(package) != request['source_files']:
        raise RuntimeError('Scene extractor source snapshot changed')
    lock = json.loads((package / 'runtime.lock.json').read_text())
    if bpy.app.version_string != lock['blender']:
        raise ValueError('Scene extraction requires configured Blender ' + lock['blender'])
    bpy.ops.wm.open_mainfile(filepath=request['scene'], load_ui=False, use_scripts=False)
    text = bpy.data.texts.get(TEXT)
    if not text or len(text.as_string().encode()) > LIMIT:
        raise ValueError('No supported Blender Render editing metadata. Render again with --save-blend using the current version.')
    metadata = json.loads(text.as_string())
    checksum = metadata.pop('sha256')
    if metadata.get('format') != 1 or digest(metadata) != checksum:
        raise ValueError('Scene editing metadata changed or has an unsupported format')
    bpy.context.view_layer.update()
    current = snapshot(bpy.context.scene)
    result = {policy: extract(bpy.context.scene, metadata, current, policy)
              for policy in ('preserve_input', 'authored')}
    Path(request['result']).write_text(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
