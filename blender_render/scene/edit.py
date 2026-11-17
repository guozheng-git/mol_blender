"""Local Blender editing handoff and validated preset extraction. No service code."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from ..config import config_root, load_config


def changed(before, after):
    """Ignore Blender's float serialization noise, never missing/added values."""
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return not math.isclose(before, after, rel_tol=1e-6, abs_tol=1e-7)
    if isinstance(before, list) and isinstance(after, list):
        return len(before) != len(after) or any(changed(a, b) for a, b in zip(before, after))
    return before != after


def convert(value, kind):
    if kind == 'value':
        return value
    if kind not in ('hex', 'rgb', 'gray') or not isinstance(value, list) or len(value) not in (3, 4):
        raise ValueError('Unsupported color binding')
    if len(value) == 4 and not math.isclose(value[3], 1, abs_tol=1e-6):
        raise ValueError('Color-socket alpha is not supported; use the material Alpha input')
    rgb = value[:3]
    if not all(isinstance(x, (float, int)) and math.isfinite(x) and 0 <= x <= 1 for x in rgb):
        raise ValueError('Preset colors must be between 0 and 1 (no HDR colors)')
    if kind == 'hex':
        # Match the renderer's existing numeric #RRGGBB convention exactly.
        return '#' + ''.join(f'{round(x * 255):02X}' for x in rgb)
    if kind == 'gray':
        if any(not math.isclose(rgb[0], x, abs_tol=1e-6) for x in rgb[1:]):
            raise ValueError('This preset control requires a grayscale ramp stop')
        return rgb[0]
    return rgb


def set_value(document, path, value):
    """JSON keys only: scene metadata is never evaluated as Python/RNA paths."""
    if not isinstance(path, list) or not path or len(path) > 12:
        raise ValueError('Invalid preset binding path')
    current = document
    for key in path[:-1]:
        if isinstance(current, dict) and isinstance(key, str):
            current = current.setdefault(key, {})
        elif isinstance(current, list) and type(key) is int and 0 <= key < len(current):
            current = current[key]
        else:
            raise ValueError('Invalid preset binding path')
    if isinstance(current, dict) and isinstance(path[-1], str):
        current[path[-1]] = value
    elif isinstance(current, list) and type(path[-1]) is int and 0 <= path[-1] < len(current):
        current[path[-1]] = value
    else:
        raise ValueError('Invalid preset binding path')


def apply_bindings(metadata, current):
    style = copy.deepcopy(metadata['style'])
    baseline = metadata['baseline']
    edits, unsupported = [], []
    differences = sorted(key for key in baseline.keys() | current.keys()
                         if key not in baseline or key not in current or changed(baseline[key], current[key]))
    grouped = {}
    for key, binding in metadata['bindings'].items():
        grouped.setdefault(tuple(binding['path']), []).append((key, binding))
    handled, edited_materials = set(), set()
    for path, entries in grouped.items():
        keys = {key for key, _ in entries}
        if not keys.intersection(differences):
            continue
        try:
            if any(key not in baseline or key not in current for key in keys):
                raise ValueError('A bound control was removed')
            values = [convert(current[key], binding['kind']) for key, binding in entries]
            if any(changed(values[0], value) for value in values[1:]):
                raise ValueError('Set this control consistently in the base and generated per-chain materials')
            set_value(style, list(path), values[0])
            edits.append({'setting': '.'.join(map(str, path)), 'value': values[0]})
            if path[0] == 'materials':
                edited_materials.add(path[1])
        except (ValueError, KeyError, TypeError) as exc:
            unsupported.append('.'.join(map(str, path)) + ': ' + str(exc))
        handled.update(keys)
    # Freeze effective defaults in edited materials. For example, enabling
    # emission must retain the visible Emission Color socket, even if the base
    # preset omitted that value while emission was disabled.
    for path, entries in grouped.items():
        if path[0] != 'materials' or path[1] not in edited_materials:
            continue
        if any(key in differences for key, _ in entries):
            continue
        try:
            values = [convert(current[key], binding['kind']) for key, binding in entries]
            if not any(changed(values[0], value) for value in values[1:]):
                set_value(style, list(path), values[0])
        except (ValueError, KeyError, TypeError):
            pass  # An unchanged, unrepresentable default remains as authored.
    unsupported.extend(key for key in differences if key not in handled)
    return style, edits, unsupported


def blender_executable(blender='', *, gui=False):
    from ..core import _detect_host_blender_executable
    config = load_config()
    value = blender or (config.get('gui_blender') if gui else None) or config.get('blender')
    path = Path(value or _detect_host_blender_executable()).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('Blender executable is missing: ' + str(path))
    if gui:
        # The Linux Studio bpy shim is deliberately headless. A desktop Blender
        # binary is a separate opt-in setting, never an automatic installation.
        with path.open('rb') as handle:
            if handle.read(2) == b'#!':
                raise ValueError('Opening Blender needs a desktop Blender executable; configure setup --gui-blender /path/to/blender')
    return path


