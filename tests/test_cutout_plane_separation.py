"""Exercise coincident cut faces with actual Blender Boolean geometry."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


def test_overlapping_surfaces_have_distinct_cut_depths(tmp_path):
    blender = os.environ.get('MOL_BLENDER_TEST_BLENDER')
    if not blender:
        pytest.skip('Set MOL_BLENDER_TEST_BLENDER for the real Boolean check')
    root = Path(__file__).resolve().parents[1]
    runtime_site = os.environ.get('MOL_BLENDER_TEST_SITE')
    script = tmp_path / 'check_planes.py'
    script.write_text(textwrap.dedent(f'''
        import sys, json
        if {runtime_site!r}:
            sys.path.insert(0, {runtime_site!r})
        sys.path.insert(0, {str(root)!r})
        import bpy
        from mathutils import Vector
        from mol_blender.blender_custom_render import (
            _separate_cutout_planes, _create_plane_cutout_prism,
            _add_boolean_difference_modifier,
        )
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        axes = tuple(Vector(v) for v in ((1,0,0),(0,1,0),(0,0,1)))
        depths = []
        # Three partially overlapping surfaces, with a shared rear cut plane.
        # A fourth surface requests a different depth and must stay unchanged.
        for index, centre_y in enumerate((0., 0., 0., .2)):
            bpy.ops.mesh.primitive_cube_add(size=2, location=(index*.05,0,0))
            target = bpy.context.object
            centre = Vector((0,centre_y,0))
            enabled = dict(left=False,right=False,top=False,bottom=False,back=True)
            insets, records = _separate_cutout_planes(
                target_obj=target, cut_center=centre, axes=axes, radius_world=.5,
                planes_enabled=enabled, plane_scales={{'back':1.}},
            )
            cutter, bounds = _create_plane_cutout_prism(
                cut_center=centre,axis_x=axes[0],axis_y=axes[1],axis_z=axes[2],
                radius_world=.5,scene_radius=2.,planes_enabled=enabled,
                plane_scales={{'back':1.}},plane_insets=insets,
            )
            _add_boolean_difference_modifier(target_obj=target,cutter_obj=cutter)
            target['bridge_cutout_planes'] = json.dumps(records)
            bpy.context.view_layer.update()
            evaluated = target.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = evaluated.to_mesh()
            depth = max((evaluated.matrix_world @ v.co).y for v in mesh.vertices)
            evaluated.to_mesh_clear()
            depths.append(depth)
        assert abs(depths[0] + .5) < 1e-6, depths
        assert abs(depths[1] - depths[0] - .0005) < 1e-6, depths
        assert abs(depths[2] - depths[1] - .0005) < 1e-6, depths
        assert abs(depths[3] + .3) < 1e-6, depths
        print('CUT_PLANES_SEPARATED', depths)
    '''))
    result = subprocess.run([blender, '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python', str(script)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CUT_PLANES_SEPARATED' in result.stdout
