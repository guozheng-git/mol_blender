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
        raise ValueError('Editor/controller source changed. Disconnect and open a new bridge session.')
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


def prepare(*, idle_timeout=3600):
    number(idle_timeout, 30, 86400)
    # Short, private Unix socket paths also work on macOS.
    root = Path(tempfile.mkdtemp(prefix='br-edit-', dir='/tmp')).resolve()
    try:
        identity = snapshot_source(Path(__file__).resolve().parent.parent, root / 'source')
        key = uuid.uuid4().hex
        cfg = {'id': key, 'token': uuid.uuid4().hex, 'root': str(root),
               'socket': str(root / 'editor.sock'), 'registry_file': str(registry() / (key + '.json')),
               'source_sha256': identity['sha256'], 'source_files': identity['files'],
               'idle_timeout': idle_timeout, 'status': 'prepared', 'log': str(root / 'editor.log')}
        write_json(root / 'session.json', cfg)
        write_json(Path(cfg['registry_file']), cfg)
        return cfg
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def open_editor(path, *, blender='', idle_timeout=3600):
    from .scene_edit import blender_executable
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


def attach_script(output, *, idle_timeout=3600):
    """Prepare an external, explicitly run bootstrap for an already-open desktop."""
    output = Path(output).expanduser().absolute()
    if output.suffix != '.py' or output.exists() or output.is_symlink():
        raise ValueError('Choose a new .py filename for the trusted attachment script')
    cfg = prepare(idle_timeout=idle_timeout)
    root = Path(cfg['root'])
    code = ('import sys\nfrom pathlib import Path\n'
            "if 'blender_render.editor_blender' in sys.modules:\n"
            "    raise RuntimeError('Restart Blender before attaching a different bridge source')\n"
            f'sys.path.insert(0, {str(root / "source")!r})\n'
            'from blender_render.editor_blender import start\n'
            f'start({str(root / "session.json")!r})\n')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as handle:
        handle.write(code)
    output.chmod(0o600)
    return {'session_id': cfg['id'], 'script': str(output), 'status': 'prepared',
            'instruction': 'Run this trusted external script once in the existing Blender session; do not enable auto-run scripts.'}


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


def edit(session_id, revision, changes):
    return call(session_id, 'edit', revision=revision, changes=changes)


def save_scene(session_id, revision, path, *, overwrite=False, copy=False):
    return call(session_id, 'save', revision=revision, path=str(Path(path).expanduser().absolute()),
                overwrite=overwrite, copy=copy)


def save_preset(session_id, revision, preset, *, description='', camera_policy='preserve_input',
                replace=False, allow_partial=False, catalog_dir=None, report=None):
    from .scene_edit import save_from_blend
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
    from .scene_edit import blender_executable
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
