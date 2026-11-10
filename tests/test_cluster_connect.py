"""`cluster connect`: a user's own gateway on a pinned login node, through a tunnel.

The end-to-end tests run the real `cluster serve`, the real remote helper and the
real client. Only ssh is replaced, by tests/fake_ssh.py, which runs the 'remote'
commands on this computer and forwards the tunnel port. No Slurm job is submitted.
"""
import json
import os
from pathlib import Path
import stat
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from blender_render.cluster import connect, connect_remote
from blender_render.cluster.client import Client, profile_path
from blender_render.cluster.common import atomic_json
from test_cluster_jobs import config  # noqa: F401  (fixture)

FAKE_SSH = Path(__file__).with_name('fake_ssh.py')


@pytest.fixture
def site(config, tmp_path, monkeypatch):
    """A validated site config on the 'gateway host' plus fake ssh."""
    home = tmp_path / 'mac-config'
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(home))
    log = tmp_path / 'ssh.log'
    monkeypatch.setenv('FAKE_SSH_LOG', str(log))
    monkeypatch.setattr(connect, 'SSH', str(FAKE_SSH))
    FAKE_SSH.chmod(0o755)
    install = tmp_path / 'install'
    install.mkdir(mode=0o700)
    document = {'spool': str(install / 'doctor-spool'), 'image': str(config.image),
                'image_sha256': config.image_sha256, 'tokens_file': str(install / 'unused-users.json')}
    (install / 'server-gpu.json').write_text(json.dumps(document))
    options = dict(ssh_host='mycluster', node='login-03.cluster.example',
                   remote_executable=str(Path(sys.executable).with_name('blender-render')),
                   server_config=str(install / 'server-gpu.json'))
    yield options, install, log
    state = connect.load_state('lab')
    if state:
        try:
            connect.disconnect('lab')
        except Exception:
            pass


def ssh_calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_connect_starts_gateway_tunnels_and_never_prints_the_token(site, capsys):
    options, install, log = site
    result = connect.connect('lab', **options)
    assert result['status'] == 'connected' and result['gateway_started'] is True
    assert result['engine_build'] and result['url'].startswith('http://127.0.0.1:')

    # The profile holds the token privately; nothing printed or returned contains it.
    profile = profile_path('lab')
    token = json.loads(profile.read_text())['token']
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600
    out = capsys.readouterr()
    assert token not in json.dumps(result) + out.out + out.err
    assert token not in log.read_text()
    assert not list(Path(connect.state_path('lab').parent).glob('**/credential.json'))

    # Every ssh call is pinned to the same node and never prompts.
    for call in ssh_calls(log):
        assert 'HostName=login-03.cluster.example' in call and 'BatchMode=yes' in call
    tunnel = [call for call in ssh_calls(log) if '-N' in call][0]
    spec = tunnel[tunnel.index('-L') + 1]
    assert spec.startswith('127.0.0.1:') and ':127.0.0.1:' in spec

    # The per-connection config changes only spool, tokens and authentication.
    connection = Path(connect.load_state('lab')['connection_dir'])
    derived = json.loads((connection / 'server.json').read_text())
    base = json.loads((install / 'server-gpu.json').read_text())
    assert {k for k in derived if derived[k] != base.get(k)} == {'spool', 'tokens_file', 'authentication'}
    assert derived['authentication'] == 'token'
    assert stat.S_IMODE(connection.stat().st_mode) == 0o700

    # The ordinary client works through the tunnel.
    assert Client('lab').api('GET', '/v1/jobs') == []


def test_reconnect_reuses_and_recovers(site):
    options, _, log = site
    first = connect.connect('lab', **options)
    calls = len(ssh_calls(log))
    again = connect.connect('lab')  # Saved settings; nothing new to type.
    assert again['tunnel_reused'] is True and len(ssh_calls(log)) == calls

    # A dead tunnel is reopened to the still-running gateway; the token is kept.
    token = json.loads(profile_path('lab').read_text())['token']
    connect.close_tunnel(connect.load_state('lab'))
    reopened = connect.connect('lab')
    assert reopened['gateway_started'] is False and reopened['remote_port'] == first['remote_port']
    assert json.loads(profile_path('lab').read_text())['token'] == token

    # After disconnect the gateway restarts and the same credential still works.
    result = connect.disconnect('lab')
    assert result['tunnel_closed'] and result['gateway_stopped']
    restarted = connect.connect('lab')
    assert restarted['gateway_started'] is True
    assert json.loads(profile_path('lab').read_text())['token'] == token
    assert Client('lab').api('GET', '/v1/jobs') == []


