"""Local chat controller for opt-in desktop Blender sessions. No remote adapter."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from .config import config_root
from .developer import snapshot_source, source_digest, source_files
from .editor_protocol import exchange, identifier, number, private_directory, write_json


def registry():
    path = config_root().absolute() / 'editor-sessions'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return private_directory(path)


def session(session_id, *, check_source=True):
    path = registry() / (identifier(session_id) + '.json')
    if path.is_symlink():
        raise ValueError('Editor session cannot be a symlink')
    cfg = json.loads(path.read_text())
    root = private_directory(cfg['root'])
    if cfg['id'] != session_id or Path(cfg['socket']) != root / 'editor.sock' or not root.name.startswith('br-edit-'):
        raise ValueError('Invalid editor session paths')
    if check_source and cfg['source_sha256'] != source_digest(source_files(Path(__file__).resolve().parent)):
        raise ValueError('Editor/controller source changed. Reattach the selected session in the same window '
                         'with editor attach-script --reconnect SESSION_ID; preserve unsaved edits. Do not open another window.')
    return cfg


def public(cfg):
    return {key: cfg[key] for key in ('id', 'status', 'scene', 'pid', 'source_sha256', 'log') if key in cfg}


def sessions():
    results = []
    for path in sorted(registry().glob('*.json')):
        try:
            cfg = session(path.stem, check_source=False)
            value = public(cfg)
            if cfg.get('pid'):
                try:
                    os.kill(cfg['pid'], 0)
                except ProcessLookupError:
                    value['status'] = 'exited'
            results.append(value)
        except (ValueError, OSError):
            results.append({'id': path.stem, 'status': 'unavailable'})
    return results


def forget(session_id):
    """Remove only a prepared/abandoned capture, never a running desktop."""
    cfg = session(session_id, check_source=False)
    launcher = Path(cfg['root']) / 'launcher.json'
    pid = cfg.get('pid') or (json.loads(launcher.read_text())['pid'] if launcher.exists() else None)
    if pid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise ValueError('Desktop is still running; use disconnect instead')
    elif Path(cfg['socket']).exists():
        raise ValueError('Attachment may be starting; use disconnect once it is ready')
    Path(cfg['registry_file']).unlink(missing_ok=True)
    shutil.rmtree(cfg['root'])
    return {'status': 'removed', 'session_id': session_id}


def prepare(*, idle_timeout=3600, session_id=None, publish=True):
    number(idle_timeout, 30, 86400)
    if session_id is not None:
        identifier(session_id)
    # Short, private Unix socket paths also work on macOS.
    root = Path(tempfile.mkdtemp(prefix='br-edit-', dir='/tmp')).resolve()
    try:
        identity = snapshot_source(Path(__file__).resolve().parent.parent, root / 'source')
        key = session_id or uuid.uuid4().hex
        cfg = {'id': key, 'token': uuid.uuid4().hex, 'root': str(root),
               'socket': str(root / 'editor.sock'), 'registry_file': str(registry() / (key + '.json')),
               'source_sha256': identity['sha256'], 'source_files': identity['files'],
               'idle_timeout': idle_timeout, 'status': 'prepared', 'log': str(root / 'editor.log')}
        write_json(root / 'session.json', cfg)
        if publish:
            write_json(Path(cfg['registry_file']), cfg)
        return cfg
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def open_editor(path, *, blender='', idle_timeout=3600):
    from .scene.edit import blender_executable
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != '.blend' or not path.is_file():
        raise ValueError('Expected an existing trusted local .blend')
    executable = blender_executable(blender, gui=True)
    cfg = prepare(idle_timeout=idle_timeout)
    root = Path(cfg['root'])
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
        env.pop(key, None)
    try:
        with Path(cfg['log']).open('w') as log:
            process = subprocess.Popen([str(executable), '--factory-startup', '--disable-autoexec', str(path),
                '--python', str(root / 'source/blender_render/editor_blender.py'), '--', str(root / 'session.json')],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        # Separate from the ready registry to avoid racing the desktop's update.
        write_json(root / 'launcher.json', {'pid': process.pid})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = json.loads(Path(cfg['registry_file']).read_text())
            if current.get('status') == 'ready':
                return {**public(current), 'session_id': cfg['id']}
            if process.poll() is not None:
                raise RuntimeError('Desktop bridge failed to start: ' + Path(cfg['log']).read_text()[-2000:])
            time.sleep(.1)
        # Do not close a desktop that may contain edits. Keep the known ID for recovery.
        return {**public(cfg), 'status': 'starting', 'session_id': cfg['id'], 'pid': process.pid}
    except BaseException:
        # Only remove a failed startup's capture; never kill an editor with work in it.
        if 'process' not in locals() or process.poll() is not None:
            Path(cfg['registry_file']).unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)
        raise


def attach_script(output, *, idle_timeout=3600, reconnect_session=None):
    """Prepare an external, explicitly run bootstrap for an already-open desktop."""
    output = Path(output).expanduser().absolute()
    if output.suffix != '.py' or output.exists() or output.is_symlink():
        raise ValueError('Choose a new .py filename for the trusted attachment script')
    if reconnect_session is not None:
        identifier(reconnect_session)
    # Keep a live registry entry intact until the same desktop executes the
    # replacement bootstrap. Preparing a reconnect never changes that desktop.
    cfg = prepare(idle_timeout=idle_timeout, session_id=reconnect_session,
                  publish=reconnect_session is None)
    root = Path(cfg['root'])
    if reconnect_session:
        code = ('import sys\nfrom pathlib import Path\n'
                f'sys.path.insert(0, {str(root / "source")!r})\n'
                'import runpy\n'
                f'runpy.run_path({str(root / "source/blender_render/editor_reattach.py")!r}, '
                f'init_globals={{"CONFIG_PATH": {str(root / "session.json")!r}}}, run_name="__main__")\n')
    else:
        code = ('import sys\nfrom pathlib import Path\n'
            "if 'blender_render.editor_blender' in sys.modules:\n"
            "    raise RuntimeError('Use attach-script --reconnect SESSION_ID in this same window')\n"
            f'sys.path.insert(0, {str(root / "source")!r})\n'
            'from blender_render.editor_blender import start\n'
            f'start({str(root / "session.json")!r})\n')
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x') as handle:
            handle.write(code)
        output.chmod(0o600)
    except BaseException:
        if reconnect_session is None:
            Path(cfg['registry_file']).unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)
        raise
    return {'session_id': cfg['id'], 'script': str(output), 'status': 'prepared',
            'instruction': 'Run this trusted external script once in the selected existing Blender window; '
                           'it preserves unsaved edits. Do not enable auto-run scripts or open another window.'}


def operation(session_id, operation_id):
    return exchange(session(session_id, check_source=False), {'transport': 'result', 'operation': identifier(operation_id)})


def call(session_id, action, *, timeout=30, **fields):
    cfg = session(session_id, check_source=action != 'disconnect')
    op = uuid.uuid4().hex
    try:
        result = exchange(cfg, {'transport': 'submit', 'operation': op, 'command': {'action': action, **fields}})
        deadline = time.monotonic() + timeout
        while result['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(.1)
            result = exchange(cfg, {'transport': 'result', 'operation': op})
    except (OSError, ConnectionError) as exc:
        raise RuntimeError(f'Editor outcome is uncertain; recover operation {op} in session {session_id}. Do not repeat the edit. {exc}') from exc
    if result['status'] == 'failed':
        raise ValueError(result['message'])
    if result['status'] != 'completed':
        raise RuntimeError(f'Editor operation {op} is {result["status"]}; recover it without resubmitting')
    return {**result['result'], 'operation': op}


def inspect(session_id):
    return call(session_id, 'inspect')


def reconnect(session_id):
    """Reconnect only the selected IPC session. Never launch, load or replace a scene."""
    identifier(session_id)
    try:
        state = inspect(session_id)
    except (OSError, ValueError, RuntimeError) as exc:
        return {'session_id': session_id, 'status': 'unavailable', 'new_window_opened': False,
                'error': str(exc), 'recovery': 'Keep the selected Blender window and its unsaved edits. '
                'Retry editor_reconnect for a transient connection failure. If its bridge expired or '
                'the package changed, generate editor attach-script --reconnect ' + session_id +
                ' --output NEW_SCRIPT.py and run that trusted script in the same window.'}
    return {'session_id': session_id, 'status': 'connected', 'new_window_opened': False, **state}


def edit(session_id, revision, changes):
    return call(session_id, 'edit', revision=revision, changes=changes)


def animation_speed(session_id, revision, multiplier, *, control=''):
    number(multiplier, 0.125, 8)
    if not isinstance(control, str) or len(control) > 1024:
        raise ValueError('Expected an inspected animation control ID')
    return call(session_id, 'animation_speed', revision=revision, control=control, multiplier=multiplier)


def viewport(session_id, revision, mode, *, overlays=None):
    from .editor_protocol import checked_value
    checked_value(mode, {'type': 'enum', 'choices': ['solid_color', 'material', 'rendered', 'wireframe']})
    if overlays is not None:
        checked_value(overlays, {'type': 'boolean'})
    return call(session_id, 'viewport', revision=revision, mode=mode, overlays=overlays)


def playback(session_id, state):
    from .editor_protocol import checked_value
    checked_value(state, {'type': 'enum', 'choices': ['play', 'pause']})
    return call(session_id, 'playback', state=state)


def save_scene(session_id, revision, path, *, overwrite=False, copy=False):
    return call(session_id, 'save', revision=revision, path=str(Path(path).expanduser().absolute()),
                overwrite=overwrite, copy=copy)


def save_preset(session_id, revision, preset, *, description='', camera_policy='preserve_input',
                replace=False, allow_partial=False, catalog_dir=None, report=None):
    from .scene.edit import save_from_blend
    with tempfile.TemporaryDirectory(prefix='br-editor-preset-') as temporary:
        captured = save_scene(session_id, revision, Path(temporary) / 'scene.blend', copy=True)
        result = save_from_blend(captured['scene'], preset, description=description,
            camera_policy=camera_policy, replace=replace, allow_partial=allow_partial,
            catalog_dir=catalog_dir, report=report)
    return {**result, 'scene_revision': revision, 'operation': captured['operation']}


def job_root(job_id):
    root = config_root().absolute() / 'editor-jobs'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_directory(root)
    return root / identifier(job_id)


def render_scene(session_id, revision, output, *, preview=True, width=None, height=None,
                 samples=None, device='CPU', timeout=600):
    """Capture once, then render asynchronously without occupying the editor UI."""
    if device not in ('CPU', 'GPU'):
        raise ValueError('device must be CPU or GPU')
    number(timeout, 10, 3600)
    if type(preview) is not bool:
        raise ValueError('preview must be a boolean')
    for value, maximum in ((width, 8192), (height, 8192), (samples, 4096)):
        if value is not None:
            number(value, 1, maximum, integer=True)
    output = Path(output).expanduser().absolute()
    artifacts = output.with_suffix('.editor')
    if output.suffix.lower() != '.png' or output.exists() or output.is_symlink() or artifacts.exists():
        raise ValueError('Choose a new .png output; its .editor artifact folder must also be new')
    cfg = session(session_id)
    job_id = uuid.uuid4().hex
    root = job_root(job_id)
    root.mkdir(mode=0o700)
    from .scene.edit import blender_executable
    executable = blender_executable()
    try:
        identity = snapshot_source(Path(__file__).resolve().parent.parent, root / 'source')
        artifacts.mkdir(parents=True, mode=0o700)
        captured = save_scene(session_id, revision, artifacts / 'scene.blend', copy=True)
        spec = {'job_id': job_id, 'session_id': session_id, 'revision': revision, 'preview': preview,
                'width': width, 'height': height, 'samples': samples, 'device': device, 'timeout': timeout,
                'scene': captured['scene'], 'blend_sha256': captured['sha256'], 'output': str(output),
                'artifacts': str(artifacts), 'root': str(root), 'blender': str(executable),
                'source_files': identity['files'], 'source_sha256': identity['sha256']}
        write_json(root / 'request.json', spec)
        write_json(artifacts / 'source.json', identity)
        env = os.environ.copy()
        for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
            env.pop(key, None)
        with (artifacts / 'controller.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-I', str(root / 'source/blender_render/editor_render.py'),
                str(root / 'request.json')], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=env, start_new_session=True)
        receipt = {'job_id': job_id, 'status': 'running', 'pid': process.pid, 'output': str(output),
                   'artifacts': str(artifacts), 'scene_revision': revision}
        write_json(root / 'receipt.json', receipt)
        return receipt
    except BaseException:
        shutil.rmtree(root / 'source', ignore_errors=True)
        raise


def job(job_id):
    root = private_directory(job_root(job_id))
    receipt = json.loads((root / 'receipt.json').read_text())
    result = root / 'result.json'
    if result.is_file():
        return {**receipt, **json.loads(result.read_text())}
    try:
        os.kill(receipt['pid'], 0)
    except ProcessLookupError:
        return {**receipt, 'status': 'failed', 'error': 'Render controller exited without a result; inspect controller.log. No retry was submitted.'}
    return receipt
