# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera, molecular-state and cutaway animation for the shared Blender renderer."""
import shutil
import tempfile
from pathlib import Path
import math
import bpy
from mathutils import Matrix, Vector
from blender_render.animation import angles, package_frames

LID_PROPERTY = "bridge_lid_transparency"


def _add_lid_transparency(material: bpy.types.Material) -> None:
    """Let an object's lid property fade this material; 0 leaves it unchanged.

    Surface materials may be assigned inside Geometry Nodes rather than object
    slots, so the lid copy shares them. An object attribute selects the fade per
    object: every object without the property renders exactly as before.
    """
    tree = getattr(material, "node_tree", None)
    if tree is None or material.get("bridge_lid_ready"):
        return
    output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    if output is None or not output.inputs["Surface"].links:
        return
    shader = output.inputs["Surface"].links[0].from_socket
    attribute = tree.nodes.new("ShaderNodeAttribute")
    attribute.attribute_type = "OBJECT"
    attribute.attribute_name = LID_PROPERTY
    clear = tree.nodes.new("ShaderNodeBsdfTransparent")
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.name = "Cutaway lid fade"
    tree.links.new(attribute.outputs["Fac"], mix.inputs[0])
    tree.links.new(shader, mix.inputs[1])
    tree.links.new(clear.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], output.inputs["Surface"])
    material["bridge_lid_ready"] = True


def make_cutaway_lid(target_obj, cutter_obj, *, transparency: float):
    """Copy the part of a surface that the cut removes, so it can fade away."""
    lid = target_obj.copy()
    lid.data = target_obj.data.copy()
    lid.name = f"{target_obj.name}_CutawayLid"
    for collection in target_obj.users_collection:
        collection.objects.link(lid)
    modifier = lid.modifiers.new(name="BridgeCutawayLid", type="BOOLEAN")
    modifier.operation = "INTERSECT"
    modifier.object = cutter_obj
    if hasattr(modifier, "solver"):
        modifier.solver = "EXACT"
    if hasattr(modifier, "use_hole_tolerant"):
        modifier.use_hole_tolerant = True
    # Bake the lid to a plain mesh once. Left procedural, every per-frame fade
    # update would recompute its molecular surface and exact Boolean.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    baked = bpy.data.meshes.new_from_object(
        lid.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph
    )
    lid.modifiers.clear()
    lid.data = baked
    lid["bridge_cutaway_lid"] = True
    lid[LID_PROPERTY] = float(transparency)
    for material in bpy.data.materials:
        _add_lid_transparency(material)
    return lid


def _orbit_frames(camera, center, config):
    original = camera.matrix_world.copy()
    axis = original.to_quaternion() @ Vector((0, 1, 0))
    for index, angle in enumerate(angles(config)[1:], 1):
        camera.matrix_world = (Matrix.Translation(center) @
            Matrix.Rotation(math.radians(angle), 4, axis) @
            Matrix.Translation(-center) @ original)
        yield index


def _path_frames(camera, config, world_scale):
    """Move the camera by each PyMOL view's rigid motion relative to the first.

    Views convert with the renderer's own PyMOL camera conversion, in angstrom,
    so axis conventions match the first frame exactly. The motion is applied to
    the camera as constructed (including preset fitting and lens), not to a
    fresh PyMOL camera. Orthographic zoom scales ortho_scale by camera distance.
    """
    from blender_render.blender_custom_render import _compute_camera_spec

    def spec(view):
        return _compute_camera_spec({'pymol_view': view})

    original = camera.matrix_world.copy()
    original_scale = camera.data.ortho_scale
    start, end = camera.data.clip_start, camera.data.clip_end
    anchor = config.get('anchor', 0)
    base_view = config['path'][anchor]
    matrix, _, _, _, front0, rear0 = spec(base_view)
    first = Matrix(matrix).inverted()
    for index, view in enumerate(config['path']):
        if index == anchor:
            continue  # the still image is this frame
        matrix, _, _, _, front, rear = spec(view)
        motion = Matrix(matrix) @ first
        motion.translation *= world_scale
        camera.matrix_world = motion @ original
        # Each view carries its own clipping slab: a camera moving in must not
        # keep the first view's front plane, which would cut the scene. Shift
        # the constructed planes (which a preset may have fitted) by the change.
        camera.data.clip_start = max(1e-6, start + (front - front0) * world_scale)
        camera.data.clip_end = max(camera.data.clip_start + 1e-3, end + (rear - rear0) * world_scale)
        if camera.data.type == 'ORTHO':
            camera.data.ortho_scale = original_scale * abs(view[11]) / abs(base_view[11])
        yield index