def test_revoke_forgets_the_connection(site):
    options, _, _ = site
    connect.connect('lab', **options)
    users = Path(connect.load_state('lab')['connection_dir']) / 'users.json'
    result = connect.disconnect('lab', revoke=True)
    assert result['revoked'] is True and result['gateway_stopped']
    assert not profile_path('lab').exists() and connect.load_state('lab') == {}
    assert json.loads(users.read_text())['users'] == {}


def test_open_mode_site_config_is_still_token_authenticated(site):
    options, install, _ = site
    document = json.loads((install / 'server-gpu.json').read_text())
    document['authentication'] = 'none'
    (install / 'server-gpu.json').write_text(json.dumps(document))
    connect.connect('lab', **options)
    derived = json.loads((Path(connect.load_state('lab')['connection_dir']) / 'server.json').read_text())
    assert derived['authentication'] == 'token'
    assert 'token' in json.loads(profile_path('lab').read_text())


@pytest.mark.parametrize('revoke', [False, True])
def test_changed_site_config_refreshes_after_disconnect_without_losing_jobs(site, revoke):
    options, install, _ = site
    connect.connect('lab', **options)
    saved = connect.load_state('lab')
    connection = Path(saved['connection_dir'])
    job = connection / 'jobs' / 'keep.txt'
    job.write_text('existing job data')
    token = json.loads(profile_path('lab').read_text())['token']
    document = json.loads((install / 'server-gpu.json').read_text())
    document['cpus'] = 2
    (install / 'server-gpu.json').write_text(json.dumps(document))
    with pytest.raises(RuntimeError, match='site configuration changed.*disconnect without'):
        connect.remote(saved, 'ensure', saved['server_config'], saved['connection_dir'], connect.free_port())
    connect.disconnect('lab', revoke=revoke)
    assert connect.connect('lab', **options)['gateway_started']
    assert json.loads((connection / 'server.json').read_text())['cpus'] == 2
    assert job.read_text() == 'existing job data'
    if not revoke:
        assert json.loads(profile_path('lab').read_text())['token'] == token
        assert connect.load_state('lab')['local_port'] == saved['local_port']


def test_invalid_config_refresh_preserves_last_valid_config(site):
    options, install, _ = site
    connect.connect('lab', **options)
    target = Path(connect.load_state('lab')['connection_dir']) / 'server.json'
    original = target.read_bytes()
    connect.disconnect('lab')
    document = json.loads((install / 'server-gpu.json').read_text())
    document['cpus'] = 0
    (install / 'server-gpu.json').write_text(json.dumps(document))
    with pytest.raises(RuntimeError, match='cpus must be positive'):
        connect.connect('lab')
    assert target.read_bytes() == original


def test_occupied_local_port_is_not_trusted_or_sent_credentials(site):
    options, _, log = site
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        with pytest.raises(RuntimeError, match='SSH tunnel exited'):
            connect.connect('lab', **options, local_port=occupied.getsockname()[1])
        assert not profile_path('lab').exists()
        assert not any(' issue ' in call[-1] for call in ssh_calls(log))
        occupied.settimeout(.1)
        with pytest.raises(TimeoutError):
            occupied.accept()  # Even the readiness check must not contact this service.


