"""Blender script generation for loading Molecular Nodes bundles."""

from __future__ import annotations

import json
import textwrap

from .view_math import CameraSpec


def render_import_script(camera_spec: CameraSpec) -> str:
    """Render Blender Python script text for bundle import + camera setup."""

    matrix_json = json.dumps(camera_spec.matrix_world)
    camera_type = camera_spec.camera_type
    fov_deg = float(camera_spec.fov_deg)
    ortho_scale = camera_spec.ortho_scale if camera_spec.ortho_scale is not None else 0.0
    clip_start = float(camera_spec.clip_start)
    clip_end = float(camera_spec.clip_end)

    return textwrap.dedent(
        f"""\
        import json
        import math
        import sys
        from pathlib import Path

        import bpy
        from mathutils import Matrix

        MATRIX_WORLD = {matrix_json}
        CAMERA_TYPE = {camera_type!r}
        FOV_DEG = {fov_deg!r}
        ORTHO_SCALE = {ortho_scale!r}
        CLIP_START = {clip_start!r}
        CLIP_END = {clip_end!r}
        MN_WORLD_SCALE = 0.01

        def parse_args():
            if "--" not in sys.argv:
                raise RuntimeError(
                    "Expected Blender args after '--': <bundle_dir> <out_blend>"
                )
            idx = sys.argv.index("--")
            tail = sys.argv[idx + 1:]
            if len(tail) != 2:
                raise RuntimeError(
                    f"Expected exactly 2 args after '--', got {{len(tail)}}: {{tail}}"
                )
            return Path(tail[0]).expanduser().resolve(), Path(tail[1]).expanduser().resolve()

        def load_molecule_with_fallback(mn_module, path, name):
            attempts = []
            errors = []

            load_ns = getattr(mn_module, "load", None)
            if load_ns is not None:
                candidates = [
                    ("mn.load.molecule_local", getattr(load_ns, "molecule_local", None)),
                    ("mn.load.load_molecule_local", getattr(load_ns, "load_molecule_local", None)),
                ]
                for label, fn in candidates:
                    if fn is None:
                        continue
                    attempts.append(label + "(path, name=..., starting_style=1)")
                    try:
                        return fn(str(path), name=name, starting_style=1)
                    except TypeError as exc:
                        errors.append(f"{{label}} with starting_style failed: {{exc}}")
                        attempts.append(label + "(path, name=...)")
                        try:
                            return fn(str(path), name=name)
                        except Exception as exc2:
                            errors.append(f"{{label}} without starting_style failed: {{exc2}}")
                    except Exception as exc:
                        errors.append(f"{{label}} failed: {{exc}}")

            # Newer Molecular Nodes API path.
            molecule_cls = None
            if hasattr(mn_module, "entities") and hasattr(mn_module.entities, "Molecule"):
                molecule_cls = mn_module.entities.Molecule
            else:
                try:
                    from molecularnodes.entities import Molecule as ImportedMolecule
                    molecule_cls = ImportedMolecule
                except Exception as exc:
                    errors.append(f"molecularnodes.entities.Molecule import failed: {{exc}}")

            if molecule_cls is not None and hasattr(molecule_cls, "load"):
                attempts.append("molecularnodes.entities.Molecule.load(path, name=...)")
                try:
                    return molecule_cls.load(path, name=name)
                except Exception as exc:
                    errors.append(f"Molecule.load failed: {{exc}}")

            attempted_text = ", ".join(attempts) if attempts else "none"
            error_text = " | ".join(errors) if errors else "no loader symbols found"
            raise RuntimeError(
                "Could not load structure through Molecular Nodes. "
                f"Attempted: {{attempted_text}}. Details: {{error_text}}"
            )

        def resolve_loaded_object(load_result, object_name):
            obj = None
            if load_result is not None:
                # Legacy loader can return Blender Object directly.
                if hasattr(load_result, "type") and hasattr(load_result, "name"):
                    obj = load_result
                # New API returns Molecule entity object.
                elif hasattr(load_result, "object"):
                    obj = getattr(load_result, "object")
            if obj is None:
                obj = bpy.data.objects.get(object_name)
            return obj

        def has_stub_node_group(obj):
            if obj is None:
                return False
            for modifier in obj.modifiers:
                if modifier.type != "NODES":
                    continue
                node_group = modifier.node_group
                if node_group is None:
                    continue
                # Input + output only means no visual style has been created yet.
                if len(node_group.nodes) <= 2:
                    return True
            return False

        def ensure_visible_style(load_result, obj):
            if obj is None:
                return
            if not has_stub_node_group(obj):
                return
            if load_result is None or not hasattr(load_result, "add_style"):
                return

            style_errors = []
            style_variants = [
                ("cartoon", "chain"),
                ("cartoon", "common"),
                ("spheres", "chain"),
                ("sticks", "chain"),
            ]
            for style_name, color_name in style_variants:
                try:
                    load_result.add_style(style_name, color=color_name)
                    if not has_stub_node_group(obj):
                        return
                except Exception as exc:
                    style_errors.append(f"{{style_name}}/{{color_name}}: {{exc}}")

            if has_stub_node_group(obj):
                detail = " | ".join(style_errors) if style_errors else "no style error detail"
                raise RuntimeError(
                    "Loaded molecule but failed to build a visible Molecular Nodes style. "
                    f"Details: {{detail}}"
                )

        def create_fallback_material():
            material = bpy.data.materials.get("BridgeVisible")
            if material is None:
                material = bpy.data.materials.new(name="BridgeVisible")
            material.use_nodes = True
            tree = material.node_tree
            for node in list(tree.nodes):
                tree.nodes.remove(node)
            node_output = tree.nodes.new("ShaderNodeOutputMaterial")
            node_bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
            node_bsdf.inputs["Base Color"].default_value = (0.12, 0.72, 0.95, 1.0)
            node_bsdf.inputs["Roughness"].default_value = 0.35
            if "Alpha" in node_bsdf.inputs:
                node_bsdf.inputs["Alpha"].default_value = 1.0
            tree.links.new(node_bsdf.outputs["BSDF"], node_output.inputs["Surface"])
            if hasattr(material, "blend_method"):
                material.blend_method = "OPAQUE"
            return material

        def enforce_notebook_safe_material(obj):
            # Blender 5 + Molecular Nodes extension currently renders some MN shaders as flat gray.
            # Keep MN geometry, but force an opaque visible material for reliability.
            if obj is None or obj.type != "MESH":
                return
            if bpy.app.version < (5, 0, 0):
                return
            material = create_fallback_material()
            for idx in range(len(obj.data.materials)):
                obj.data.materials[idx] = material
            if len(obj.data.materials) == 0:
                obj.data.materials.append(material)
            for modifier in obj.modifiers:
                if modifier.type != "NODES" or modifier.node_group is None:
                    continue
                for node in modifier.node_group.nodes:
                    if not node.name.startswith("Style"):
                        continue
                    socket = node.inputs.get("Material")
                    if socket is None:
                        continue
                    try:
                        socket.default_value = material
                    except Exception:
                        pass

        def ensure_camera(scene):
            cam_obj = bpy.data.objects.get("PyMOL_Camera")
            if cam_obj is not None and cam_obj.type != "CAMERA":
                raise RuntimeError("Object named 'PyMOL_Camera' exists but is not a camera.")
            if cam_obj is None:
                cam_data = bpy.data.cameras.new("PyMOL_Camera")
                cam_obj = bpy.data.objects.new("PyMOL_Camera", cam_data)
                scene.collection.objects.link(cam_obj)
            cam_obj.matrix_world = Matrix(MATRIX_WORLD)
            cam_data = cam_obj.data
            cam_data.clip_start = CLIP_START
            cam_data.clip_end = CLIP_END
            if CAMERA_TYPE == "ORTHO":
                cam_data.type = "ORTHO"
                cam_data.ortho_scale = ORTHO_SCALE
            else:
                cam_data.type = "PERSP"
                cam_data.angle = math.radians(FOV_DEG)
            scene.camera = cam_obj
            return cam_obj

        def apply_mn_world_scale_to_camera(camera_obj):
            if camera_obj is None or camera_obj.type != "CAMERA":
                return
            if not (MN_WORLD_SCALE > 0.0 and MN_WORLD_SCALE != 1.0):
                return

            matrix = camera_obj.matrix_world.copy()
            matrix.translation = matrix.translation * MN_WORLD_SCALE
            camera_obj.matrix_world = matrix

            camera_data = camera_obj.data
            camera_data.clip_start = max(1e-6, camera_data.clip_start * MN_WORLD_SCALE)
            camera_data.clip_end = max(
                camera_data.clip_start + 1e-3,
                camera_data.clip_end * MN_WORLD_SCALE,
            )
            if camera_data.type == "ORTHO":
                camera_data.ortho_scale = max(1e-6, camera_data.ortho_scale * MN_WORLD_SCALE)

        def apply_viewport_resolution_from_view(scene, view_payload):
            viewport = view_payload.get("viewport_px")
            if not isinstance(viewport, list) or len(viewport) != 2:
                return
            try:
                width = int(viewport[0])
                height = int(viewport[1])
            except Exception:
                return
            if width > 0 and height > 0:
                scene.render.resolution_x = width
                scene.render.resolution_y = height

        def ensure_light(scene):
            if any(obj.type == "LIGHT" for obj in bpy.data.objects):
                return
            light_data = bpy.data.lights.new(name="BridgeLight", type="SUN")
            light_obj = bpy.data.objects.new("BridgeLight", light_data)
            scene.collection.objects.link(light_obj)
            light_obj.location = (6.0, -6.0, 8.0)
            light_obj.rotation_euler = (0.9, 0.2, 0.7)

        def clear_default_geometry_objects():
            # Blender starts with a default Cube that can hide the imported molecule.
            removable_types = {"MESH", "CURVE", "SURFACE", "META", "FONT", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}
            for obj in list(bpy.data.objects):
                if obj.type in removable_types:
                    bpy.data.objects.remove(obj, do_unlink=True)

        def main():
            bundle_dir, out_blend = parse_args()
            bundle_json = bundle_dir / "bundle.json"
            view_json = bundle_dir / "view.json"
            structures_dir = bundle_dir / "structures"

            if not bundle_json.exists():
                raise RuntimeError(f"Missing bundle metadata: {{bundle_json}}")
            if not view_json.exists():
                raise RuntimeError(f"Missing view metadata: {{view_json}}")
            if not structures_dir.exists():
                raise RuntimeError(f"Missing structures directory: {{structures_dir}}")

            with bundle_json.open("r", encoding="utf-8") as fh:
                _bundle = json.load(fh)
            with view_json.open("r", encoding="utf-8") as fh:
                _view = json.load(fh)

            structure_files = sorted(structures_dir.glob("*.pdb")) + sorted(structures_dir.glob("*.cif"))
            if not structure_files:
                raise RuntimeError(f"No structure files found in {{structures_dir}}")

            mn = None
            import_errors = []
            try:
                import MolecularNodes as mn  # legacy module name
            except Exception as exc:
                import_errors.append(f"MolecularNodes import failed: {{exc}}")
            if mn is None:
                try:
                    import molecularnodes as mn  # Blender extensions module name
                except Exception as exc:
                    import_errors.append(f"molecularnodes import failed: {{exc}}")
            if mn is None:
                details = " | ".join(import_errors) if import_errors else "unknown import error"
                raise RuntimeError(
                    "Install/enable Molecular Nodes extension/addon in Blender before running build-blend. "
                    f"Details: {{details}}"
                )

            clear_default_geometry_objects()
            imported_objects = []
            for file_path in structure_files:
                object_name = file_path.stem
                try:
                    loaded = load_molecule_with_fallback(mn, file_path, object_name)
                    loaded_obj = resolve_loaded_object(loaded, object_name)
                    ensure_visible_style(loaded, loaded_obj)
                    enforce_notebook_safe_material(loaded_obj)
                    imported_objects.append(loaded_obj)
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to load '{{file_path.name}}' through Molecular Nodes: {{exc}}"
                    ) from exc

            scene = bpy.context.scene
            apply_viewport_resolution_from_view(scene, _view)
            camera_obj = ensure_camera(scene)
            apply_mn_world_scale_to_camera(camera_obj)
            ensure_light(scene)
            out_blend.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=str(out_blend))
            print(f"Saved Blender scene to {{out_blend}}")

        if __name__ == "__main__":
            main()
        """
    )
