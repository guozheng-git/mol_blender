"""Open and focus a molecular object from inside Blender.

Usage:
  blender --python scripts/remote_open_focus.py -- /path/to/scene.blend [object_name]
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


def _args_after_double_dash() -> list[str]:
    if "--" not in sys.argv:
        raise RuntimeError(
            "Expected args after '--': /path/to/scene.blend [object_name]"
        )
    idx = sys.argv.index("--")
    return sys.argv[idx + 1 :]


def _open_scene(scene_path: Path) -> None:
    if not scene_path.exists():
        raise FileNotFoundError(f"Scene file not found: {scene_path}")
    bpy.ops.wm.open_mainfile(filepath=str(scene_path))


def _unhide_all_objects() -> None:
    for obj in bpy.data.objects:
        obj.hide_viewport = False
        obj.hide_render = False
        try:
            obj.hide_set(False)
        except Exception:
            pass


def _choose_target_object(name: str | None) -> bpy.types.Object:
    if name:
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise RuntimeError(f"Requested object '{name}' was not found.")
        return obj

    preferred = bpy.data.objects.get("1A3N")
    if preferred is not None:
        return preferred

    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.name.lower() != "cube":
            return obj
    raise RuntimeError("Could not find a mesh object to focus.")


def _hide_non_target_meshes(target: bpy.types.Object) -> None:
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.name == target.name:
            continue
        obj.hide_viewport = True
        obj.hide_render = True
        try:
            obj.hide_set(True)
        except Exception:
            pass


def _focus_viewport(target: bpy.types.Object) -> None:
    wm = bpy.context.window_manager
    if not wm.windows:
        return

    window = wm.windows[0]
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer

    for obj in view_layer.objects:
        obj.select_set(False)
    target.select_set(True)
    view_layer.objects.active = target

    scene.camera = bpy.data.objects.get("PyMOL_Camera") or scene.camera
    _hide_non_target_meshes(target)

    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        region = next((r for r in area.regions if r.type == "WINDOW"), None)
        if region is None:
            continue
        space = area.spaces.active
        if space.type != "VIEW_3D":
            continue

        with bpy.context.temp_override(
            window=window,
            area=area,
            region=region,
            scene=scene,
            view_layer=view_layer,
            active_object=target,
            selected_objects=[target],
            selected_editable_objects=[target],
        ):
            try:
                space.shading.type = "MATERIAL"
            except Exception:
                pass
            if scene.camera is not None:
                space.region_3d.view_perspective = "CAMERA"


def main() -> None:
    argv = _args_after_double_dash()
    if not argv:
        raise RuntimeError("Provide at least a .blend path after '--'.")

    scene_path = Path(argv[0]).expanduser().resolve()
    object_name = argv[1] if len(argv) > 1 else "1A3N"

    _open_scene(scene_path)
    _unhide_all_objects()
    target = _choose_target_object(object_name)
    _focus_viewport(target)
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
    print(f"Focused object '{target.name}' and saved scene: {scene_path}")


if __name__ == "__main__":
    main()
