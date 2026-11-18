"""The shared job API: receipts, read-only recovery, polling and the lab connection."""
import json
from pathlib import Path

import pytest

from mol_blender import jobs
from mol_blender.cluster import client as cluster_client
from mol_blender.cluster import connect
from mol_blender.cluster.client import RemoteError


class FakeClient:
    """One gateway shared by every Client instance; statuses are served in order."""
    received = True
    statuses: list = []
    calls: list = []

    def __init__(self, profile):
        self.profile = profile

    def recover(self, receipt):
        record = json.loads(Path(receipt).read_text())
        if not record.get("job_id"):
            FakeClient.calls.append("lookup")
            if not FakeClient.received:
                raise RemoteError("Cluster HTTP 404: Job not found")
        return self.status(record.get("job_id") or "b" * 32)

    def status(self, job_id):
        FakeClient.calls.append("status")
        reply = FakeClient.statuses.pop(0) if len(FakeClient.statuses) > 1 else FakeClient.statuses[0]
        if isinstance(reply, Exception):
            raise reply
        return {"job_id": job_id, **reply}

    def api(self, method, path, data=None):
        FakeClient.calls.append(method + " " + path)
        return {"status": "cancelling"}

    def fetch(self, job_id, output):
        FakeClient.calls.append("fetch " + job_id)
        return {"status": "completed", "outputs": {"render.png": str(output)}}


