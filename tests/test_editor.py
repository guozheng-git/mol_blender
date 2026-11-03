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
