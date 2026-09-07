#!/usr/bin/env python3
"""Run with Blender --background --python this_file -- OUTPUT_DIRECTORY.

Executed Blender regression checks, independent of a running render service.
The synthetic mesh tests complement, rather than replace, molecular previews.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / 'blender_render'))
if os.environ.get('BLENDER_RENDER_SITE'):
    sys.path.insert(0, os.environ['BLENDER_RENDER_SITE'])
import blender_custom_render as render

output = Path(sys.argv[sys.argv.index('--') + 1])
output.mkdir(parents=True, exist_ok=True)
results = {}


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mesh_snapshot(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', coords)
        loops = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get('vertex_index', loops)
        return hashlib.sha256(coords.tobytes() + loops.tobytes()).hexdigest()
    finally:
        evaluated.to_mesh_clear()


def check_cutaway(procedural):
    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    surface = bpy.context.object
    surface.data.materials.append(render._create_material('shell', {'base_color': '#2CB880'}))
    for face in surface.data.polygons:
        face.use_smooth = True
    if procedural:
        group = bpy.data.node_groups.new('Procedural surface', 'GeometryNodeTree')
        group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
        source = group.nodes.new('NodeGroupInput')
        sink = group.nodes.new('NodeGroupOutput')
        group.links.new(source.outputs['Geometry'], sink.inputs['Geometry'])
        surface.modifiers.new('Surface geometry', 'NODES').node_group = group
    bpy.ops.mesh.primitive_torus_add(major_radius=.35, minor_radius=.04)
    interior = bpy.context.object
    bpy.context.view_layer.update()
    before = mesh_snapshot(interior)
    info = {}
    assert render._apply_surface_ligand_cutout_planes(
        load_result=SimpleNamespace(), target_obj=surface, ligand_anchor=Vector((0, 0, 0)),
        radius=.45, offset=0, mirror=False, interface_center=None,
        camera_axis=Vector((0, -1, 0)), frame_mode='camera', flatten_z=False,
        plane_scales_cfg={'left':1, 'right':1, 'top':3, 'bottom':3, 'back':1},
        seal_mode='none', cap_material_cfg={'base_color':'#2CB880'}, runtime_info=info)
    bpy.context.view_layer.update()
    assert mesh_snapshot(interior) == before
    ev = surface.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        assert all(edge.is_manifold for edge in bm.edges)
        cuts = [face for face in mesh.polygons if mesh.materials[face.material_index].name.startswith('BridgeCutoutCap')]
        assert cuts and not any(face.use_smooth for face in cuts)
        # Front (+Y) points in the opening have been removed, back survives.
        assert not any(abs(v.co.x)<.44 and v.co.y>-.44 for v in mesh.vertices)
        assert any(v.co.y<-.9 for v in mesh.vertices)
        return {'inner_sha256':before, 'closed_surface':True, 'flat_colored_faces':len(cuts),
                'procedural':procedural}
    finally:
        bm.free()
        ev.to_mesh_clear()


def check_camera(projection, dimensions):
    reset()
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = dimensions
    bpy.ops.mesh.primitive_cube_add(location=(.2, 0, .1))
    obj = bpy.context.object
    obj.scale = (1.3, .7, .4)
    camera = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))
    scene.collection.objects.link(camera)
    scene.camera = camera
    bpy.context.view_layer.update()
    config = {'projection':projection, 'direction_source':'preset', 'yaw_deg':-60,
              'pitch_deg':15, 'roll_deg':28, 'fit':True, 'fit_margin':1.08,
              'shift_x':-.12, 'shift_y':.03}
    render._apply_override_camera(scene, camera, config, [obj])
    bpy.context.view_layer.update()
    points = [world_to_camera_view(scene, camera, obj.matrix_world @ v.co) for v in obj.data.vertices]
    assert all(0 <= p.x <= 1 and 0 <= p.y <= 1 and p.z > 0 for p in points)
    fitted = camera.matrix_world.copy()
    config.update(fit=False, projection='ORTHO', ortho_scale_angstrom=20)
    focus = Vector((.31, -.22, .18))
    config['focus_style'] = 'ligand'
    render._apply_override_camera(scene, camera, config, [obj], style_centers_by_name={'ligand':focus})
    bpy.context.view_layer.update()
    assert abs(camera.data.ortho_scale - .2) < 1e-6
    forward = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    assert forward.dot((focus-camera.location).normalized()) > .99999
    return {'projection':projection, 'dimensions':dimensions, 'all_vertices_inside':True,
            'explicit_scale_and_focus':True, 'fitted_matrix':[list(row) for row in fitted]}


def render_material(config, name, shape='plane'):
    reset()
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 8
    scene.render.resolution_x = 96
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.view_settings.view_transform = 'Standard'
    render._set_world(scene, {'background_type':'solid','color':'#FFFFFF','strength':1})
    if shape == 'sphere':
        bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=.8)
        for polygon in bpy.context.object.data.polygons:
            polygon.use_smooth = True
    elif shape == 'grid':
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=.8)
        render._apply_surface_wireframe(bpy.context.object, .5, 8.0, Vector((0,0,-1)))
        cells = bpy.context.object.data.polygons
        assert cells and all(len(face.vertices)==4 and face.normal.z>=-1e-6 for face in cells)
    else:
        bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    plane.data.materials.append(render._create_material(name, config))
    camera = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.location = (0, 0, 3)
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = 2
    scene.render.filepath = str(output / (name + '.png'))
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    pixels = np.array(image.pixels[:]).reshape(64, 96, 4)
    return pixels


def check_native_nucleic_bases():
    reset()
    ccd_path = os.environ.get('PRESS_BIOTITE_CCD_PATH')
    if ccd_path:
        assert render._biotite_ccd_target_paths() == [Path(ccd_path)]
        render._activate_biotite_ccd_path(Path(ccd_path))
    snapshots = []
    for index, scale in enumerate([None, .45, None]):
        molecule = render._load_molecule_with_fallback(repo / 'examples' / '1bna.cif', f'DNA_{index}')
        style = {'name':f'nucleic_{index}', 'style':'cartoon', 'quality':2,
                 'base_shape':'Cylinder', 'nucleic_radius':.8}
        if scale is not None:
            style['nucleic_base_scale'] = scale
        render._add_style(molecule, style, None)
        bpy.context.view_layer.update()
        snapshots.append(mesh_snapshot(molecule.object))
    assert snapshots[0] != snapshots[1], 'Rung control had no geometry effect'
    assert snapshots[0] == snapshots[2], 'Rung control mutated the shared default node asset'
    return {'default_sha256':snapshots[0], 'thin_sha256':snapshots[1], 'default_unchanged':True}


results['nucleic'] = check_native_nucleic_bases()
results['cutaway'] = [check_cutaway(False), check_cutaway(True)]
results['camera'] = [check_camera(projection, size) for projection in ['ORTHO','PERSP']
                     for size in [(960,540),(540,960)]]
gradient = render_material({'color_gradient':{'colors':['#FF0000','#0000FF'], 'direction':[1,0,0]},
                            'roughness':1}, 'gradient')
left = gradient[:,8:24,:3].mean((0,1))
right = gradient[:,72:88,:3].mean((0,1))
assert left[0]>right[0]+.15 and right[2]>left[2]+.15
frosted = render_material({'alpha':.5, 'base_color':'#FFFFFF', 'frosted':{
    'noise_scale':8, 'opacity_min':.05, 'opacity_max':1, 'grid_scale':20,
    'grid_width':.01,'grid_strength':.5,'grid_color':'#FFFFFF'}}, 'frosted')
alpha = frosted[8:-8,8:-8,3]
assert alpha.max() < .52 and alpha.min() < .12 and alpha.max()-alpha.min() > .15
results['shaders'] = {'gradient_left_rgb':left.tolist(),'gradient_right_rgb':right.tolist(),
                       'frosted_alpha_min':float(alpha.min()),'frosted_alpha_max':float(alpha.max())}

# Camera-relative cuts must use final yaw, pitch and roll, without mutating the
# saved pose while computing the cut. A later camera fit must use the same frame.
reset()
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
scene = bpy.context.scene
camera = bpy.data.objects.new('Camera', bpy.data.cameras.new('Camera'))
scene.collection.objects.link(camera)
camera.rotation_euler = (.4, .2, .8)
bpy.context.view_layer.update()
before = camera.matrix_world.copy()
cfg = {'direction_source':'pymol', 'yaw_offset_deg':37, 'pitch_offset_deg':-21,
       'roll_deg':17, 'fit':True, 'projection':'ORTHO'}
rotation = render._override_camera_rotation(camera, cfg)
assert camera.matrix_world == before
forward, up = rotation @ Vector((0,0,-1)), rotation @ Vector((0,1,0))
axes = render._resolve_cutout_frame_axes(frame_mode='camera', frame_axes_cfg=None,
    ligand_anchor=Vector((0,0,0)), interface_center=None, camera_axis=forward,
    camera_up=up, flatten_z=False)
assert axes[1].dot(-forward) > .99999 and axes[2].dot(up) > .99999
render._apply_override_camera(scene, camera, cfg, [obj])
bpy.context.view_layer.update()
assert (camera.matrix_world.to_3x3() @ Vector((0,0,-1))).dot(forward) > .99999
assert (camera.matrix_world.to_3x3() @ Vector((0,1,0))).dot(up) > .99999
results['camera_cut_frame'] = {'final_forward_and_up_match':True, 'saved_pose_unmutated':True}

rim_config = {'base_color':'#FA12A6', 'emission_color':'#FF39BA', 'emission_strength':.22,
              'edge_highlight':{'color':'#FFE5F7','power':2.5,'strength':1.4}}
rim = render_material(rim_config, 'rim', 'sphere')
plain = render_material({k:v for k,v in rim_config.items() if k!='edge_highlight'}, 'plain', 'sphere')
# Green increases toward white at the silhouette; pink centers stay pink.
edge_gain = (rim[28:36,10:13,1]-plain[28:36,10:13,1]).mean()
center_gain = (rim[28:36,44:52,1]-plain[28:36,44:52,1]).mean()
assert edge_gain > center_gain + .05, (edge_gain, center_gain)
assert np.max(np.abs(rim[28:36,44:52,:3]-plain[28:36,44:52,:3])) < .03
wire = render_material({'base_color':'#FFFFFF','emission_color':'#FFFFFF','emission_strength':1}, 'wire', 'grid')
assert wire[:,:,3].max() > .8 and wire[:,:,3].min() < .05
assert .05 < (wire[:,:,3]>.2).mean() < .8
results['rim_and_mesh'] = {'edge_green_gain':float(edge_gain),'center_green_gain':float(center_gain),
                           'wire_visible_fraction':float((wire[:,:,3]>.2).mean())}
scene = bpy.context.scene
scene.render.resolution_x = 1600
scene.render.resolution_y = 900
render._configure_glow_compositor(scene, {'materials':{'test':{'glow':{'size':6,'strength':.15,'threshold':.9}}}})
tree = getattr(scene, 'compositing_node_group', None) or scene.node_tree
glare = next(n for n in tree.nodes if n.bl_idname=='CompositorNodeGlare')
if glare.inputs.get('Size') is not None:
    assert abs(glare.inputs['Size'].default_value-.04) < 1e-6
    assert glare.inputs['Quality'].default_value == 'High'
results['glow'] = {'small_image_fraction':.04, 'high_quality':True}
(output / 'checks.json').write_text(json.dumps(results,indent=2))
print('PASS: closed cuts, preserved interior, final camera frame, four fits, shader opacity, continuous rims, visible quad mesh and bounded glow')
