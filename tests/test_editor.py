import json
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
import pytest

from blender_render import editor, editor_protocol as protocol


@pytest.mark.parametrize('value,spec', [
    (True, {'type': 'number', 'minimum': 0, 'maximum': 1}),
    (float('nan'), {'type': 'number', 'minimum': 0, 'maximum': 1}),
    (2, {'type': 'number', 'minimum': 0, 'maximum': 1}),
    ('red', {'type': 'color'}),
    ([0, 1], {'type': 'vector', 'length': 3, 'minimum': 0, 'maximum': 1}),
    ([0, 1, float('inf')], {'type': 'vector', 'length': 3, 'minimum': 0, 'maximum': 1}),
    (1, {'type': 'boolean'}),
    ('PANORAMA', {'type': 'enum', 'choices': ['ORTHO', 'PERSP']}),
    (8.5, {'type': 'integer', 'minimum': 1, 'maximum': 16}),
])
def test_invalid_controls_refused(value, spec):
    with pytest.raises(ValueError):
        protocol.checked_value(value, spec)


def test_private_socket_roundtrip_and_token(tmp_path):
    tmp_path.chmod(0o700)
    path = str(tmp_path / 'editor.sock')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # pytest's paths can exceed macOS's Unix socket limit.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='br-test-', dir='/tmp') as short:
        path = str(Path(short) / 'editor.sock')
        listener.bind(path); listener.listen()
        def serve():
            with listener.accept()[0] as connection:
                msg = protocol.receive(connection)
                assert msg['token'] == 'private'
                protocol.send(connection, {'status': 'completed'})
        thread = threading.Thread(target=serve); thread.start()
        assert protocol.exchange({'socket': path, 'token': 'private'}, {'transport': 'result'}) == {'status': 'completed'}
        thread.join(5); listener.close()


def test_private_directory_and_identifiers(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match='0700'):
        protocol.private_directory(tmp_path)
    for value in ('../escape', 'http://host', 'not-a-session'):
        with pytest.raises(ValueError): protocol.identifier(value)


def test_uncertain_operation_is_not_replayed(monkeypatch):
    monkeypatch.setattr(editor, 'session', lambda *a, **kw: {})
    attempts = []
    def exchange(*args):
        attempts.append(args)
        raise TimeoutError('lost reply')
    monkeypatch.setattr(editor, 'exchange', exchange)
    with pytest.raises(RuntimeError, match='Do not repeat'):
        editor.edit('session', 'revision', [{'control': 'world/color', 'value': '#123456'}])
    assert len(attempts) == 1


def test_pending_operation_is_polled_without_replay(monkeypatch):
    monkeypatch.setattr(editor, 'session', lambda *a, **kw: {})
    attempts = []
    def exchange(cfg, message):
        attempts.append(message)
        return {'status': 'queued'} if message['transport'] == 'submit' else {'status': 'completed', 'result': {'revision': 'next'}}
    monkeypatch.setattr(editor, 'exchange', exchange)
    assert editor.edit('session', 'revision', [])['revision'] == 'next'
    assert [a['transport'] for a in attempts] == ['submit', 'result']
    assert attempts[0]['operation'] == attempts[1]['operation']


def test_preset_captures_unsaved_scene_and_cleans_temp(monkeypatch):
    from blender_render import scene_edit
    calls = []
    def save(session_id, revision, path, **kwargs):
        assert kwargs['copy'] is True
        Path(path).write_bytes(b'test-scene')
        calls.append(Path(path))
        return {'scene': str(path), 'operation': 'capture'}
    monkeypatch.setattr(editor, 'save_scene', save)
    monkeypatch.setattr(scene_edit, 'save_from_blend', lambda path, preset, **kw: {'preset': preset, 'camera_policy': kw['camera_policy']})
    result = editor.save_preset('session', 'revision', 'user/look', camera_policy='authored')
    assert result['camera_policy'] == 'authored'
    assert not calls[0].exists()


def test_invalid_render_does_not_capture_scene(tmp_path, monkeypatch):
    monkeypatch.setattr(editor, 'session', lambda *a, **kw: pytest.fail('Should reject before contacting editor'))
    for kwargs in ({'samples': 5000}, {'width': 0}, {'device': 'CUDA'}, {'timeout': float('inf')}):
        with pytest.raises(ValueError): editor.render_scene('session', 'revision', tmp_path / 'out.png', **kwargs)
    path = tmp_path / 'existing.png'; path.write_bytes(b'keep')
    with pytest.raises(ValueError): editor.render_scene('session', 'revision', path)
    assert path.read_bytes() == b'keep'


