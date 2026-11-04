"""Render bounded, data-only putative polar contacts exported by PyMOL."""
from __future__ import annotations


def build(array, obj, pairs, config):
    import math
    import bpy
    from mathutils import Vector
    from blender_render.geometry import linear_color
    if len(pairs) > 256:
        raise ValueError('Polar contacts exceed 256 pairs')
    insertions = getattr(array, 'ins_code', [''] * len(array))
    points = {}
    for i, (chain, resi, ins, resn, name) in enumerate(zip(array.chain_id,array.res_id,insertions,array.res_name,array.atom_name)):
        key=f'{chain}:{int(resi)}{str(ins).strip()}:{resn}:{name}'
        if key in points: points[key]=None
        else: points[key]=obj.matrix_world @ obj.data.vertices[i].co
    material=bpy.data.materials.new('Putative polar contacts');material.use_nodes=True
    bsdf=material.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value=linear_color(config['color'])
    bsdf.inputs['Roughness'].default_value=.4
    radius=config['radius_angstrom']*.01;count=config['dashes'];objects=[]
    for number,pair in enumerate(pairs):
        a,b=(points.get(key) for key in pair)
        if a is None or b is None:
            raise ValueError('Polar contact atom key is absent or ambiguous')
        if not .001 < (a-b).length <= .0361:
            raise ValueError('Polar contact exceeds the 3.6 angstrom export cutoff')
        for dash in range(count):
            start=a+(b-a)*(dash+.12)/count;end=a+(b-a)*(dash+.78)/count
            length=(end-start).length;r=min(radius,length/2);half=length/2-r
            vertices=[(0,0,-length/2)];rings=[];sides=24
            profiles=[(-half+r*math.sin(-math.pi/2+j*math.pi/16),r*math.cos(-math.pi/2+j*math.pi/16)) for j in range(1,9)]
            profiles += [(half+r*math.sin(j*math.pi/16),r*math.cos(j*math.pi/16)) for j in range(8)]
            for z,rr in profiles:
                ring=[]
                for k in range(sides):
                    ring.append(len(vertices));vertices.append((rr*math.cos(2*math.pi*k/sides),rr*math.sin(2*math.pi*k/sides),z))
                rings.append(ring)
            top=len(vertices);vertices.append((0,0,length/2));faces=[]
            for k in range(sides):
                nk=(k+1)%sides;faces.append((0,rings[0][nk],rings[0][k]))
                for lower,upper in zip(rings,rings[1:]):faces.append((lower[k],lower[nk],upper[nk],upper[k]))
                faces.append((rings[-1][k],rings[-1][nk],top))
            mesh=bpy.data.meshes.new('Polar contact dash');mesh.from_pydata(vertices,[],faces);mesh.update()
            guide=bpy.data.objects.new(f'PolarContact_{number+1}_{dash+1}',mesh);bpy.context.scene.collection.objects.link(guide)
            guide.location=(start+end)/2;guide.rotation_mode='QUATERNION';guide.rotation_quaternion=(end-start).to_track_quat('Z','Y')
            mesh.materials.append(material)
            for face in mesh.polygons:face.use_smooth=True
            objects.append(guide)
    return objects
