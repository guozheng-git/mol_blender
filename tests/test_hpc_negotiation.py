import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from blender_render import cli, hpc, hpc_remote


def profile(tmp_path):
    return {'version': 2, 'transport':'slurm', 'kind':'hpc', 'host':'fake', 'remote_root':str(tmp_path),
            'remote_python':'python', 'registry':str(tmp_path/'registry'), 'image':'fake.sif',
            'launcher': {'kind':'slurm_sbatch', 'arguments':['--gres=gpu:1']}}


def test_legacy_profile_requires_explicit_migration(tmp_path):
    doc = profile(tmp_path)
    doc.pop('launcher')
    with pytest.raises(ValueError, match='migrat'):
        hpc.validate_profile(doc)


@pytest.mark.parametrize('reply, expected', [('123\n', ('123', None)), ('123;alpha\n', ('123', 'alpha'))])
def test_sbatch_uses_private_script_argv_and_cluster(tmp_path, monkeypatch, capsys, reply, expected):
    doc = profile(tmp_path)
    monkeypatch.setattr(hpc_remote.sys, 'argv', ['hpc_remote', 'start'])
    monkeypatch.setattr(hpc_remote.sys, 'stdin', io.StringIO(json.dumps({'profile':doc})))
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert command[:2] == ['sbatch', '--parsable'] and '--wrap' not in command
        script = Path(command[-1])
        assert script.stat().st_mode & 0o777 == 0o700
        assert 'exec python' in script.read_text()
        return SimpleNamespace(returncode=0, stdout=reply)
    monkeypatch.setattr(hpc_remote.subprocess, 'run', run)
    hpc_remote.main()
    state = json.loads(capsys.readouterr().out)
    assert (state['job_id'], state.get('cluster')) == expected
    assert len(commands) == 1


@pytest.mark.parametrize('reply', ['123;bad;more', '123\n456', 'warning 123', '-1', '123;--evil', ''])
def test_malformed_sbatch_response_never_authorizes_retry(tmp_path, monkeypatch, reply):
    with pytest.raises(ValueError):
        hpc_remote.parse_sbatch(reply)


def test_doctor_profile_does_not_contact_runtime(tmp_path, monkeypatch):
    doc = profile(tmp_path)
    monkeypatch.setattr(cli, 'load_config', lambda: {'profiles':{'test':doc}})
    result = cli.execute(cli.parser().parse_args(['doctor', '--profile', 'test']))
    assert result['runtime_verified'] is False
    assert result['controller_declaration']['engine_build']


def test_attach_absent_never_allocates(tmp_path, monkeypatch, capsys):
    doc = profile(tmp_path)
    monkeypatch.setattr(hpc_remote.sys, 'argv', ['hpc_remote', 'attach'])
    monkeypatch.setattr(hpc_remote.sys, 'stdin', io.StringIO(json.dumps({'profile':doc})))
    hpc_remote.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'absent'
