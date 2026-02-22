"""Open and focus a molecular object from inside Blender.

Usage:
  blender --python scripts/remote_open_focus.py -- /path/to/scene.blend [object_name]

While Blender stays open, this script also listens for live commands from:
  <scene_dir>/.bridge_live_cmd.json
and writes acknowledgements to:
  <scene_dir>/.bridge_live_ack.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import bpy

_LIVE_BRIDGE_STATE: dict[str, object] = {}


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


def _vec3(values: object, name: str) -> tuple[float, float, float]:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise RuntimeError(f"'{name}' must be a list/tuple of 3 numbers.")
    try:
        return (float(values[0]), float(values[1]), float(values[2]))
    except Exception as exc:
        raise RuntimeError(f"'{name}' contains non-numeric values: {values}") from exc


def _apply_live_command(payload: dict[str, object]) -> dict[str, object]:
    op = str(payload.get("op", "set_transform"))
    target_name = str(payload.get("object") or _LIVE_BRIDGE_STATE.get("target_name") or "")
    target = bpy.data.objects.get(target_name) if target_name else None
    if target is None:
        raise RuntimeError(f"Target object '{target_name}' was not found.")

    if op == "set_transform":
        if "location" in payload:
            target.location = _vec3(payload["location"], "location")
        if "rotation_euler" in payload:
            target.rotation_euler = _vec3(payload["rotation_euler"], "rotation_euler")
        if "scale" in payload:
            target.scale = _vec3(payload["scale"], "scale")
    elif op == "translate":
        delta = _vec3(payload.get("delta_location"), "delta_location")
        target.location = (
            target.location.x + delta[0],
            target.location.y + delta[1],
            target.location.z + delta[2],
        )
    elif op == "focus_target":
        _focus_viewport(target)
    elif op == "save_scene":
        pass
    else:
        raise RuntimeError(
            f"Unsupported op '{op}'. Use one of: set_transform, translate, focus_target, save_scene."
        )

    scene_path = _LIVE_BRIDGE_STATE.get("scene_path")
    should_save = bool(payload.get("save", False)) or op == "save_scene"
    if should_save and isinstance(scene_path, Path):
        bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))

    bpy.context.view_layer.update()
    return {
        "ok": True,
        "op": op,
        "object": target.name,
        "location": [float(target.location.x), float(target.location.y), float(target.location.z)],
    }


def _write_ack(payload: dict[str, object], result: dict[str, object]) -> None:
    ack_path = _LIVE_BRIDGE_STATE.get("ack_path")
    if not isinstance(ack_path, Path):
        return
    message = {
        "seq": payload.get("seq"),
        "ts": time.time(),
        **result,
    }
    try:
        ack_path.write_text(json.dumps(message, indent=2), encoding="utf-8")
    except Exception:
        pass


def _poll_live_bridge() -> float:
    cmd_path = _LIVE_BRIDGE_STATE.get("cmd_path")
    if not isinstance(cmd_path, Path):
        return 0.2

    try:
        if not cmd_path.exists():
            return 0.2
        mtime_ns = cmd_path.stat().st_mtime_ns
    except Exception:
        return 0.2

    last_mtime_ns = _LIVE_BRIDGE_STATE.get("last_mtime_ns")
    if isinstance(last_mtime_ns, int) and mtime_ns == last_mtime_ns:
        return 0.2

    _LIVE_BRIDGE_STATE["last_mtime_ns"] = mtime_ns
    payload: dict[str, object]
    try:
        payload = json.loads(cmd_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Command file must contain a JSON object.")
        result = _apply_live_command(payload)
    except Exception as exc:
        payload = {"seq": None}
        result = {"ok": False, "error": str(exc)}
    _write_ack(payload, result)
    return 0.2


def _register_live_bridge(scene_path: Path, target_name: str) -> None:
    cmd_path = scene_path.parent / ".bridge_live_cmd.json"
    ack_path = scene_path.parent / ".bridge_live_ack.json"
    _LIVE_BRIDGE_STATE.clear()
    _LIVE_BRIDGE_STATE.update(
        {
            "scene_path": scene_path,
            "target_name": target_name,
            "cmd_path": cmd_path,
            "ack_path": ack_path,
            "last_mtime_ns": None,
        }
    )

    try:
        ack_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "status": "listening",
                    "target": target_name,
                    "cmd_path": str(cmd_path),
                    "ts": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    if bpy.app.timers.is_registered(_poll_live_bridge):
        bpy.app.timers.unregister(_poll_live_bridge)
    bpy.app.timers.register(_poll_live_bridge, first_interval=0.2, persistent=True)
    print(f"Live bridge listening on: {cmd_path}")


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
    _register_live_bridge(scene_path, target.name)
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
    print(f"Focused object '{target.name}' and saved scene: {scene_path}")


if __name__ == "__main__":
    main()