def test_source_change_requires_new_session(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    cfg = editor.prepare()
    try:
        assert editor.session(cfg['id'])['source_sha256'] == cfg['source_sha256']
        monkeypatch.setattr(editor, 'source_files', lambda _: {'changed.py': 'abc'})
        with pytest.raises(ValueError, match='source changed'): editor.session(cfg['id'])
        assert editor.session(cfg['id'], check_source=False)['id'] == cfg['id']
        assert 'token' not in editor.public(cfg)
    finally:
        import shutil
        shutil.rmtree(cfg['root'])


def test_attach_script_is_opt_in_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'config'))
    path = tmp_path / 'attach.py'
    result = editor.attach_script(path)
    cfg = editor.session(result['session_id'])
    try:
        assert result['status'] == 'prepared' and 'start(' in path.read_text()
        assert 'token' not in path.read_text()
        with pytest.raises(ValueError): editor.attach_script(path)
    finally:
        import shutil
        shutil.rmtree(cfg['root'])


def test_reconnect_keeps_session_and_never_opens_desktop(monkeypatch):
    sid = 'a' * 32
    monkeypatch.setattr(editor, 'open_editor', lambda *a, **kw: pytest.fail('Must not open Blender'))
    monkeypatch.setattr(editor.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Must not launch a process'))
    monkeypatch.setattr(editor, 'inspect', lambda selected: {'file': 'unsaved.blend', 'dirty': True,
                                                           'revision': 'same'} if selected == sid else pytest.fail('Wrong session'))
    assert editor.reconnect(sid) == {'session_id': sid, 'status': 'connected', 'new_window_opened': False,
                                    'file': 'unsaved.blend', 'dirty': True, 'revision': 'same'}
    def unavailable(_):
        raise ConnectionError('Socket unavailable')
    monkeypatch.setattr(editor, 'inspect', unavailable)
    result = editor.reconnect(sid)
    assert result['status'] == 'unavailable' and not result['new_window_opened']
    assert 'Socket unavailable' in result['error'] and sid in result['recovery']


def test_prepare_reconnect_does_not_replace_live_registry(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'config'))
    old = editor.prepare()
    captures = []
    prepare = editor.prepare
    def record(**kwargs):
        result = prepare(**kwargs); captures.append(result)
        return result
    monkeypatch.setattr(editor, 'prepare', record)
    try:
        path = tmp_path / 'reattach.py'
        result = editor.attach_script(path, reconnect_session=old['id'])
        assert result['session_id'] == old['id']
        assert editor.session(old['id'])['root'] == old['root']
        assert captures[0]['root'] != old['root']
        assert Path(captures[0]['root'], 'session.json').is_file()
        compile(path.read_text(), str(path), 'exec')
        with pytest.raises(ValueError): editor.attach_script(path, reconnect_session=old['id'])
        assert editor.session(old['id'])['root'] == old['root']
    finally:
        import shutil
        for cfg in [old, *captures]: shutil.rmtree(cfg['root'], ignore_errors=True)


def test_mcp_open_requires_explicit_new_window(monkeypatch):
    pytest.importorskip('fastmcp')
    import asyncio
    from fastmcp import Client
    from blender_render.mcp_server import create_stdio
    calls = []
    monkeypatch.setattr(editor, 'open_editor', lambda *a, **kw: calls.append((a, kw)) or {'session_id': 'selected'})
    async def check():
        async with Client(create_stdio(editor_enabled=True)) as client:
            result = await client.call_tool('editor_open', {'scene_path': 'existing.blend'}, raise_on_error=False)
            assert result.is_error and not calls
            result = await client.call_tool('editor_open', {'scene_path': 'existing.blend', 'new_window': True})
            assert result.structured_content['session_id'] == 'selected'
    asyncio.run(check())
    assert len(calls) == 1


def test_cli_and_pymol_opt_in_routes(monkeypatch):
    from blender_render import cli, local, local_bridge, pymol_bridge_plugin as plugin
    calls = []
    monkeypatch.setattr(local, 'render_file', lambda *a, **kw: calls.append(kw))
    cli.execute(cli.parser().parse_args(['render', 'test.cif', '--edit-bridge']))
    assert calls[-1]['edit_bridge'] is True
    monkeypatch.setattr(plugin, 'build_render_payload', lambda *a, **kw: {})
    monkeypatch.setattr(local_bridge, 'render_payload', lambda *a, **kw: calls.append(kw) or {})
    plugin.bridge_render(edit_bridge=1, auto_preview=0, _self=object())
    assert calls[-1]['edit_bridge'] is True
    with pytest.raises(ValueError, match='local desktop'):
        plugin.bridge_render(cluster='slurm', edit_bridge=1)


def test_editor_cli_routes(monkeypatch, tmp_path):
    from blender_render import editor_cli
    calls = []
    monkeypatch.setattr(editor, 'save_preset', lambda *a, **kw: calls.append((a, kw)))
    editor_cli.execute(editor_cli.parser().parse_args(['save-preset', 'session', 'user/look', '--revision', 'abc', '--camera-policy', 'authored']))
    assert calls[-1][0] == ('session', 'abc', 'user/look')
    assert calls[-1][1]['camera_policy'] == 'authored'
    monkeypatch.setattr(editor, 'render_scene', lambda *a, **kw: calls.append((a, kw)))
    editor_cli.execute(editor_cli.parser().parse_args(['render', 'session', '--revision', 'abc', '-o', 'x.png', '--final', '--device', 'GPU']))
    assert calls[-1][1]['preview'] is False and calls[-1][1]['device'] == 'GPU'


