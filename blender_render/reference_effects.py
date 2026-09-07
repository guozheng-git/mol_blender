# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional reference-style shader controls used by the shared scene builder."""
from __future__ import annotations


def directional_factor(tree, vector_socket, direction):
    """Map a signed direction across the unit coordinate cube to [0, 1]."""
    norm = sum(abs(float(value)) for value in direction)
    if norm <= 0:
        raise ValueError("Gradient direction must be nonzero")
    dot = tree.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.inputs[1].default_value = [float(value) / norm for value in direction]
    tree.links.new(vector_socket, dot.inputs[0])
    offset = tree.nodes.new("ShaderNodeMath")
    offset.operation = "ADD"
    offset.inputs[1].default_value = sum(-min(0, float(value)) for value in direction) / norm
    tree.links.new(dot.outputs["Value"], offset.inputs[0])
    return offset.outputs[0]


def apply_material_effects(tree, bsdf, config, rgba):
    pigment = None
    if config.get("color_attribute"):
        attribute = tree.nodes.new("ShaderNodeAttribute")
        attribute.attribute_name = config["color_attribute"]
        pigment = attribute.outputs["Color"]
    gradient = config.get("color_gradient")
    if gradient:
        coordinate = tree.nodes.new("ShaderNodeTexCoord")
        factor = directional_factor(tree, coordinate.outputs["Generated"], gradient["direction"])
        ramp = tree.nodes.new("ShaderNodeValToRGB")
        ramp.name = "Reference color gradient"
        colors = gradient["colors"]
        for index, color in enumerate(colors):
            position = index / (len(colors) - 1)
            element = ramp.color_ramp.elements[index] if index < 2 else ramp.color_ramp.elements.new(position)
            element.position = position
            element.color = rgba(color)
        tree.links.new(factor, ramp.inputs["Fac"])
        pigment = ramp.outputs["Color"]
    frosted = config.get("frosted")
    if frosted:
        coordinate = tree.nodes.new("ShaderNodeTexCoord")
        noise = tree.nodes.new("ShaderNodeTexNoise")
        noise.name = "Frosted shell patches"
        noise.inputs["Scale"].default_value = frosted["noise_scale"]
        noise.inputs["Detail"].default_value = 2.0
        tree.links.new(coordinate.outputs["Generated"], noise.inputs["Vector"])
        opacity = tree.nodes.new("ShaderNodeValToRGB")
        opacity.name = "Frosted shell opacity"
        for element, position, value in zip(opacity.color_ramp.elements, [.38, .65], [frosted["opacity_min"], frosted["opacity_max"]]):
            element.position = position
            element.color = (value, value, value, 1)
        tree.links.new(noise.outputs["Fac"], opacity.inputs["Fac"])
        alpha = tree.nodes.new("ShaderNodeMath")
        alpha.name = "Frosted base opacity"
        alpha.operation = "MULTIPLY"
        alpha.inputs[1].default_value = float(config.get("alpha", 1.0))
        tree.links.new(opacity.outputs["Color"], alpha.inputs[0])
        tree.links.new(alpha.outputs[0], bsdf.inputs["Alpha"])
        grid = tree.nodes.new("ShaderNodeTexBrick")
        grid.name = "Frosted shell grid"
        grid.offset = 0.0
        grid.inputs["Scale"].default_value = frosted["grid_scale"]
        grid.inputs["Mortar Size"].default_value = frosted["grid_width"]
        grid.inputs["Mortar Smooth"].default_value = 0.005
        grid.inputs["Brick Width"].default_value = .5
        grid.inputs["Row Height"].default_value = .5
        tree.links.new(coordinate.outputs["Generated"], grid.inputs["Vector"])
        strength = tree.nodes.new("ShaderNodeMath")
        strength.operation = "MULTIPLY"
        strength.inputs[1].default_value = frosted["grid_strength"]
        tree.links.new(grid.outputs["Fac"], strength.inputs[0])
        mix = tree.nodes.new("ShaderNodeMixRGB")
        mix.name = "Frosted grid pigment"
        mix.inputs[1].default_value = rgba(config.get("base_color", "#FFFFFF"))
        mix.inputs[2].default_value = rgba(frosted["grid_color"])
        if pigment is not None:
            tree.links.new(pigment, mix.inputs[1])
        tree.links.new(strength.outputs[0], mix.inputs[0])
        pigment = mix.outputs[0]
    if pigment is not None:
        shadow = tree.nodes.get("Clay shadow")
        tree.links.new(pigment, shadow.inputs[1] if shadow else bsdf.inputs["Base Color"])


def scale_nucleic_bases(branch, scale):
    """Narrow native base rungs without changing length or the shared node asset."""
    tree = branch.node_tree.copy()
    branch.node_tree = tree
    targets = [node for node in tree.nodes if node.type == "GROUP"
               and node.inputs.get("Base Scale") is not None]
    if len(targets) != 1:
        raise RuntimeError("Molecular Nodes 4.5.10 Base Scale socket is required")
    socket = targets[0].inputs["Base Scale"]
    multiply = tree.nodes.new("ShaderNodeVectorMath")
    multiply.name = "Nucleic base thickness"
    multiply.operation = "MULTIPLY"
    multiply.inputs[1].default_value = (scale, scale, 1.0)
    if socket.is_linked:
        source = socket.links[0].from_socket
        tree.links.new(source, multiply.inputs[0])
    else:
        multiply.inputs[0].default_value = socket.default_value
    tree.links.new(multiply.outputs["Vector"], socket)