def test_changing_node_replaces_the_tunnel_and_saves_the_new_identity(site):
    options, _, log = site
    connect.connect('lab', **options)
    before = connect.load_state('lab')
    calls = len(ssh_calls(log))
    result = connect.connect('lab', node='login-04.cluster.example')
    after = connect.load_state('lab')
    assert result['node'] == after['node'] == 'login-04.cluster.example'
    assert not result['tunnel_reused'] and after['tunnel_pid'] != before['tunnel_pid']
    assert not connect.tunnel_process(before)
    new_calls = ssh_calls(log)[calls:]
    assert 'HostName=login-03.cluster.example' in new_calls[0]  # Stop on the original node.
    assert all('HostName=login-04.cluster.example' in call for call in new_calls[1:])


def test_replacement_controller_can_stop_gateway_after_old_install_is_removed(site):
    options, _, _ = site
    connect.connect('lab', **options)
    saved = connect.load_state('lab')
    original_pid = saved['gateway_pid']
    saved['remote_executable'] = '/removed/controller/bin/blender-render'
    atomic_json(connect.state_path('lab'), saved)
    result = connect.connect('lab', remote_executable=options['remote_executable'])
    assert result['gateway_started'] and not result['tunnel_reused']
    assert not connect_remote.alive(original_pid)
    assert connect.load_state('lab')['remote_executable'] == options['remote_executable']


def test_concurrent_remote_ensure_starts_exactly_one_gateway(site):
    options, _, _ = site
    conf = connect.settings('lab', options)
    barrier = Barrier(2)

    def ensure():
        ports = [connect.free_port() for _ in range(3)]
        barrier.wait()
        return connect.remote(conf, 'ensure', conf['server_config'], conf['connection_dir'], *ports)

    results = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: ensure(), range(2)))
        assert len({r['pid'] for r in results}) == 1
        assert sum(r['started'] for r in results) == 1
        assert connect.remote(conf, 'stop', conf['connection_dir'])['stopped']
        assert all(not connect_remote.alive(r['pid']) for r in results)
    finally:
        connect.remote(conf, 'stop', conf['connection_dir'])
        # Also clean up an unrecorded gateway if the serialization assertion fails.
        for result in results:
            if connect_remote.ours(result):
                os.killpg(result['pid'], 15)


def test_concurrent_local_connects_share_one_tunnel_and_credential(site):
    options, _, log = site
    barrier = Barrier(2)

    def start():
        barrier.wait()
        return connect.connect('lab', **options)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert sum(r['tunnel_reused'] for r in results) == 1
    assert sum('-N' in call for call in ssh_calls(log)) == 1
    assert Client('lab').api('GET', '/v1/jobs') == []


@pytest.mark.parametrize('field,value,message', [
    ('ssh_host', '-oProxyCommand=evil', 'Invalid ssh_host'),
    ('node', 'node;rm', 'Invalid node'),
    ('remote_executable', 'controller/bin/blender-render', 'absolute path'),
    ('server_config', '', 'First connection needs'),
])
def test_settings_are_validated_before_any_ssh(tmp_path, monkeypatch, field, value, message):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    options = dict(ssh_host='mycluster', node='login-03.cluster.example', remote_executable='/x/bin/blender-render',
                   server_config='/x/server.json')
    options[field] = value
    with pytest.raises(ValueError, match=message):
        connect.settings('lab', options)


def test_saved_identity_cannot_be_silently_changed(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    atomic_json(connect.state_path('lab', create=True), dict(ssh_host='mycluster', node='n1', remote_executable='/x/bin/blender-render',
                                               server_config='/x/s.json', remote_user='client-a', local_port=30000,
                                               connection_dir='/x/connections/lab-client-a'))
    assert connect.settings('lab', {'node': 'n2'})['node'] == 'n2'  # Moving nodes is allowed.
    with pytest.raises(ValueError, match='remote_user differs'):
        connect.settings('lab', {'remote_user': 'client-b'})


def test_missing_profile_error_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path))
    with pytest.raises(ValueError, match="No cluster profile 'lab'.*cluster connect"):
        Client('lab')


def test_status_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'home'))
    assert connect.status('lab') == {'profile': 'lab', 'connection': None}
    assert not (tmp_path / 'home').exists()


def test_tunnel_log_is_private(site):
    options, _, _ = site
    connect.connect('lab', **options)
    log = connect.state_path('lab').with_suffix('.tunnel.log')
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
