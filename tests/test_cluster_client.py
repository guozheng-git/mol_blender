import hashlib
import json
from dataclasses import replace
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest
import uvicorn

from blender_render.cluster.client import Client, NoRedirect, RemoteError, configure, profile_path
from blender_render.cluster.common import atomic_json, digest_file, read_json, validate_url
from blender_render.cluster.server import create_app
from test_cluster_jobs import Scheduler, config, payload


@pytest.fixture(params=['token', 'none'])
def live_service(config, tmp_path, monkeypatch, request):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'client-config'))
    config = replace(config, authentication=request.param)
    token = 'test-credential-' + 'a' * 32
    if config.authentication == 'token':
        atomic_json(config.tokens_file, {'users': {'alice': hashlib.sha256(token.encode()).hexdigest()}})
    app = create_app(config, scheduler=Scheduler())
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = 'http://127.0.0.1:' + str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    if config.authentication == 'token':
        credential = tmp_path / 'credential.json'
        atomic_json(credential, {'url': url, 'token': token})
        configure('lab', credential)
    else:
        configure('lab', url=url)
    try:
        yield Client('lab'), app.state.store
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def completed(store, identifier):
    root = store.directory(identifier)
    (root / 'out').mkdir()
    Image.new('RGB', (32, 24), 'green').save(root / 'out/render.png')
    path = root / 'out/render.png'
    atomic_json(root / 'result.json', {'status': 'completed', 'finished_at': time.time(), 'artifacts': {
        'render.png': {'path': 'out/render.png', 'bytes': path.stat().st_size, 'sha256': digest_file(path)}}})
    return path


def test_real_http_submit_receipt_recovery_fetch_and_no_overwrite(live_service, tmp_path):
    client, store = live_service
    output = tmp_path / 'my-render.png'
    result = client.submit(payload(), {'width': 32, 'height': 24, 'samples': 1}, output)
    assert result['status'] == 'queued'
    receipt = read_json(result['receipt'])
    assert 'token' not in receipt and 'payload' not in receipt
    assert client.recover(result['receipt'])['job_id'] == result['job_id']
    del receipt['job_id']  # Model a lost response after Slurm accepted the request.
    atomic_json(result['receipt'], receipt)
    assert client.recover(result['receipt'])['job_id'] == result['job_id']
    original = completed(store, result['job_id'])
    fetched = client.fetch(result['job_id'], output)
    assert output.read_bytes() == original.read_bytes()
    assert fetched['outputs']['render.png'] == str(output)
    with pytest.raises(ValueError, match='exists'):
        client.fetch(result['job_id'], output)
    assert len(store.scheduler.submissions) == 1


def test_corrupt_download_never_publishes_partial_output(live_service, tmp_path):
    client, store = live_service
    output = tmp_path / 'corrupt.png'
    result = client.submit(payload(), {}, output)
    original = completed(store, result['job_id'])
    data = bytearray(original.read_bytes())
    data[-1] ^= 1
    original.write_bytes(data)
    with pytest.raises(RemoteError, match='integrity'):
        client.fetch(result['job_id'], output)
    assert not output.exists() and not output.with_suffix('.render').exists()
    assert not list(tmp_path.glob('.cluster-download-*'))


def test_wait_timeout_does_not_cancel_or_duplicate(live_service, tmp_path):
    client, store = live_service
    result = client.submit(payload(), {}, tmp_path / 'pending.png', wait=True, timeout=.01)
    assert result['status'] == 'queued'
    assert store.scheduler.cancelled == []
    assert len(store.scheduler.submissions) == 1


def test_network_failure_keeps_a_recoverable_receipt(live_service, tmp_path, monkeypatch):
    client, store = live_service
    api = client.api
    def lost_reply(method, path, data=None):
        result = api(method, path, data)
        if method == 'POST':
            raise RemoteError('lost connection')
        return result
    monkeypatch.setattr(client, 'api', lost_reply)
    output = tmp_path / 'lost.png'
    with pytest.raises(RemoteError, match='Recover with'):
        client.submit(payload(), {}, output)
    assert client.recover(output.with_suffix('.cluster.json'))['status'] == 'queued'
    assert len(store.scheduler.submissions) == 1