def _molecular_states(scene, view_doc, structure_path):
    """Vertex positions per PyMOL state for every molecule object in the scene.

    Molecular Nodes stores one vertex per atom at 0.01 x angstrom, in the order
    its reader returns atoms after removing solvent. The same reader and filter
    run here, and the exported base state must match the loaded structure, so
    any ordering difference stops the render instead of scrambling atoms.
    """
    import json
    import numpy as np
    import biotite.structure as struc
    import molecularnodes as mn

    data = json.loads(Path(view_doc['state_coordinates_file']).read_text())
    array = mn.Molecule._read(str(structure_path)).array
    if isinstance(array, struc.AtomArrayStack):
        array = array[0]
    states = np.asarray(data['coordinates'], dtype=float).reshape(len(data['coordinates']), -1, 3)
    if states.shape[1] != len(array):
        raise RuntimeError('State coordinates list a different number of atoms than the structure')
    if np.abs(states[data['base'] - 1] - array.coord).max() > 2e-3:
        raise RuntimeError('State coordinates do not follow the structure atom order')
    positions = states[:, ~struc.filter_solvent(array)] * 0.01
    base = positions[data['base'] - 1]
    targets = []
    for obj in scene.objects:
        mesh = obj.data if obj.type == 'MESH' else None
        if mesh is None or len(mesh.vertices) != len(base):
            continue
        current = np.empty(len(base) * 3)
        mesh.attributes['position'].data.foreach_get('vector', current)
        if np.allclose(current.reshape(-1, 3), base, atol=1e-4):
            targets.append(obj)
    if not targets:
        raise RuntimeError('No molecule object matches the exported base state')
    return positions, base, targets, data['base']


def _set_positions(targets, positions):
    flat = positions.reshape(-1)
    for obj in targets:
        obj.data.attributes['position'].data.foreach_set('vector', flat)
        obj.data.update()


def _set_lid(lids, opacity):
    for lid in lids:
        lid[LID_PROPERTY] = 1.0 - opacity
        lid.hide_render = opacity <= 0.0
        # The lid is a static mesh: re-sync the object, never its geometry.
        lid.update_tag(refresh={'OBJECT'})


def render(scene, camera, center, output_png, config, world_scale=None, view_doc=None,
           structure_path=None):
    original = camera.matrix_world.copy()
    original_ortho_scale = camera.data.ortho_scale
    original_clip = camera.data.clip_start, camera.data.clip_end
    original_path = scene.render.filepath
    original_persistent = scene.render.use_persistent_data
    scene.render.use_persistent_data = True
    lids = [obj for obj in scene.objects if obj.get('bridge_cutaway_lid')] if config.get('lid') else []
    if config.get('lid') and not lids:
        raise RuntimeError('The lid animation found no cutaway surface to fade')
    molecular = None
    if config.get('states'):
        molecular = _molecular_states(scene, view_doc, structure_path)
    if config['mode'] == 'path':
        frames = _path_frames(camera, config, world_scale)
        count = len(config['path'])
    else:
        frames = _orbit_frames(camera, center, config)
        count = len(angles(config))
    try:
        with tempfile.TemporaryDirectory(prefix='animation-', dir=output_png.parent) as directory:
            directory = Path(directory)
            still = config.get('anchor', 0)
            shutil.copyfile(output_png, directory / f'{still:04d}.png')
            if molecular and config['states'][still] != molecular[3]:
                raise RuntimeError('The anchor animation state must be the exported state')
            for done, index in enumerate(frames, 2):
                if molecular:
                    _set_positions(molecular[2], molecular[0][config['states'][index] - 1])
                if lids:
                    _set_lid(lids, config['lid'][index])
                scene.render.filepath = str(directory / f'{index:04d}.png')
                bpy.context.view_layer.update()
                bpy.ops.render.render(write_still=True)
                print(f'Animation frame {done}/{count}', flush=True)
            package_frames(directory, output_png.with_suffix('.frames.zip'), config,
                           scene.render.resolution_x, scene.render.resolution_y)
    finally:
        if molecular:
            _set_positions(molecular[2], molecular[1])
        if lids:
            _set_lid(lids, config['lid'][config.get('anchor', 0)])
        camera.matrix_world = original
        camera.data.ortho_scale = original_ortho_scale
        camera.data.clip_start, camera.data.clip_end = original_clip
        scene.render.filepath = original_path
        scene.render.use_persistent_data = original_persistent
        bpy.context.view_layer.update()
    if config['mode'] == 'path':
        return dict({k: v for k, v in config.items() if k != 'path'}, frames=count,
                    camera='pymol_view_path', encoding_background='white')
    return dict(config, frames=count, angles_deg=angles(config), axis='initial_camera_up',
                center=list(center), encoding_background='white')
