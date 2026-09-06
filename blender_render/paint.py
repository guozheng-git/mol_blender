# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dried acrylic material from the validated botanical rendering workflow.

Distances are Blender world units (one Angstrom is 0.01 world units).
Imported only within Blender.
"""
import bpy

def make_paint_material(cfg):
    mat = bpy.data.materials.new('Dried artist paint | ridges, stipple and pigment')
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    def node(kind, name, x, y):
        n = nodes.new(kind)
        n.name = name
        n.label = name
        n.location = (x, y)
        return n

    coord = node('ShaderNodeTexCoord', 'Object space paint coordinates', -1200, 50)
    sec = node('ShaderNodeAttribute', 'Secondary structure material response', -1500, -550)
    sec.attribute_name = 'sec_struct'
    is_ribbon = node('ShaderNodeMath', 'Helices and sheets carry paint relief', -1250, -550)
    is_ribbon.operation = 'LESS_THAN'
    is_ribbon.inputs[1].default_value = 2.5
    links.new(sec.outputs['Fac'], is_ribbon.inputs[0])
    relief_weight = node('ShaderNodeMapRange', 'Gentler texture on connecting loops', -1020, -600)
    relief_weight.inputs['To Min'].default_value = cfg['loop_relief']
    relief_weight.inputs['To Max'].default_value = 1.0
    links.new(is_ribbon.outputs[0], relief_weight.inputs['Value'])
    edge_normal = node('ShaderNodeBevel', 'Rounded painted edge normals', -760, -620)
    edge_normal.inputs['Radius'].default_value = cfg['edge_normal_radius']
    edge_normal.samples = 6
    coarse = node('ShaderNodeTexNoise', 'Broad irregular paint deposition', -1000, 400)
    coarse.inputs['Scale'].default_value = cfg['coarse_scale']
    coarse.inputs['Detail'].default_value = 2.4
    coarse.inputs['Roughness'].default_value = 0.65
    links.new(coord.outputs['Object'], coarse.inputs['Vector'])

    stretch = node('ShaderNodeVectorMath', 'Short brushed paint ridges', -1000, 0)
    stretch.operation = 'MULTIPLY'
    stretch.inputs[1].default_value = cfg['brush_stretch']
    links.new(coord.outputs['Object'], stretch.inputs[0])
    grain = node('ShaderNodeTexNoise', 'Dry paint wrinkles', -760, 0)
    grain.inputs['Scale'].default_value = cfg['paint_scale']
    grain.inputs['Detail'].default_value = cfg['paint_detail']
    grain.inputs['Roughness'].default_value = 0.75
    grain.inputs['Distortion'].default_value = cfg['paint_distortion']
    links.new(stretch.outputs[0], grain.inputs['Vector'])
    relief = node('ShaderNodeValToRGB', 'Raised paint ridges and shallow valleys', -530, -50)
    relief.color_ramp.elements[0].position = 0.29
    relief.color_ramp.elements[0].color = (0.07, 0.07, 0.07, 1)
    relief.color_ramp.elements[1].position = 0.71
    relief.color_ramp.elements[1].color = (0.94, 0.94, 0.94, 1)
    relief.color_ramp.interpolation = 'EASE'
    links.new(grain.outputs['Fac'], relief.inputs[0])

    micro = node('ShaderNodeTexNoise', 'Fine pigment stipple', -760, -360)
    micro.inputs['Scale'].default_value = cfg['micro_scale']
    micro.inputs['Detail'].default_value = 2.0
    micro.inputs['Roughness'].default_value = 0.8
    links.new(coord.outputs['Object'], micro.inputs['Vector'])

    base_bump = node('ShaderNodeBump', 'Subtle thick paint undulation', -260, -250)
    base_bump.inputs['Strength'].default_value = cfg['coarse_strength']
    base_bump.inputs['Distance'].default_value = cfg['coarse_distance']
    links.new(coarse.outputs['Fac'], base_bump.inputs['Height'])
    links.new(edge_normal.outputs['Normal'], base_bump.inputs['Normal'])
    paint_bump = node('ShaderNodeBump', 'Dried paint surface relief', -20, -150)
    paint_bump.inputs['Strength'].default_value = cfg['paint_strength']
    paint_bump.inputs['Distance'].default_value = cfg['paint_distance']
    links.new(relief.outputs['Color'], paint_bump.inputs['Height'])
    links.new(base_bump.outputs['Normal'], paint_bump.inputs['Normal'])
    micro_bump = node('ShaderNodeBump', 'Pigment grain relief', 210, -150)
    micro_bump.inputs['Strength'].default_value = cfg['micro_strength']
    micro_bump.inputs['Distance'].default_value = cfg['micro_distance']
    links.new(micro.outputs['Fac'], micro_bump.inputs['Height'])
    links.new(paint_bump.outputs['Normal'], micro_bump.inputs['Normal'])
    for i, (bump, strength) in enumerate(((base_bump, cfg['coarse_strength']),
                                           (paint_bump, cfg['paint_strength']),
                                           (micro_bump, cfg['micro_strength']))):
        weight = node('ShaderNodeMath', 'Relief strength by content ' + str(i), -350 + i * 240, -500)
        weight.operation = 'MULTIPLY'
        weight.inputs[1].default_value = strength
        links.new(relief_weight.outputs['Result'], weight.inputs[0])
        links.new(weight.outputs[0], bump.inputs['Strength'])

    attribute = node('ShaderNodeAttribute', 'Preserved green ribbon palette', -760, 680)
    attribute.attribute_name = cfg.get('color_attribute', 'Color')
    pigment = node('ShaderNodeValToRGB', 'Subtle uneven pigment density', -510, 480)
    pigment.color_ramp.elements[0].color = (cfg['pigment_min'],) * 3 + (1,)
    pigment.color_ramp.elements[1].color = (1, 1, 1, 1)
    links.new(coarse.outputs['Fac'], pigment.inputs['Fac'])
    multiply = node('ShaderNodeMixRGB', 'Paint body color', -220, 560)
    multiply.blend_type = 'MULTIPLY'
    multiply.inputs[0].default_value = 1.0
    links.new(attribute.outputs['Color'], multiply.inputs[1])
    links.new(pigment.outputs['Color'], multiply.inputs[2])

    luminance = node('ShaderNodeRGBToBW', 'Identify dark green paint', -500, 860)
    links.new(attribute.outputs['Color'], luminance.inputs[0])
    dark_weight = node('ShaderNodeMapRange', 'Keep sage and chartreuse colors pale', -260, 860)
    dark_weight.inputs['From Min'].default_value = 0.14
    dark_weight.inputs['From Max'].default_value = 0.28
    dark_weight.inputs['To Min'].default_value = 1.0
    dark_weight.inputs['To Max'].default_value = 0.0
    links.new(luminance.outputs[0], dark_weight.inputs['Value'])
    tint = node('ShaderNodeMixRGB', 'Forest green pigment tint', 0, 860)
    tint.inputs[1].default_value = (1, 1, 1, 1)
    tint.inputs[2].default_value = tuple(cfg['dark_paint_tint']) + (1,)
    links.new(dark_weight.outputs['Result'], tint.inputs[0])
    tinted = node('ShaderNodeMixRGB', 'Final reference paint palette', 250, 700)
    tinted.blend_type = 'MULTIPLY'
    tinted.inputs[0].default_value = 1.0
    links.new(multiply.outputs[0], tinted.inputs[1])
    links.new(tint.outputs[0], tinted.inputs[2])

    roughness = node('ShaderNodeMapRange', 'Uneven dry satin roughness', 150, 380)
    roughness.inputs['From Min'].default_value = 0.0
    roughness.inputs['From Max'].default_value = 1.0
    roughness.inputs['To Min'].default_value = cfg['roughness_min']
    roughness.inputs['To Max'].default_value = cfg['roughness_max']
    links.new(grain.outputs['Fac'], roughness.inputs['Value'])
    bsdf = node('ShaderNodeBsdfPrincipled', 'Opaque dried acrylic paint', 480, 350)
    bsdf.inputs['Specular IOR Level'].default_value = cfg['specular']
    bsdf.inputs['Coat Weight'].default_value = cfg['coat']
    bsdf.inputs['Coat Roughness'].default_value = cfg['coat_roughness']
    links.new(tinted.outputs[0], bsdf.inputs['Base Color'])
    links.new(roughness.outputs['Result'], bsdf.inputs['Roughness'])
    links.new(micro_bump.outputs['Normal'], bsdf.inputs['Normal'])
    links.new(base_bump.outputs['Normal'], bsdf.inputs['Coat Normal'])
    out = node('ShaderNodeOutputMaterial', 'Paint surface', 800, 350)
    links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
    return mat
