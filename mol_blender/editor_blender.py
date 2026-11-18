# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in desktop bridge: socket I/O off-thread, all bpy work on a UI timer."""
from __future__ import annotations
import atexit
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy
from mol_blender import editor_controls as controls
from mol_blender.scene import capture
from mol_blender.editor_protocol import MAX_OPERATIONS, number, receive, send, write_json

_ACTIVE = None


class Bridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.scene = bpy.context.scene
        self.stopped = threading.Event()
        self.pending = queue.Queue(maxsize=8)
        self.jobs = {}
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        self.undo = []
        self.closing = False
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(cfg['socket'])
        os.chmod(cfg['socket'], 0o600)
        self.listener.listen(4)
        self.listener.settimeout(.5)

    def transport(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(2)
                try:
                    message = receive(connection)
                    if not isinstance(message.get('token'), str) or not hmac.compare_digest(message['token'], self.cfg['token']):
                        raise ValueError('Invalid editor session token')
                    action = message.get('transport')
                    op = message.get('operation')
                    from mol_blender.editor_protocol import identifier
                    identifier(op)
                    with self.lock:
                        self.last_used = time.monotonic()
                        if action == 'submit':
                            command = message.get('command')
                            if not isinstance(command, dict):
                                raise ValueError('Expected an editor command')
                            signature = capture.digest(command)
                            if op in self.jobs:
                                if self.jobs[op]['signature'] != signature:
                                    raise ValueError('Operation ID already has a different command')
                            else:
                                if self.closing:
                                    raise ValueError('Editor bridge is disconnecting; no operation was queued')
                                if len(self.jobs) >= MAX_OPERATIONS:
                                    raise ValueError('Editor operation limit reached; disconnect and reattach')
                                if self.pending.full():
                                    raise ValueError('Editor is busy; no operation was queued')
                                self.jobs[op] = {'signature': signature, 'status': 'queued'}
                                self.pending.put_nowait((op, command))
                        elif action != 'result':
                            raise ValueError('Unsupported editor transport action')
                        job = self.jobs.get(op, {'status': 'unknown'})
                        result = {'operation': op, **{k: v for k, v in job.items() if k != 'signature'}}
                    send(connection, result)
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    try:
                        send(connection, {'error': str(exc)[:2000]})
                    except OSError:
                        pass

    def dispatch(self, command):
        if not isinstance(command, dict):
            raise ValueError('Expected an editor command')
        action = command.get('action')
        allowed = {'inspect': {'action'}, 'edit': {'action', 'revision', 'changes'},
                   'animation_speed': {'action', 'revision', 'control', 'multiplier'},
                   'viewport': {'action', 'revision', 'mode', 'overlays'},
                   'playback': {'action', 'state'},
                   'save': {'action', 'revision', 'path', 'copy', 'overwrite'},
                   'undo': {'action', 'revision'}, 'pause': {'action'}, 'disconnect': {'action'}}
        if action not in allowed or set(command) - allowed[action]:
            raise ValueError('Unknown editor action or fields')
        if action == 'disconnect':
            self.closing = True
            return {'status': 'disconnected', 'editor_left_open': True}
        if bpy.context.scene != self.scene:
            raise ValueError('Active Blender scene changed; reattach to select the new scene')
        if action in ('pause', 'playback'):
            from mol_blender.editor_interaction import playback
            return playback(self.scene, 'pause' if action == 'pause' else command.get('state'))
        scene = bpy.context.scene
        if action != 'inspect' and any(w.scene == scene and w.screen.is_animation_playing for w in bpy.context.window_manager.windows):
            raise ValueError('Pause timeline playback before editing, saving or rendering')
        if action != 'inspect' and (bpy.context.mode != 'OBJECT' or bpy.app.is_job_running('RENDER')):
            raise ValueError('Finish the active edit mode or render before changing or saving the scene')
        data = controls.metadata()
        current_revision = controls.revision(scene, data)
        if action != 'inspect' and command.get('revision') != current_revision:
            raise ValueError('Scene changed since inspection. Inspect again before editing, saving or rendering.')
        if action == 'inspect':
            return controls.inspect(scene)
        if action in ('edit', 'animation_speed', 'viewport'):
            if action == 'edit':
                result, before = controls.edit(scene, command.get('changes'))
            else:
                from mol_blender.editor_interaction import animation_speed, viewport
                if action == 'animation_speed':
                    result, before = animation_speed(scene, command.get('control', ''), command.get('multiplier'))
                else:
                    result, before = viewport(scene, command.get('mode'), command.get('overlays'))
            self.undo.append((result['revision'], before))
            self.undo = self.undo[-32:]
            return result
        if action == 'undo':
            if not self.undo or self.undo[-1][0] != current_revision:
                raise ValueError('No chat edit can be undone at this revision; manual changes are preserved')
            _, before = self.undo[-1]
            result = controls.restore(scene, before)
            self.undo.pop()
            # Keep the preceding edit's actual revision. Re-labelling it here
            # would allow a second undo to cross an intervening manual edit.
            return result
        if action == 'save':
            value = command.get('path')
            if not isinstance(value, str) or len(value) > 4096 or not Path(value).is_absolute():
                raise ValueError('Expected an absolute local .blend path')
            path = Path(value)
            if path.is_symlink() or path.suffix.lower() != '.blend':
                raise ValueError('Expected a regular .blend destination')
            overwrite, copy = command.get('overwrite', False), command.get('copy', False)
            if type(overwrite) is not bool or type(copy) is not bool:
                raise ValueError('copy and overwrite must be booleans')
            if path.exists() and not overwrite:
                raise ValueError('Scene already exists; choose a new path or explicitly request overwrite')
            path.parent.mkdir(parents=True, exist_ok=True)
            result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=copy, check_existing=False)
            if 'FINISHED' not in result:
                raise RuntimeError('Blender did not save the scene')
            return {'scene': str(path), 'copy': copy, 'revision': controls.revision(scene, data),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def tick(self):
        if self.stopped.is_set():
            return None
        if time.monotonic() - self.last_used > self.cfg['idle_timeout']:
            self.stop()
            return None
        try:
            op, command = self.pending.get_nowait()
        except queue.Empty:
            return .1
        with self.lock:
            self.jobs[op]['status'] = 'running'
        try:
            result = self.dispatch(command)
            state = {'status': 'completed', 'result': result}
        except Exception as exc:
            state = {'status': 'failed', 'message': str(exc)[:4000]}
        with self.lock:
            self.jobs[op].update(state)
        if command.get('action') == 'disconnect' and state['status'] == 'completed':
            # Allow the controller to retrieve the receipt before teardown.
            bpy.app.timers.register(self.finish_disconnect, first_interval=1)
            return None
        return .1

    def finish_disconnect(self):
        self.stop()

    def before_load(self, *_):
        self.stop()  # Never follow the user into another file without a new opt-in.

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        self.listener.close()
        if bpy.app.timers.is_registered(self.tick):
            bpy.app.timers.unregister(self.tick)
        if self.before_load in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.remove(self.before_load)
        if bpy.app.driver_namespace.get('mol_blender_editor_bridge') is self:
            bpy.app.driver_namespace.pop('mol_blender_editor_bridge')
        Path(self.cfg['registry_file']).unlink(missing_ok=True)
        shutil.rmtree(self.cfg['root'], ignore_errors=True)


def start(config_path):
    global _ACTIVE
    if _ACTIVE and not _ACTIVE.stopped.is_set():
        raise ValueError('This Blender process already has an editing bridge')
    cfg = json.loads(Path(config_path).read_text())
    from mol_blender.developer import source_files
    if source_files(Path(__file__).resolve().parent) != cfg['source_files']:
        raise ValueError('Editor bridge source changed; prepare a new session')
    lock = json.loads(Path(__file__).with_name('runtime.lock.json').read_text())
    if bpy.app.version_string != lock['blender']:
        raise ValueError('Editor bridge requires Blender ' + lock['blender'])
    if bpy.app.background:
        raise ValueError('The editor bridge requires a desktop Blender session')
    controls.metadata()
    _ACTIVE = Bridge(cfg)
    bpy.app.driver_namespace['mol_blender_editor_bridge'] = _ACTIVE
    thread = threading.Thread(target=_ACTIVE.transport, daemon=True, name='mol-blender editor transport')
    thread.start()
    bpy.app.timers.register(_ACTIVE.tick, first_interval=.1)
    bpy.app.handlers.load_pre.append(_ACTIVE.before_load)
    atexit.register(_ACTIVE.stop)
    cfg.update(pid=os.getpid(), status='ready', scene=bpy.data.filepath)
    write_json(Path(cfg['registry_file']), cfg)
    print('mol-blender chat bridge ready: ' + cfg['id'], flush=True)
    return {'session_id': cfg['id'], 'status': 'ready'}


if __name__ == '__main__':
    start(sys.argv[sys.argv.index('--') + 1])