def test_optional_mcp_editor_tools_and_original_png(tmp_path, monkeypatch):
    pytest.importorskip('fastmcp')
    import asyncio, base64, hashlib
    from PIL import Image
    from fastmcp import Client
    from blender_render.mcp_server import create_stdio
    path = tmp_path / 'preview.png'; Image.new('RGB', (8,8), 'orange').save(path)
    calls = []
    monkeypatch.setattr(editor, 'edit', lambda *a: calls.append(a) or {'revision': 'next'})
    monkeypatch.setattr(editor, 'job', lambda _: {'status': 'completed', 'output': str(path), 'png_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    async def check():
        async with Client(create_stdio(editor_enabled=True)) as client:
            names = {tool.name for tool in await client.list_tools()}
            assert {'render_structure', 'editor_open', 'editor_inspect', 'editor_edit', 'editor_save_scene', 'editor_save_preset', 'editor_render_scene', 'editor_render_status', 'editor_operation', 'editor_disconnect'} <= names
            await client.call_tool('editor_edit', {'session_id': 'session', 'revision': 'old', 'changes': [{'control': 'world/color', 'value': '#112233'}]})
            result = await client.call_tool('editor_render_status', {'job_id': 'job'})
            images = [c for c in result.content if c.type == 'image']
            assert len(images) == 1 and base64.b64decode(images[0].data) == path.read_bytes()
    asyncio.run(check())
    assert calls[0][0:2] == ('session', 'old')


def test_forget_refuses_live_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    cfg = editor.prepare()
    cfg['pid'] = __import__('os').getpid()
    protocol.write_json(Path(cfg['registry_file']), cfg)
    with pytest.raises(ValueError, match='running'): editor.forget(cfg['id'])
    cfg.pop('pid'); protocol.write_json(Path(cfg['registry_file']), cfg)
    assert editor.forget(cfg['id'])['status'] == 'removed'
    assert not Path(cfg['root']).exists()


def test_forget_refuses_starting_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    cfg = editor.prepare()
    launcher = Path(cfg['root']) / 'launcher.json'
    protocol.write_json(launcher, {'pid': __import__('os').getpid()})
    with pytest.raises(ValueError, match='running'): editor.forget(cfg['id'])
    launcher.unlink()
    editor.forget(cfg['id'])


def test_failed_attach_script_cleans_source(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'config'))
    blocked = tmp_path / 'file'; blocked.write_text('keep')
    captures = []
    prepare = editor.prepare
    def record(**kwargs):
        cfg = prepare(**kwargs); captures.append(cfg)
        return cfg
    monkeypatch.setattr(editor, 'prepare', record)
    with pytest.raises(OSError): editor.attach_script(blocked / 'attach.py')
    assert not Path(captures[0]['root']).exists()
    assert not Path(captures[0]['registry_file']).exists()
    assert blocked.read_text() == 'keep'


@pytest.mark.parametrize('manual_between', [False, True])
def test_repeated_chat_undo_cannot_cross_a_manual_edit(monkeypatch, manual_between):
    import importlib.util, sys
    import blender_render
    scene = object()
    state = {'revision': 'second-chat-edit'}
    restored = []
    def restore(scene, before):
        restored.append(before)
        state['revision'] = before
        return dict(state)
    controls = SimpleNamespace(metadata=lambda: {}, revision=lambda *a: state['revision'], restore=restore)
    bpy = SimpleNamespace(context=SimpleNamespace(scene=scene, mode='OBJECT',
        window_manager=SimpleNamespace(windows=[])), app=SimpleNamespace(is_job_running=lambda _: False))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(blender_render, 'editor_controls', controls, raising=False)
    monkeypatch.setattr(blender_render, 'scene_capture', SimpleNamespace(), raising=False)
    spec = importlib.util.spec_from_file_location('blender_render._undo_test', Path(editor.__file__).with_name('editor_blender.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    bridge = module.Bridge.__new__(module.Bridge)
    bridge.scene = scene
    previous = 'manual-edit' if manual_between else 'first-chat-edit'
    bridge.undo = [('first-chat-edit', 'original'), ('second-chat-edit', previous)]
    bridge.dispatch({'action': 'undo', 'revision': state['revision']})
    if manual_between:
        with pytest.raises(ValueError, match='manual changes are preserved'):
            bridge.dispatch({'action': 'undo', 'revision': state['revision']})
        assert state['revision'] == 'manual-edit' and restored == ['manual-edit']
    else:
        bridge.dispatch({'action': 'undo', 'revision': state['revision']})
        assert state['revision'] == 'original'