def open_scene(path, *, blender='', edit_bridge=False):
    """Launch a separate desktop process; no code embedded in the scene runs."""
    if edit_bridge:
        from ..editor import open_editor
        return open_editor(path, blender=blender)
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != '.blend' or not path.is_file():
        raise ValueError('Expected an existing local .blend scene')
    executable = blender_executable(blender, gui=True)
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
        env.pop(key, None)
    with path.with_suffix('.open.log').open('a') as log:
        process = subprocess.Popen([str(executable), '--factory-startup', '--disable-autoexec', str(path)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
    # A successful spawn is a launch request, not proof that a window appeared.
    return {'status': 'launched', 'pid': process.pid, 'scene': str(path),
            'log': str(path.with_suffix('.open.log'))}


def inspect_scene(path, *, blender='', timeout=120):
    """Read a captured scene using a fresh, isolated, autoexec-disabled Blender."""
    from ..developer import snapshot_source, isolated_environment, digest_file
    if not math.isfinite(timeout) or not 5 <= timeout <= 3600:
        raise ValueError('Scene inspection timeout must be between 5 and 3600 seconds')
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != '.blend' or not path.is_file():
        raise ValueError('Expected an existing local .blend scene')
    executable = blender_executable(blender)
    with tempfile.TemporaryDirectory(prefix='blender-preset-') as temporary:
        root = Path(temporary)
        identity = snapshot_source(Path(__file__).resolve().parents[2], root / 'source')
        # Freeze bytes before opening. The user's scene is never written by import.
        import shutil
        shutil.copyfile(path, root / 'edited.blend')
        blend_digest = digest_file(root / 'edited.blend')
        request = {'scene': str(root / 'edited.blend'), 'result': str(root / 'result.json'),
                   'source_files': identity['files']}
        (root / 'request.json').write_text(json.dumps(request))
        command = [str(executable), '--background', '--factory-startup', '--disable-autoexec',
                   '--python-exit-code', '1', '--python', str(root / 'source/blender_render/scene/capture.py'),
                   '--', str(root / 'request.json')]
        with (root / 'inspect.log').open('w') as log:
            process = subprocess.Popen(command, cwd=root, env=isolated_environment(root),
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except BaseException:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
                raise
        if code:
            raise RuntimeError('Blender scene inspection failed:\n' + (root / 'inspect.log').read_text()[-4000:])
        result = json.loads((root / 'result.json').read_text())
        result.update(blend_sha256=blend_digest, extractor_source_sha256=identity['sha256'])
        return result


def save_from_blend(path, identifier, *, description='', catalog_dir=None, blender='',
                    camera_policy='preserve_input', replace=False, allow_partial=False,
                    report=None, timeout=120):
    from ..authoring import _local_path, save_preset
    from ..core import validate_style_document
    catalog = Path(catalog_dir).expanduser() if catalog_dir else config_root() / 'presets'
    destination = _local_path(identifier, catalog)
    if destination.exists() and not replace:
        raise ValueError('Preset already exists; use --replace to create the next revision')
    if camera_policy not in ('preserve_input', 'authored'):
        raise ValueError('camera_policy must be preserve_input or authored')
    report_path = Path(report).expanduser().resolve() if report else None
    if report_path == destination.resolve():
        raise ValueError('The report and saved preset must use different paths')
    if report_path and report_path.exists():
        raise ValueError('Report already exists; choose a new filename')
    inspected = inspect_scene(path, blender=blender, timeout=timeout)
    result = inspected[camera_policy]
    result.update(blend_sha256=inspected['blend_sha256'], camera_policy=camera_policy,
                  extractor_source_sha256=inspected['extractor_source_sha256'])
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open('x') as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
    if result['unsupported'] and not allow_partial:
        details = '\n'.join(result['unsupported'][:15])
        raise ValueError('Scene has edits a preset cannot reproduce. No preset saved.\n' + details +
                         '\nKeep the .blend, or explicitly use --allow-partial to save only supported changes.' +
                         ('\nFull report: ' + str(report_path) if report_path else ' Use --report to save details.'))
    validate_style_document(result['style'])
    saved = save_preset(identifier, result['style'], catalog, description=description or 'Edited in Blender',
                        replace=replace, camera_policy=camera_policy,
                        provenance={k: result[k] for k in ('blend_sha256', 'extractor_source_sha256',
                            'changes', 'unsupported', 'ignored', 'source_engine')})
    return {'preset': saved['id'], 'path': saved['path'], 'revision': saved['revision'],
            'sha256': saved['sha256'], 'changes': result['changes'],
            'unsupported': result['unsupported'], 'ignored': result['ignored'],
            'camera_policy': camera_policy, 'partial': bool(result['unsupported'])}
