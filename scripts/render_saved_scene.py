"""Render a saved .blend scene to an image in background Blender.

Usage:
  blender --background --python scripts/render_saved_scene.py -- /path/to/scene.blend /path/to/out.png [camera_name]
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy


def _parse_args() -> tuple[Path, Path, str | None]:
    if "--" not in sys.argv:
        raise RuntimeError(
            "Expected args after '--': <scene.blend> <out.png> [camera_name]"
        )
    tail = sys.argv[sys.argv.index("--") + 1 :]
    if len(tail) < 2:
        raise RuntimeError(
            f"Expected at least 2 args after '--', got {len(tail)}: {tail}"
        )

    scene_path = Path(tail[0]).expanduser().resolve()
    out_path = Path(tail[1]).expanduser().resolve()
    camera_name = tail[2] if len(tail) > 2 else None
    return scene_path, out_path, camera_name


def main() -> None:
    scene_path, out_path, camera_name = _parse_args()
    if not scene_path.exists():
        raise FileNotFoundError(f"Scene file not found: {scene_path}")

    bpy.ops.wm.open_mainfile(filepath=str(scene_path))
    scene = bpy.context.scene

    if camera_name:
        camera_obj = bpy.data.objects.get(camera_name)
        if camera_obj is None or camera_obj.type != "CAMERA":
            raise RuntimeError(f"Camera '{camera_name}' not found in scene.")
        scene.camera = camera_obj

    out_path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(out_path)
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(animation=False, write_still=True)
    print(f"Rendered {scene_path} -> {out_path}")


if __name__ == "__main__":
    main()
