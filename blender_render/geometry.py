# SPDX-FileCopyrightText: 2026 Magnus Bauer
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit secondary-structure/color transfer and rounded cartoon boundaries."""
from __future__ import annotations

import colorsys
import math

import bpy
import bmesh
import numpy as np
from mathutils.kdtree import KDTree


def linear_color(value):
    rgb = [int(value.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in rgb] + [1.0]


def apply_atom_attributes(molecule, view, style):
    array = getattr(molecule, "array", None)
    if array is None:
        array = getattr(molecule, "atom_array", None)
    if array is None:
        raise RuntimeError("Molecular Nodes did not expose atom attributes")
    if len(array.shape) > 1:
        array = array[0]
    count = len(array)
    ss_map = view.get("ss_hints", {}).get("by_chain_resi", {})
    insertion = getattr(array, "ins_code", [""] * count)
    residue_ids = [f"{int(r)}{str(ins).strip()}" for r, ins in zip(array.res_id, insertion)]
    sec = np.array([{"H": 1, "S": 2}.get(ss_map.get(f"{c}:{r}", "L"), 3)
                    for c, r in zip(array.chain_id, residue_ids)], dtype=np.int32)
    store = getattr(molecule, "store_named_attribute", None)
    if callable(store) and ss_map:
        store(sec, "sec_struct", atype="INT")
    palette = style.get("geometry", {}).get("secondary_structure_palette")
    follow = style.get("color_follow", {})
    atom_hints = view.get("color_hints", {}).get("atoms", {}) if follow.get("enabled") else {}
    if not palette and not atom_hints:
        return
    colors = np.ones((count, 4), dtype=np.float32)
    previous = None
    helix_index = -1
    seen = {}
    for i, (chain, resi, resn, name) in enumerate(zip(array.chain_id, residue_ids, array.res_name, array.atom_name)):
        residue = (str(chain), str(resi))
        if residue not in seen:
            if sec[i] == 1 and (previous is None or previous[0] != str(chain) or previous[1] != 1):
                helix_index += 1
            if palette:
                color = palette["helix"][helix_index % len(palette["helix"])] if sec[i] == 1 else palette["sheet"] if sec[i] == 2 else palette["loop"]
            else:
                color = "#A9C7E7"
            seen[residue] = linear_color(color)
            previous = (str(chain), sec[i])
        colors[i] = seen[residue]
        hint = atom_hints.get(f"{chain}:{resi}:{resn}:{name}")
        if hint:
            rgb = [int(hint.lstrip("#")[j:j + 2], 16) / 255 for j in (0, 2, 4)]
            h, s, v = colorsys.rgb_to_hsv(*rgb)
            rgb = colorsys.hsv_to_rgb(h, min(1, max(0, s + follow.get("saturation_boost", 0))), min(1, max(0, v + follow.get("value_boost", 0))))
            adjusted = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in rgb]
            mix = follow.get("mix", .65) if palette else 1.0
            colors[i, :3] = (1 - mix) * colors[i, :3] + mix * np.asarray(adjusted)
    if not callable(store):
        raise RuntimeError("Pinned Molecular Nodes runtime is required for atom color transfer")
    store(colors, "Color", atype="FLOAT_COLOR")


def round_edges(objects, config):
    if not config or config.get("radius_angstrom", 0) <= 0:
        return 0
    count = 0
    seen = set()
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.as_pointer() in seen or obj.type != "MESH" or obj.hide_render:
            continue
        seen.add(obj.as_pointer())
        source_color = obj.data.attributes.get("Color")
        source_points, source_colors = [], []
        if source_color is not None and source_color.domain == "POINT":
            source_points = [vertex.co.copy() for vertex in obj.data.vertices]
            source_colors = [tuple(value.color) for value in source_color.data]
        # Cartoon nucleotide bases (and some sphere styles) are instances.
        # Blender's mesh extraction drops them unless they are realized first.
        # Keep them in the same geometry transaction as the rounded backbone.
        group = bpy.data.node_groups.new("Realize molecular geometry", "GeometryNodeTree")
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        source = group.nodes.new("NodeGroupInput")
        realize = group.nodes.new("GeometryNodeRealizeInstances")
        output = group.nodes.new("NodeGroupOutput")
        group.links.new(source.outputs["Geometry"], realize.inputs["Geometry"])
        group.links.new(realize.outputs["Geometry"], output.inputs["Geometry"])
        modifier = obj.modifiers.new("Realize nucleotide bases before rounding", "NODES")
        modifier.node_group = group
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        # MN 4.5.10 leaves realized base faces with transparent/unset Color.
        # Transfer only those unset values from the nearest source atom, so
        # PyMOL black colors and the existing interpolated cartoon stay intact.
        color = mesh.attributes.get("Color")
        if source_points and color is not None:
            tree = KDTree(len(source_points))
            for index, point in enumerate(source_points):
                tree.insert(point, index)
            tree.balance()
            for index, value in enumerate(color.data):
                if value.color[3] > 0:
                    continue
                if color.domain == "FACE":
                    point = mesh.polygons[index].center
                elif color.domain == "CORNER":
                    point = mesh.vertices[mesh.loops[index].vertex_index].co
                else:
                    point = mesh.vertices[index].co
                _, nearest, _ = tree.find(point)
                value.color = source_colors[nearest]
        obj.modifiers.clear()
        bpy.data.node_groups.remove(group)
        obj.data = mesh
        bevel = obj.modifiers.new("Rounded painted borders", "BEVEL")
        bevel.width = config["radius_angstrom"] * .01
        bevel.segments = config["segments"]
        bevel.limit_method = "ANGLE"
        bevel.angle_limit = math.radians(config["angle_degrees"])
        bevel.miter_outer = "MITER_ARC"
        bevel.use_clamp_overlap = True
        if config["adaptive_clearance"]:
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                bm.normal_update()
                bm.edges.ensure_lookup_table()
                candidates = {e for e in bm.edges if e.is_manifold and e.calc_face_angle() > bevel.angle_limit}
                clearance = {}
                for vertex in bm.verts:
                    transverse = [e.calc_length() for e in vertex.link_edges if e not in candidates]
                    lengths = transverse or [e.calc_length() for e in vertex.link_edges]
                    clearance[vertex] = min(lengths, default=0) * .45
                weights = mesh.attributes.get("bevel_weight_edge") or mesh.attributes.new("bevel_weight_edge", "FLOAT", "EDGE")
                for edge in bm.edges:
                    weights.data[edge.index].value = min(1.0, *(clearance[v] / bevel.width for v in edge.verts)) if edge in candidates else 0
                bevel.limit_method = "WEIGHT"
                bevel.use_clamp_overlap = False
            finally:
                bm.free()
        count += 1
    return count
