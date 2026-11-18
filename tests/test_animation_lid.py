"""Real Blender geometry regression; opt in with MOL_BLENDER_TEST_BLENDER."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


def test_cutaway_lid_has_only_outer_faces_and_consistent_visibility(tmp_path):
    blender = os.environ.get('MOL_BLENDER_TEST_BLENDER')
    if not blender:
        pytest.skip('Set MOL_BLENDER_TEST_BLENDER to run the real Blender check')
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / 'check_lid.py'
    script.write_text(textwrap.dedent(f'''
        import sys
        sys.path.insert(0, {str(root)!r})
        import bpy
        from mol_blender.animation_blender import make_cutaway_lid, _set_lid, LID_PROPERTY, LID_TRANSPARENT_BOUNCES
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
        material = bpy.data.materials.new('Shared surface and cutter material')
        material.use_nodes = True
        bpy.ops.mesh.primitive_cube_add(size=2)
        target = bpy.context.object
        target.data.materials.append(material)
        bpy.ops.mesh.primitive_cube_add(size=2, location=(1, 0, 0))
        cutter = bpy.context.object
        cutter.hide_render = True
        cutter.data.materials.append(material)
        original_mesh = cutter.data
        original_faces = [(tuple(p.vertices), p.material_index) for p in cutter.data.polygons]
        initial_objects = set(bpy.data.objects)
        initial_materials = set(bpy.data.materials)
        for transparency in (0., .5, 1.):
            requested_depth = 8 if transparency < 1. else 96
            bpy.context.scene.cycles.transparent_max_bounces = requested_depth
            lid = make_cutaway_lid(target, cutter, transparency=transparency)
            assert not lid.hide_render
            assert bpy.context.scene.cycles.transparent_max_bounces == max(requested_depth, LID_TRANSPARENT_BOUNCES)
            assert lid[LID_PROPERTY] == transparency
            assert not lid.modifiers
            # The intersection is a half cube. Its new face on x=0 must be
            # absent, leaving the five original exterior faces and their color.
            assert len(lid.data.polygons) == 5, len(lid.data.polygons)
            assert all(not all(abs(lid.data.vertices[i].co.x) < 1e-6
                               for i in p.vertices) for p in lid.data.polygons)
            assert all(lid.data.materials[p.material_index] == material
                       for p in lid.data.polygons)
            _set_lid([lid], 0.)
            assert not lid.hide_render and lid[LID_PROPERTY] == 1.
            _set_lid([lid], .5)
            assert not lid.hide_render and lid[LID_PROPERTY] == .5
            bpy.data.objects.remove(lid, do_unlink=True)
        assert cutter.data == original_mesh
        assert list(cutter.data.materials) == [material]
        assert [(tuple(p.vertices), p.material_index) for p in cutter.data.polygons] == original_faces
        assert set(bpy.data.objects) == initial_objects
        assert set(bpy.data.materials) == initial_materials
        print('CUTAWAY_LID_CHECK_PASSED')
    '''))
    result = subprocess.run([blender, '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python', str(script)],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CUTAWAY_LID_CHECK_PASSED' in result.stdout
