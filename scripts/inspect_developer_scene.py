"""Inspect a locally generated scene in fresh Blender with --disable-autoexec.

blender --background --disable-autoexec render.blend --python this.py -- report.json
No render or scene mutation is performed.
"""
import hashlib
import json
import sys
from pathlib import Path

import bpy
import numpy as np

scene = bpy.context.scene
camera = scene.camera
matrix = np.asarray(camera.matrix_world)
forward = -matrix[:3, 2]
depths = []
geometry = hashlib.sha256()
count = 0
depsgraph = bpy.context.evaluated_depsgraph_get()
for obj in sorted(scene.objects, key=lambda obj: obj.name):
    if obj.type != "MESH" or obj.hide_render:
        continue
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        coordinates = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        indices = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.vertices.foreach_get("co", coordinates)
        mesh.loops.foreach_get("vertex_index", indices)
        # Object names may include run IDs; scientific geometry does not.
        geometry.update(coordinates.tobytes())
        geometry.update(indices.tobytes())
        geometry.update(np.asarray(obj.matrix_world, dtype=np.float32).tobytes())
        transform = np.asarray(obj.matrix_world)
        world = coordinates.reshape(-1, 3) @ transform[:3, :3].T + transform[:3, 3]
        depths.extend(((world - matrix[:3, 3]) @ forward / .01).tolist())
        count += len(mesh.vertices)
    finally:
        evaluated.to_mesh_clear()
report = {"geometry_sha256": geometry.hexdigest(), "vertices": count,
          "depth_quantiles_angstrom": np.quantile(depths, [0, .25, .5, .75, 1]).tolist(),
          "camera_matrix": matrix.tolist(), "camera_type": camera.data.type,
          "depth_of_field": {"enabled": camera.data.dof.use_dof,
                             "focus_distance_angstrom": camera.data.dof.focus_distance / .01,
                             "f_stop": camera.data.dof.aperture_fstop},
          "resolution": [scene.render.resolution_x, scene.render.resolution_y],
          "seed": scene.cycles.seed, "samples": scene.cycles.samples}
Path(sys.argv[sys.argv.index("--") + 1]).write_text(json.dumps(report, indent=2))
