"""An isolated ellipsoid cut with a shaded, sequence-colored inner wall."""
from __future__ import annotations


def apply(target, center, axes, radii, indices, attribute, info):
    import bpy
    from mathutils import Matrix
    from mathutils.kdtree import KDTree
    # Sample only selected surface atoms, so the inner wall cannot inherit
    # the nearby peptide's pigment. Geometry of cartoons/sticks is untouched.
    source = target.data.attributes.get(attribute)
    if source is None or source.domain != 'POINT':
        raise ValueError('Rounded cutaway requires a point color attribute')
    selected = indices if indices is not None else range(len(target.data.vertices))
    points = [target.matrix_world @ target.data.vertices[i].co for i in selected]
    colors = [tuple(source.data[i].color) for i in selected]
    if not points:
        raise ValueError('Rounded cutaway surface selection is empty')
    kd = KDTree(len(points))
    for i, point in enumerate(points): kd.insert(point, i)
    kd.balance()
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(target.evaluated_get(deps), preserve_all_data_layers=True, depsgraph=deps)
    target.modifiers.clear();target.data = mesh
    base = next((m for m in mesh.materials if m), None)
    if base is None: raise ValueError('Rounded cutaway surface has no material')
    cavity = base.copy();cavity.name = 'Rounded cavity'
    nt = cavity.node_tree;bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    pigment = nt.nodes.new('ShaderNodeAttribute');pigment.attribute_name = attribute
    shade = nt.nodes.new('ShaderNodeVectorMath');shade.operation = 'SCALE';shade.inputs['Scale'].default_value = .7
    nt.links.new(pigment.outputs['Color'], shade.inputs[0]);nt.links.new(shade.outputs['Vector'], bsdf.inputs['Base Color'])
    bsdf.inputs['Roughness'].default_value = .8
    mesh.materials.append(cavity);cap_index = len(mesh.materials)-1
    bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64, location=center)
    cutter = bpy.context.object
    try:
        cutter.rotation_euler = Matrix(axes).transposed().to_euler()
        cutter.scale = radii;cutter.hide_render = True
        for m in mesh.materials: cutter.data.materials.append(m)
        for face in cutter.data.polygons: face.material_index = cap_index
        mod = target.modifiers.new('Rounded pocket opening', 'BOOLEAN')
        mod.operation='DIFFERENCE';mod.solver='EXACT';mod.object=cutter
        bpy.context.view_layer.update()
        cut = bpy.data.meshes.new_from_object(target.evaluated_get(deps), preserve_all_data_layers=True, depsgraph=deps)
        target.modifiers.clear();target.data=cut
    finally:
        bpy.data.objects.remove(cutter, do_unlink=True)
    color = cut.attributes.get(attribute)
    if color:cut.attributes.remove(color)
    color = cut.color_attributes.new(name=attribute,type='FLOAT_COLOR',domain='POINT')
    for vertex, value in zip(cut.vertices, color.data):
        neighbors=kd.find_n(target.matrix_world @ vertex.co,4)
        weights=[1/max(distance,1e-6)**2 for _,_,distance in neighbors];total=sum(weights)
        value.color=tuple(sum(colors[i][k]*w for (_,i,_),w in zip(neighbors,weights))/total for k in range(3))+(1,)
    for face in cut.polygons:face.use_smooth=True
    cut.update()
    info.update(applied=True,ligand_cutout_mode='ellipsoid',radii_world=list(radii),center_world=list(center),surface_only=True)
    return True
