"""Bounded emission-only Gaussian halo; ordinary highlights stay unchanged."""
from __future__ import annotations


def configure_emission_halo(scene, config):
    import bpy
    for layer in scene.view_layers:
        layer.use_pass_emit = True
    tree = getattr(scene, 'compositing_node_group', None)
    if tree is None:
        if hasattr(scene, 'compositing_node_group'):
            tree = bpy.data.node_groups.new('Emission halo', 'CompositorNodeTree')
            scene.compositing_node_group = tree
        else:
            scene.use_nodes = True
            tree = scene.node_tree
    tree.nodes.clear()
    layers = tree.nodes.new('CompositorNodeRLayers')
    blur = tree.nodes.new('CompositorNodeBlur')
    radius = max(scene.render.resolution_x, scene.render.resolution_y) * scene.render.resolution_percentage / 100 * config['radius_fraction']
    if blur.inputs.get('Type') is not None:
        blur.inputs['Type'].default_value = 'Gaussian'
        blur.inputs['Size'].default_value = (radius, radius)
    else:
        blur.filter_type = 'GAUSS'
        blur.size_x = blur.size_y = round(radius)
    tree.links.new(layers.outputs.get('Emit') or layers.outputs['Emission'], blur.inputs['Image'])
    try:
        add = tree.nodes.new('CompositorNodeMixRGB')
        image_a, image_b, result = add.inputs[1], add.inputs[2], add.outputs[0]
    except RuntimeError:
        add = tree.nodes.new('ShaderNodeMix')
        add.data_type = 'RGBA'
        add.clamp_factor = False
        image_a, image_b, result = add.inputs[6], add.inputs[7], add.outputs[2]
    add.blend_type = 'ADD'
    add.inputs[0].default_value = config['strength']
    tree.links.new(layers.outputs['Image'], image_a)
    tree.links.new(blur.outputs['Image'], image_b)
    try:
        output = tree.nodes.new('CompositorNodeComposite')
    except RuntimeError:
        if not any(s.name == 'Image' and s.in_out == 'OUTPUT' for s in tree.interface.items_tree):
            tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        output = tree.nodes.new('NodeGroupOutput')
    tree.links.new(result, output.inputs[0])
    scene.render.use_compositing = True
