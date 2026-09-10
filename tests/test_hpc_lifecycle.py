import io
import json
from types import SimpleNamespace

from blender_render import hpc_remote


def registry(tmp_path):
    state = {"status":"ready", "session_id":"previous", "job_id":"123",
             "node":"gpu-node", "port":1234, "worker_started":1}
    (tmp_path / "session.json").write_text(json.dumps(state))
    (tmp_path / "token").write_text("original-allocation-token")
    return state


def test_completing_allocation_is_not_a_warm_session(tmp_path, monkeypatch):
    registry(tmp_path)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="COMPLETING\n")
    monkeypatch.setattr(hpc_remote.subprocess, "run", run)
    result = hpc_remote.check_session(tmp_path)
    assert result["status"] == "failed"
    assert "token" not in result
    assert len(commands) == 1  # No service probe can make a dying allocation ready.


def test_absent_status_does_not_create_registry_or_allocate(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'absent'
    monkeypatch.setattr(hpc_remote.sys, 'argv', ['hpc_remote', 'status'])
    monkeypatch.setattr(hpc_remote.sys, 'stdin', io.StringIO(json.dumps({'profile': {'registry': str(root)}})))
    def forbidden(*a, **kw):
        raise AssertionError('Status started a process')
    monkeypatch.setattr(hpc_remote.subprocess, 'run', forbidden)
    monkeypatch.setattr(hpc_remote.subprocess, 'Popen', forbidden)
    hpc_remote.main()
    assert json.loads(capsys.readouterr().out) == {'status': 'absent'}
    assert not root.exists()


def test_replacement_waits_for_previous_writer_and_preserves_its_token(tmp_path, monkeypatch, capsys):
    registry(tmp_path)
    profile = {"registry":str(tmp_path), "remote_root":str(tmp_path), "remote_python":"python",
               'host':'fake', 'image':'fake.sif', 'version':2, 'transport':'slurm',
               'launcher':{'kind':'slurm_sbatch', 'arguments':[]}}
    monkeypatch.setattr(hpc_remote.sys, "argv", ["hpc_remote", "start"])
    monkeypatch.setattr(hpc_remote.sys, "stdin", io.StringIO(json.dumps({"profile":profile})))
    monkeypatch.setattr(hpc_remote.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="COMPLETING\n"))
    def must_not_allocate(*args, **kwargs):
        raise AssertionError("The previous database writer is still running")
    monkeypatch.setattr(hpc_remote.subprocess, "Popen", must_not_allocate)
    times = iter([0, 13])
    monkeypatch.setattr(hpc_remote.time, "monotonic", lambda: next(times))
    hpc_remote.main()
    assert json.loads(capsys.readouterr().out)["status"] == "stopping"
    assert (tmp_path / "token").read_text() == "original-allocation-token"
    assert json.loads((tmp_path / "session.json").read_text())["session_id"] == "previous"
