# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded timeline and viewport controls for the selected local desktop only."""
import bpy

from .scene import capture
from .editor_protocol import checked_value, number

VIEW_MODES = ('solid_color', 'material', 'rendered', 'wireframe')
PERIOD_INPUTS = {'Period (frames)': ('period_frames', 2.0, 3600.0),
                 'Period (seconds)': ('period_seconds', 0.1, 600.0)}


def views(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                yield window, area, area.spaces.active


def desktop_state(scene):
    """Include manual viewport/timeline edits in revision and undo conflict checks."""
    return {'timeline': [scene.frame_start, scene.frame_end, scene.frame_preview_start,
                         scene.frame_preview_end, scene.use_preview_range, scene.sync_mode],
            'views': [{'window': window.as_pointer(), 'area': area.as_pointer(),
                       'shading': space.shading.type, 'color': space.shading.color_type,
                       'overlays': space.overlay.show_overlays,
                       'cavity': space.shading.show_cavity} for window, area, space in views(scene)],
            'viewport_colors': {capture.ident(mat): list(mat.diffuse_color) for mat in bpy.data.materials
                                if mat.users or mat.use_fake_user}}


def escape(value):
    return value.replace('~', '~0').replace('/', '~1')


def append_controls(scene, data, result, Control):
    def add(name, target, attribute, spec, *, kind='value', label=''):
        result.setdefault(name, []).append(Control(name, target, attribute, spec, kind=kind, label=label))
    enum = lambda *values: {'type': 'enum', 'choices': list(values)}
    for _, _, space in views(scene):
        add('viewport/shading', space.shading, 'type', enum('SOLID', 'MATERIAL', 'RENDERED', 'WIREFRAME'))
        add('viewport/color_source', space.shading, 'color_type', enum('MATERIAL', 'OBJECT', 'RANDOM', 'SINGLE'))
        add('viewport/overlays', space.overlay, 'show_overlays', {'type': 'boolean'})
        add('viewport/cavity', space.shading, 'show_cavity', {'type': 'boolean'})
    if 'viewport/shading' in result:
        for mat in bpy.data.materials:
            if mat.users or mat.use_fake_user:
                add('viewport/material/' + escape(capture.ident(mat)) + '/color', mat, 'diffuse_color',
                    {'type': 'color'}, kind='hex', label=mat.name + ': viewport color')
    add('animation/fps', scene.render, 'fps', {'type': 'integer', 'minimum': 1, 'maximum': 120},
        label='Timeline frames per second (not a general speed multiplier)')
    # Only explicit, unlinked period sockets are exposed. No arbitrary RNA paths,
    # drivers, expressions, node creation or structural deformation is accepted.
    for obj in scene.objects:
        for modifier in obj.modifiers:
            if modifier.type != 'NODES' or not modifier.node_group or modifier.node_group.animation_data:
                continue
            for node in modifier.node_group.nodes:
                if node.type != 'GROUP':
                    continue
                for name, (suffix, minimum, maximum) in PERIOD_INPUTS.items():
                    socket = node.inputs.get(name)
                    if (socket is None or socket.is_linked or socket.type != 'VALUE'
                            or not isinstance(socket.default_value, (int, float))):
                        continue
                    key = 'animation/loop/' + '/'.join(map(escape, [obj.name, modifier.name, node.name, suffix]))
                    add(key, socket, 'default_value', {'type': 'number', 'minimum': minimum, 'maximum': maximum},
                        label=obj.name + ' / ' + (node.label or node.name) + ': ' + name)


def animation_speed(scene, target, multiplier):
    from . import editor_controls as controls
    number(multiplier, 0.125, 8)
    available = controls.controls(scene, controls.metadata())
    loops = {key: group for key, group in available.items() if key.startswith('animation/loop/')}
    if not isinstance(target, str):
        raise ValueError('Animation control must be an inspected control ID')
    if not target:
        if len(loops) != 1:
            raise ValueError('Select one inspected animation/loop control. This scene has '
                             f'{len(loops)} supported period controls; no scene was replaced.')
        target = next(iter(loops))
    if target not in loops:
        raise ValueError('Unsupported animation speed control; inspect the existing scene. No scene was replaced.')
    before = loops[target][0].value()
    result, undo = controls.edit(scene, [{'control': target, 'value': before / multiplier}])
    result['animation_speed'] = {'control': target, 'multiplier': multiplier,
                                 'period_before': before, 'period_after': before / multiplier}
    return result, undo


def viewport(scene, mode, overlays=None):
    from . import editor_controls as controls
    checked_value(mode, {'type': 'enum', 'choices': list(VIEW_MODES)})
    if overlays is not None:
        checked_value(overlays, {'type': 'boolean'})
    available = controls.controls(scene, controls.metadata())
    if 'viewport/shading' not in available:
        raise ValueError('The selected scene has no visible 3D viewport. Switch an area to 3D Viewport in the same window.')
    shading = {'solid_color': 'SOLID', 'material': 'MATERIAL', 'rendered': 'RENDERED', 'wireframe': 'WIREFRAME'}
    changes = [{'control': 'viewport/shading', 'value': shading[mode]}]
    if mode == 'solid_color':
        changes.extend([{'control': 'viewport/color_source', 'value': 'MATERIAL'},
                        {'control': 'viewport/cavity', 'value': True}])
        # Copy supported pigment values to Workbench display colors only. The
        # shader, renderer, light and camera values remain unchanged.
        pigments = {}
        for group in available.values():
            for control in group:
                if control.style_path and control.style_path[-1] == 'base_color' and control.kind == 'hex':
                    pigments[control.target.id_data.as_pointer()] = control.value()
        for mat in bpy.data.materials:
            if mat.node_tree and mat.node_tree.as_pointer() in pigments:
                key = 'viewport/material/' + escape(capture.ident(mat)) + '/color'
                if key in available:
                    changes.append({'control': key, 'value': pigments[mat.node_tree.as_pointer()]})
    if overlays is not None:
        changes.append({'control': 'viewport/overlays', 'value': overlays})
    return controls.edit(scene, changes)


def playback(scene, state):
    """Idempotent explicit play/pause; never toggle or follow another scene."""
    from . import editor_controls as controls
    checked_value(state, {'type': 'enum', 'choices': ['play', 'pause']})
    all_windows = list(bpy.context.window_manager.windows)
    windows = [w for w in all_windows if w.scene == scene]
    if not windows:
        raise ValueError('The selected scene is not displayed in a desktop window')
    if state == 'play' and (bpy.context.mode != 'OBJECT' or bpy.app.is_job_running('RENDER')):
        raise ValueError('Finish the active edit mode or render before starting playback')
    active = [w for w in all_windows if w.screen.is_animation_playing]
    selected_active = [w for w in active if w.scene == scene]
    # Blender's native play/cancel operators operate on a process-wide timer.
    # Calling play once per window would toggle it off again or stop a different
    # scene. Refuse a conflicting timer and operate on just one selected window.
    if state == 'play' and any(w.scene != scene for w in active):
        raise ValueError('Another scene is playing in this Blender process; pause it there first. Nothing was changed.')
    frame, subframe = scene.frame_current, scene.frame_subframe
    changed = False
    window = selected_active[0] if selected_active else windows[0]
    if (state == 'pause' and selected_active) or (state == 'play' and not selected_active):
        with bpy.context.temp_override(window=window, screen=window.screen):
            if state == 'pause':
                bpy.ops.screen.animation_cancel(restore_frame=False)
                # Keep the exact frame at request execution even if a playback
                # callback or Blender's stop path restores the initial frame.
                if (scene.frame_current, scene.frame_subframe) != (frame, subframe):
                    scene.frame_set(frame, subframe=subframe)
            else:
                bpy.ops.screen.animation_play()
        changed = True
    result = controls.inspect(scene)
    if result['playing'] != (state == 'play'):
        raise RuntimeError('Blender did not reach the requested playback state; inspect this session before retrying')
    result['playback'] = {'state': state, 'changed': changed, 'frame_before': frame,
                          'frame_after': scene.frame_current}
    return result