@pytest.fixture
def gateway(monkeypatch):
    FakeClient.received, FakeClient.statuses, FakeClient.calls = True, [{"status": "queued"}], []
    monkeypatch.setattr(cluster_client, "Client", FakeClient)
    clock = [0.0]
    monkeypatch.setattr(jobs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(jobs.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return FakeClient


def receipt(tmp_path, **fields):
    path = tmp_path / "render.cluster.json"
    path.write_text(json.dumps({"profile": "lab", "request_id": "r" * 32,
                                "output": str(tmp_path / "render.png"), **fields}))
    return path


def test_backend_aliases():
    assert [jobs.backend(name) for name in ("local", "lab", "cluster", "gpu")] == ["local", "lab", "lab", "lab"]
    with pytest.raises(ValueError):
        jobs.backend("remote")
    assert jobs.receipt_for("/out/render.png") == Path("/out/render.cluster.json")


def test_unconfirmed_upload_is_adopted_and_saved_to_the_receipt(gateway, tmp_path):
    path = receipt(tmp_path)
    status = jobs.observe(path)
    assert status["job_id"] == "b" * 32 and status["status"] == "queued"
    assert json.loads(path.read_text())["job_id"] == "b" * 32
    jobs.observe(path)
    assert gateway.calls == ["lookup", "status", "status"]  # Later checks use the job directly.


def test_upload_that_never_arrived_is_reported_without_resubmitting(gateway, tmp_path):
    gateway.received = False
    status = jobs.observe(receipt(tmp_path))
    assert status["status"] == "prepared" and "same request ID" in status["message"]
    assert "job_id" not in json.loads((tmp_path / "render.cluster.json").read_text())


def test_a_known_job_that_vanished_is_an_error_not_prepared(gateway, tmp_path):
    gateway.statuses = [RemoteError("Cluster HTTP 404: Job not found")]
    with pytest.raises(RemoteError):
        jobs.observe(receipt(tmp_path, job_id="a" * 32))


def test_wait_backs_off_through_outages_and_stops_at_a_terminal_state(gateway, tmp_path):
    gateway.statuses = [OSError("tunnel closed"), RemoteError("Cluster connection failed"),
                        {"status": "running"}, {"status": "completed"}]
    updates = []
    final = jobs.wait(receipt(tmp_path, job_id="a" * 32), timeout=600, on_update=updates.append)
    assert final["status"] == "completed" and not final.get("waiting_stopped")
    assert [u["status"] for u in updates] == ["recovering", "recovering", "running"]


@pytest.mark.parametrize("status", ["submission_unknown", "failed", "expired", "cancelled"])
def test_wait_returns_states_that_will_not_progress_by_polling(gateway, tmp_path, status):
    gateway.statuses = [{"status": status}]
    assert jobs.wait(receipt(tmp_path, job_id="a" * 32), timeout=600)["status"] == status


def test_wait_requests_cancellation_once_and_waits_for_confirmation(gateway, tmp_path):
    gateway.statuses = [{"status": "running"}, {"status": "cancelling"}, {"status": "cancelled"}]
    final = jobs.wait(receipt(tmp_path, job_id="a" * 32), timeout=600, cancelled=lambda: True)
    assert final["status"] == "cancelled" and final["cancel_requested"]
    assert gateway.calls.count("POST /v1/jobs/" + "a" * 32 + "/cancel") == 1


def test_a_cancel_the_gateway_refuses_does_not_hide_the_jobs_status(gateway, tmp_path, monkeypatch):
    monkeypatch.setattr(FakeClient, "api", lambda self, method, path, data=None: (_ for _ in ()).throw(
        RemoteError("Cluster HTTP 409: needs administrator reconciliation")))
    gateway.statuses = [{"status": "running"}, {"status": "running"}, {"status": "submission_unknown"}]
    updates = []
    final = jobs.wait(receipt(tmp_path, job_id="a" * 32), timeout=600, cancelled=lambda: True,
                      on_update=updates.append)
    assert final["status"] == "submission_unknown" and not final["cancel_requested"]
    assert updates[0]["status"] == "running" and "409" in updates[0]["cancel_error"]


def test_wait_reads_the_profile_once_not_every_round(gateway, tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(FakeClient, "__init__", lambda self, profile: made.append(profile))
    gateway.statuses = [{"status": "queued"}, {"status": "running"}, {"status": "completed"}]
    assert jobs.wait(receipt(tmp_path, job_id="a" * 32), timeout=600)["status"] == "completed"
    assert made == ["lab"]


def test_poll_rejects_unbounded_waits(gateway):
    for timeout in (0, 8 * 86400):
        with pytest.raises(ValueError):
            jobs.poll(lambda: {"status": "queued"}, timeout=timeout)


def test_wait_deadline_reports_that_nothing_was_resubmitted(gateway, tmp_path):
    final = jobs.wait(receipt(tmp_path, job_id="a" * 32), timeout=10)
    assert final["waiting_stopped"] and "No new job was submitted" in final["message"]


def test_wait_does_not_retry_a_missing_receipt(gateway, tmp_path):
    with pytest.raises(FileNotFoundError):
        jobs.wait(tmp_path / "missing.cluster.json", timeout=600)


def test_fetch_with_a_known_job_skips_the_extra_status_round_trip(gateway, tmp_path):
    result = jobs.fetch(receipt(tmp_path, job_id="a" * 32))
    assert result["status"] == "completed" and gateway.calls == ["fetch " + "a" * 32]


class FakeConnect:
    """A tunnel that is open after the first connect, until a disconnect."""
    def __init__(self, saved=True, reused=False, fail=None):
        self.saved, self.open, self.fail, self.calls = saved, reused, fail, []

    def load_state(self, profile):
        return {"ssh_host": "lab"} if self.saved else {}

    def connect(self, profile):
        self.calls.append("connect")
        if self.fail:
            raise self.fail
        reused, self.open = self.open, True
        return {"status": "connected", "tunnel_reused": reused}

    def disconnect(self, profile):
        self.calls.append("disconnect")
        self.open = False
        return {"status": "disconnected"}


@pytest.fixture
def tunnel(monkeypatch, tmp_path):
    """Install a FakeConnect; lease files live under a private cluster home."""
    monkeypatch.setenv("MOL_BLENDER_CLUSTER_HOME", str(tmp_path / "cluster-home"))

    def install(*args, **kwargs):
        fake = FakeConnect(*args, **kwargs)
        for name in ("load_state", "connect", "disconnect"):
            monkeypatch.setattr(connect, name, getattr(fake, name))
        return fake
    return install


@pytest.mark.parametrize("saved,reused,keep,calls,summary", [
    (True, False, False, ["connect", "disconnect"], "closed"),
    (True, False, True, ["connect"], "kept open"),
    (True, True, False, ["connect"], "left open (it was already open)"),
    (False, False, False, [], "not managed (no saved SSH connection)"),
])
def test_lab_connection_closes_only_what_it_opened(tunnel, saved, reused, keep, calls, summary):
    fake = tunnel(saved, reused)
    with jobs.lab_connection("lab", keep=keep) as info:
        assert fake.calls == (["connect"] if saved else [])
    assert fake.calls == calls and info["connection"] == summary


def test_lab_connection_reports_a_failed_disconnect_without_hiding_the_result(tunnel, monkeypatch):
    tunnel()
    monkeypatch.setattr(connect, "disconnect", lambda profile: (_ for _ in ()).throw(OSError("ssh gone")))
    with jobs.lab_connection("lab") as info:
        pass
    assert info["connection"] == "disconnect failed: ssh gone"


def test_a_tunnel_another_render_is_using_stays_open_until_the_last_one_finishes(tunnel):
    fake = tunnel()
    with jobs.lab_connection("lab") as opener:  # Studio opens the tunnel and wants it closed.
        helper = jobs.lab_connection("lab", keep=True)  # A co-pymol helper reuses it meanwhile.
        helper.__enter__()
    assert opener["connection"] == "left open (another render is still using it)"
    assert "disconnect" not in fake.calls
    helper.__exit__(None, None, None)
    with jobs.lab_connection("lab") as later:  # The next short-lived command closes it.
        pass
    assert later["connection"] == "closed" and fake.calls.count("disconnect") == 1


def test_a_lease_left_by_a_process_that_exited_does_not_hold_the_tunnel(tunnel):
    fake = tunnel()
    leases = connect.state_path("lab", create=True).with_suffix(".leases")
    leases.mkdir()
    (leases / "999999999-dead.lease").touch()
    with jobs.lab_connection("lab") as info:
        pass
    assert info["connection"] == "closed" and fake.calls == ["connect", "disconnect"]
    assert not list(leases.glob("*.lease"))


def test_recovery_continues_with_the_saved_profile_when_ssh_fails(tunnel):
    tunnel(fail=RuntimeError("ssh: MFA required"))
    with jobs.lab_connection("lab", required=False) as info:
        assert not info["opened"]
    assert info["connection"].startswith("connect failed") and "MFA" in info["connection"]
    with pytest.raises(RuntimeError, match="MFA"):
        with jobs.lab_connection("lab"):
            pass


def test_lab_profiles_can_live_apart_from_the_renderer_configuration(tmp_path, monkeypatch):
    from mol_blender.config import cluster_root, config_root
    monkeypatch.setenv("MOL_BLENDER_HOME", str(tmp_path / "studio"))
    monkeypatch.delenv("MOL_BLENDER_CLUSTER_HOME", raising=False)
    assert cluster_root() == config_root() == tmp_path / "studio"  # Unchanged default.
    monkeypatch.setenv("MOL_BLENDER_CLUSTER_HOME", str(tmp_path / "terminal"))
    assert config_root() == tmp_path / "studio"  # Presets and runtimes stay the client's own.
    assert cluster_client.profile_path("lab") == tmp_path / "terminal/clusters/lab.json"
    assert connect.state_path("lab") == tmp_path / "terminal/cluster-connections/lab.json"


def test_local_bundles_have_no_size_cap_but_lab_uploads_do(tmp_path):
    from mol_blender.local import load_payload
    bundle = tmp_path / "scene.json"
    bundle.write_text(json.dumps({"atoms": "x" * 2048}))
    assert load_payload(bundle)["atoms"] == "x" * 2048
    with pytest.raises(ValueError, match="size limit"):
        load_payload(bundle, limit=1024)


def test_render_takes_the_same_options_as_job_submit(monkeypatch):
    from mol_blender import cli, local
    from mol_blender.presets import DEFAULT_PRESET
    calls = []
    monkeypatch.setattr(local, "render_file", lambda path, **kw: calls.append(kw["options"]))
    cli.execute(cli.parser().parse_args(["render", "x.json", "--width", "64", "--no-transparent"]))
    assert calls[-1] == {"width": 64, "transparent_background": False, "preset": DEFAULT_PRESET}
    with pytest.raises(ValueError, match="not both"):
        cli.execute(cli.parser().parse_args(["render", "x.json", "--preset", "a", "--style-json", "b.json"]))


def test_co_pymol_names_the_receipt_the_job_api_writes():
    # PyMOL's Python cannot import the renderer, so co-pymol keeps its own copy of the name.
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "co_pymol/render/records.py"
    spec = importlib.util.spec_from_file_location("co_pymol_records", path)
    records = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(records)
    assert jobs.receipt_for(Path("/run/render.png")).name == records.RECEIPT
