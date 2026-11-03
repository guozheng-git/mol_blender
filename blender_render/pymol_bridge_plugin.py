"""PyMOL command adapter. Export the current pose and render with local Blender."""
from __future__ import annotations
import json
import sys
from pathlib import Path
# PyMOL's default `run` executes in its own globals: __file__ still names
# pymol/__init__.py, while __script__ identifies the file being run.
_PACKAGE_ROOT = str(Path(globals().get("__script__", __file__)).resolve().parent.parent)
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)
from blender_render.presets import DEFAULT_PRESET
from blender_render.export import build_render_payload, _normalize_cli_string, _parse_bool


def try_load_preview(cmd_obj, output_png):
    try:
        cmd_obj.load_png(str(output_png), movie=1, quiet=1)
        return True
    except Exception:
        print("Rendered PNG: " + str(output_png))
        return False


def bridge_render(selection="(all)", preset=DEFAULT_PRESET, style_json="",
                  output_dir=".", output_name="render.png", rough=0, color_follow="",
                  save_blend="", state=-1, blender_exe="", keep_temp=0,
                  width="", height="", samples="", transparent_background="",
                  _self=None, overrides_json="", preserve_view="", timeout=600,
                  animation="", duration="", fps="", rock_angle="", gif="", auto_preview=1,
                  cluster="", wait=0, warm=0, open_blender=0, gui_blender="", edit_bridge=0):
    if _normalize_cli_string(cluster) and _parse_bool(warm):
        raise ValueError('warm=1 selects the local worker; omit cluster or use warm=0 for Slurm')
    if _normalize_cli_string(cluster) and (_parse_bool(open_blender) or _parse_bool(edit_bridge) or _normalize_cli_string(gui_blender)):
        raise ValueError('open_blender selects local desktop editing; fetch a Slurm scene with save_blend=1 and open it locally')
    if _self is None:
        from pymol import cmd
    else:
        cmd = _self
    from blender_render.local_bridge import render_payload
    text = _normalize_cli_string
    payload = build_render_payload(cmd, text(selection), int(state), include_color_hints=True)
    options = {"preset": text(preset), "rough": _parse_bool(rough)}
    for key, value in (("color_follow", color_follow),):
        if str(value).strip():
            options[key] = _parse_bool(value)
    if text(style_json):
        options["style_doc"] = json.loads(Path(text(style_json)).expanduser().read_text())
        options.pop("preset", None)
    if text(overrides_json):
        options["overrides"] = json.loads(Path(text(overrides_json)).expanduser().read_text())
    if str(save_blend).strip():
        options["save_blend"] = _parse_bool(save_blend)
    if str(preserve_view).strip():
        options["preserve_view"] = _parse_bool(preserve_view)
    for key, value in (("width", width), ("height", height), ("samples", samples)):
        if str(value).strip():
            options[key] = int(value)
    if str(transparent_background).strip():
        options["transparent_background"] = _parse_bool(transparent_background)
    for key, value, convert in [('animation', animation, text), ('duration', duration, float),
                                ('fps', fps, int), ('rock_angle', rock_angle, float), ('gif', gif, _parse_bool)]:
        if str(value).strip():
            options[key] = convert(value)
    output = (Path(text(output_dir) or ".").expanduser() / (text(output_name) or "render.png")).with_suffix(".png").resolve()
    if text(cluster):
        if text(blender_exe) or _parse_bool(keep_temp):
            raise ValueError('Cluster jobs use administrator-configured runtime and retention')
        from blender_render.cluster_bridge import submit
        result = submit(payload, output=output, options=options, profile=text(cluster),
                        wait=_parse_bool(wait), timeout=float(timeout))
        if 'outputs' not in result:
            print(json.dumps(result, indent=2))
            return result
    else:
        result = render_payload(payload, output=output, options=options, blender=text(blender_exe),
                       timeout=float(timeout), keep_temp=_parse_bool(keep_temp), warm=_parse_bool(warm),
                       open_blender=_parse_bool(open_blender), gui_blender=text(gui_blender), edit_bridge=_parse_bool(edit_bridge))
    if result and result.get('editor'):
        print(json.dumps({'editor': result['editor']}, indent=2))
    return _present_result(cmd, result, output, auto_preview)


def _present_result(cmd, result, output, auto_preview):
    if result and 'frames.zip' in result.get('outputs', {}):
        from blender_render.pymol_movie import remember, load_movie
        archive = result['outputs']['frames.zip']
        try:
            remember(cmd, archive)
            result['presentation'] = load_movie(cmd, archive) if _parse_bool(auto_preview) else 'fallback_path'
        except Exception as exc:
            result['presentation'] = 'fallback_path'
            print('Movie files saved; PyMOL preview unavailable: ' + str(exc))
        print(json.dumps(result, indent=2))
        return result
    if _parse_bool(auto_preview):
        try_load_preview(cmd, output)
    print(str(output))
    return str(output)


def bridge_cluster_status(job, cluster="default", _self=None):
    from blender_render.cluster_bridge import command
    result = command(['status', _normalize_cli_string(job), '--profile', _normalize_cli_string(cluster)])
    print(json.dumps(result, indent=2))
    return result


def bridge_cluster_cancel(job, cluster="default", _self=None):
    from blender_render.cluster_bridge import command
    result = command(['cancel', _normalize_cli_string(job), '--profile', _normalize_cli_string(cluster)])
    print(json.dumps(result, indent=2))
    return result


def bridge_cluster_fetch(job, cluster="default", output_dir=".", output_name="render.png", auto_preview=1, _self=None):
    from blender_render.cluster_bridge import command
    if _self is None:
        from pymol import cmd
    else:
        cmd = _self
    text = _normalize_cli_string
    output = (Path(text(output_dir)).expanduser() / text(output_name)).with_suffix('.png').resolve()
    result = command(['fetch', text(job), '--profile', text(cluster), '--output', str(output)])
    return _present_result(cmd, result, output, auto_preview)

def bridge_movie_export(filename, source="", _self=None):
    from blender_render.pymol_movie import movie_export
    return movie_export(_normalize_cli_string(filename), _normalize_cli_string(source), _self=_self)


def bridge_preset_from_blend(filename, preset, description="", camera_policy="preserve_input",
                             replace=0, allow_partial=0, report="", _self=None):
    """Save supported edits from a local .blend as user/name; never modifies the scene."""
    from blender_render.local_bridge import preset_from_blend
    text = _normalize_cli_string
    result = preset_from_blend(text(filename), text(preset), description=text(description),
                               camera_policy=text(camera_policy), replace=_parse_bool(replace),
                               allow_partial=_parse_bool(allow_partial), report=text(report))
    print(json.dumps(result, indent=2))
    return result


blender_render = bridge_render

def register_plugin(cmd_obj=None):
    if cmd_obj is None:
        from pymol import cmd as cmd_obj
    cmd_obj.extend("blender_render", bridge_render)
    cmd_obj.extend("blender_movie_export", bridge_movie_export)
    cmd_obj.extend("blender_render_status", bridge_cluster_status)
    cmd_obj.extend("blender_render_fetch", bridge_cluster_fetch)
    cmd_obj.extend("blender_render_cancel", bridge_cluster_cancel)
    cmd_obj.extend("blender_preset_from_blend", bridge_preset_from_blend)

try:
    from pymol import cmd
    register_plugin(cmd)
except ImportError:
    pass