def test_pymol_bridge_submits_with_foreign_python_startup_settings(live_service, tmp_path, monkeypatch):
    import sys
    from blender_render import cluster_bridge
    from blender_render.config import save_config
    client, store = live_service
    save_config({'local_python': sys.executable})
    for name in ('PYTHONHOME', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
        monkeypatch.setenv(name, str(tmp_path / 'embedded-pymol' / 'bin' / 'python'))
    # The real child must import jsonschema/authoring and use the real HTTP API.
    # /v1/info alone would not exercise the import that failed in embedded PyMOL.
    result = cluster_bridge.submit(payload(), options={'width':32,'height':24,'samples':1},
                                   output=tmp_path / 'bridge.png', profile='lab')
    assert result['status'] == 'queued'
    assert client.recover(result['receipt'])['job_id'] == result['job_id']
    assert len(store.scheduler.submissions) == 1


def test_plaintext_remote_origins_and_redirects_are_rejected():
    for url in ('http://cluster.example', 'https://user:password@cluster.example',
                'https://cluster.example/path', 'https://cluster.example?token=bad'):
        with pytest.raises(ValueError):
            validate_url(url)
    assert validate_url('http://127.0.0.1:8765')
    assert validate_url('https://cluster.example')
    with pytest.raises(RemoteError, match='redirect'):
        NoRedirect().redirect_request(None)


def test_cluster_pymol_submission_does_not_launch_local_blender(monkeypatch, tmp_path):
    from blender_render import pymol_bridge_plugin as plugin, local_bridge, cluster_bridge
    monkeypatch.setattr(plugin, 'build_render_payload', lambda *a, **kw: payload())
    monkeypatch.setattr(local_bridge, 'render_payload', lambda *a, **kw: pytest.fail('local render launched'))
    calls = []
    monkeypatch.setattr(cluster_bridge, 'submit', lambda data, **kw: calls.append((data, kw)) or
                        {'status': 'queued', 'job_id': 'a' * 32})
    result = plugin.bridge_render(cluster='lab', output_dir=str(tmp_path), _self=SimpleNamespace())
    assert result['status'] == 'queued'
    assert calls[0][1]['profile'] == 'lab' and calls[0][1]['wait'] is False
    assert calls[0][0] == payload()


def test_pymol_fetch_loads_verified_png(monkeypatch, tmp_path):
    from blender_render import pymol_bridge_plugin as plugin, cluster_bridge
    png = tmp_path / 'download.png'
    png.write_bytes(b'validated by client')
    monkeypatch.setattr(cluster_bridge, 'command', lambda args: {'status': 'completed', 'outputs': {'render.png': str(png)}})
    loaded = []
    context = SimpleNamespace(load_png=lambda path, **kw: loaded.append(path))
    plugin.bridge_cluster_fetch('a' * 32, cluster='lab', output_dir=str(tmp_path), output_name=png.name, _self=context)
    assert loaded == [str(png)]


def test_admin_credentials_are_private_and_tokens_never_printed(tmp_path, monkeypatch):
    from blender_render.cluster.auth import add_user, authenticate, revoke_user
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'config'))
    path, output = tmp_path / 'users.json', tmp_path / 'credential.json'
    result = add_user(path, 'alice', 'https://cluster.example', output)
    credential = read_json(output)
    assert credential['token'] not in json.dumps(result)
    assert credential['token'] not in path.read_text()
    assert path.stat().st_mode & 0o077 == output.stat().st_mode & 0o077 == 0
    assert authenticate(path, 'Bearer ' + credential['token']) == 'alice'
    configure('lab', output)
    assert profile_path('lab').stat().st_mode & 0o077 == 0
    revoke_user(path, 'alice')
    assert authenticate(path, 'Bearer ' + credential['token']) is None


def test_local_imports_do_not_load_cluster_server_dependencies():
    import subprocess, sys
    result = subprocess.run([sys.executable, '-c',
        "import blender_render.cli, blender_render.local; import sys; "
        "assert not {'starlette', 'uvicorn', 'blender_render.cluster.server'} & sys.modules.keys()"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_open_profiles_are_explicit_and_send_no_credentials(tmp_path, monkeypatch):
    from blender_render.cluster.cli import execute, parser
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'config'))
    args = parser().parse_args(['configure', '--profile', 'open', '--url', 'https://cluster.example'])
    execute(args)
    client = Client('open')
    seen = []
    monkeypatch.setattr(client.opener, 'open', lambda request, **kw: seen.append(request))
    client.open('GET', '/v1/info')
    assert not seen[0].has_header('Authorization')
    assert read_json(profile_path('open')) == {'url': 'https://cluster.example', 'authentication': 'none'}
    # Missing credentials must never silently turn an old/private profile into open mode.
    for invalid in ({'url': client.url}, {'url': client.url, 'authentication': 'unknown'},
                    {'url': client.url, 'authentication': 'none', 'token': 'a' * 43}):
        atomic_json(profile_path('invalid'), invalid)
        with pytest.raises(ValueError):
            Client('invalid')
    with pytest.raises(ValueError, match='either'):
        configure('ambiguous', tmp_path / 'credential.json', url=client.url)


def test_existing_presets_strip_unused_contact_metadata(live_service, tmp_path):
    client, store = live_service
    data=payload();data['polar_contacts']=[['A:1:ARG:NE','B:2:PTR:O1P']]
    result=client.submit(data, {}, tmp_path/'without-contacts.png')
    saved=read_json(store.directory(result['job_id'])/'request.json')['request']
    assert 'polar_contacts' not in saved['payload']
    assert 'polar_contacts' in data
