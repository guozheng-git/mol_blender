"""Security and lifecycle tests use fake compute; no external billing required."""
import base64
import io
import json
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from blender_render.api import Downloads, create_app
from blender_render.jobs import JobError, JobStore, Policy
from blender_render.service import RenderService, prepare_options, validate_input


TOKEN = "x" * 43
SECOND_TOKEN = "y" * 43


@pytest.fixture
def payload():
    cif = '''data_test
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_asym_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . ALA A 1 1 0.0 0.0 0.0 1.0 10.0 1 A 1
'''
    return {"version": "1.0", "cif_text": cif, "dss": {"ran": True},
        "view": {"pymol_view": [1,0,0,0,1,0,0,0,1,0,0,-50,0,0,0,1,100,0], "viewport_px": [800,600], "fov_deg": 20, "orthoscopic": False}}


class Compute:
    def __init__(self, gate=None, fail=False):
        self.calls = []
        self.entered = threading.Event()
        self.gate, self.fail = gate, fail

    def render(self, request, job_id, cancelled):
        self.calls.append(job_id)
        self.entered.set()
        if self.gate:
            while not self.gate.wait(.005):
                if cancelled():
                    raise RuntimeError("Cancelled")
        if self.fail:
            raise RuntimeError("Worker died")
        buffer = io.BytesIO()
        Image.new("RGBA", (4, 3), (30, 100, 45, 0)).save(buffer, format="PNG")
        return {"status": "completed", "png_base64": base64.b64encode(buffer.getvalue()).decode(), "manifest": {}, "runtime_seconds": .01}

    def stop(self):
        if self.gate:
            self.gate.set()


def await_status(store, owner, identifier, desired):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        job = store.get(owner, identifier)
        if job["status"] == desired:
            return job
        time.sleep(.01)
    pytest.fail(str(job))


def test_concurrent_fifo_and_idempotent_retries(tmp_path, payload):
    store = JobStore(tmp_path)
    gate = threading.Event()
    backend = Compute(gate)
    service = RenderService(store, backend)
    source = service.import_payload("alice", payload)
    first = service.submit("alice", source["input_id"], {}, "first")
    replay = service.submit("alice", source["input_id"], {}, "first")
    second = service.submit("bob", service.import_payload("bob", payload)["input_id"], {}, "second")
    assert first["job_id"] == replay["job_id"]
    assert second["queue_position"] == 2
    service.start()
    try:
        await_status(store, "alice", first["job_id"], "running")
        assert backend.entered.wait(1)
        assert backend.calls == [first["job_id"]]
        gate.set()
        await_status(store, "bob", second["job_id"], "done")
        assert backend.calls == [first["job_id"], second["job_id"]]
    finally:
        service.stop()
        store.close()


def test_reserved_budget_prevents_dispatch_and_survives_restart(tmp_path, payload):
    policy = Policy(public=True, budget_usd=.31)
    store = JobStore(tmp_path, policy)
    compute = Compute()
    service = RenderService(store, compute)
    source = service.import_payload("alice", payload)
    with pytest.raises(JobError, match="exhausted"):
        service.submit("alice", source["input_id"], {}, "one")
    assert not compute.calls
    store.close()
    reopened = JobStore(tmp_path, replace(policy, budget_usd=530))
    with pytest.raises(JobError, match="exhausted"):
        reopened.submit("alice", source["input_id"], {}, "new")
    reopened.close()


def test_private_health_and_compatibility_routes_require_owner(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    with TestClient(create_app(service, token=TOKEN)) as client:
        assert client.get('/health').status_code == 401
        assert client.get('/health', headers={'Authorization':'Bearer '+TOKEN}).status_code == 200
        assert client.get('/status/unknown').status_code == 401
        assert client.get('/result/unknown').status_code == 401
        assert client.post('/stop').status_code == 401
    store.close()


def test_raw_file_upload_uses_converter_and_rejects_malformed_before_compute(tmp_path, payload):
    store = JobStore(tmp_path)
    compute = Compute()
    service = RenderService(store, compute)
    converted = []
    def convert(data, suffix):
        converted.append((data,suffix))
        if data == b'broken session':
            raise ValueError('internal converter path must stay private')
        return {**payload, 'cif_text':'data_empty\n'} if data == b'bad' else payload
    with TestClient(create_app(service, token=TOKEN, converter=convert)) as client:
        headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/octet-stream'}
        assert client.post('/v1/files/pse', content=b'valid fixture', headers=headers).status_code == 200
        assert converted == [(b'valid fixture','.pse')]
        assert client.post('/v1/files/pse',content=b'bad',headers=headers).status_code == 400
        broken=client.post('/v1/files/pse',content=b'broken session',headers=headers)
        assert broken.status_code == 422
        assert 'internal converter path' not in broken.text
        assert client.post('/v1/files/blend',content=b'BLENDER',headers=headers).status_code == 422
        assert not compute.calls
    store.close()


def test_budget_shutdown_keeps_unexpired_inputs_until_their_retention_deadline(tmp_path, payload):
    from blender_render.budget import OperatingBudget
    now = [1780000000.]
    store = JobStore(tmp_path, Policy(public=True, ttl_seconds=100), clock=lambda:now[0])
    service = RenderService(store, Compute())
    source = service.import_payload('alice',payload)
    budget = OperatingBudget(store,cap_usd=0)
    stopped=[]
    waited=[]
    def wait(seconds):
        if now[0] < 1780000100:
            assert store.input('alice',source['input_id'])
        waited.append(seconds)
        now[0] += seconds
    budget.stop_unfunded(service,lambda:stopped.append(True),wait=wait)
    assert waited and stopped == [True]
    assert not list(store.inputs.iterdir())
    store.close()


def test_reservation_is_durable_before_claim(tmp_path, payload):
    snapshots = []
    store = JobStore(tmp_path, Policy(public=True), sync=lambda: snapshots.append("committed"))
    identifier = store.add_input("a", payload)["input_id"]
    store.submit("a", identifier, {}, "x")
    count = len(snapshots)
    assert store.quota("a")["reserved_usd"] == pytest.approx(.312)
    assert store.claim()
    assert len(snapshots) == count + 1
    store.close()


def test_volume_commit_failure_closes_admission(tmp_path, payload):
    store = JobStore(tmp_path)
    source = store.add_input("a", payload)
    def broken():
        raise OSError("storage disconnected")
    store.sync = broken
    with pytest.raises(OSError):
        store.submit("a", source["input_id"], {}, "one")
    assert not store.healthy
    with pytest.raises(JobError, match="unavailable"):
        store.claim()
    store.close()


def test_unknown_execution_is_never_replayed_or_refunded(tmp_path, payload):
    store = JobStore(tmp_path, Policy(public=True))
    source = store.add_input("a", payload)
    one = store.submit("a", source["input_id"], {}, "one")
    store.claim()
    store.close()
    store = JobStore(tmp_path, Policy(public=True))
    service = RenderService(store, Compute())
    assert store.get("a", one["job_id"])["status"] == "failed"
    assert store.quota("a")["estimated_spent_usd"] == pytest.approx(.312)
    assert store.claim() is None
    store.close()


def test_owner_isolation_and_authenticated_artifacts(tmp_path, payload):
    store = JobStore(tmp_path)
    service = RenderService(store, Compute())
    app = create_app(service, token=TOKEN, tokens={TOKEN:"a", SECOND_TOKEN:"b"})
    a, b = {"Authorization":"Bearer " + TOKEN}, {"Authorization":"Bearer " + SECOND_TOKEN}
    with TestClient(app) as client:
        assert client.post("/v1/structures", json={"payload":payload}).status_code == 401
        source = client.post("/v1/structures", json={"payload":payload}, headers=a).json()
        assert client.post("/v1/jobs", json={"input_id":source["input_id"], "idempotency_key":"bad"}, headers=b).status_code == 404
        job = client.post("/v1/jobs", json={"input_id":source["input_id"], "idempotency_key":"one"}, headers=a).json()
        await_status(store, "a", job["job_id"], "done")
        path = "/v1/jobs/" + job["job_id"]
        assert client.get(path, headers=b).status_code == 404
        assert client.get(path + "/artifacts/render.png", headers=b).status_code == 404
        result = client.get(path + "/result", headers=a).json()
        url = result["artifacts"][0]["download_url"]
        assert client.get(url).headers["content-type"] == "image/png"
        assert client.get(url[:-1] + "z").status_code == 401
        assert client.get(path + "/artifacts/secrets", headers=a).status_code == 404
    store.close()


def test_running_and_queued_cancellation(tmp_path, payload):
    store = JobStore(tmp_path)
    gate = threading.Event()
    service = RenderService(store, Compute(gate))
    source = service.import_payload("a", payload)
    first = service.submit("a", source["input_id"], {}, "one")
    second = service.submit("a", source["input_id"], {}, "two")
    assert store.cancel("a", second["job_id"])["status"] == "cancelled"
    service.start()
    try:
        await_status(store, "a", first["job_id"], "running")
        store.cancel("a", first["job_id"])
        await_status(store, "a", first["job_id"], "cancelled")
        assert len(service.backend.calls) == 1
    finally:
        service.stop()
        store.close()


def test_daily_quota_credit_expiry_and_retention(tmp_path, payload):
    now = [1800000000.0]
    store = JobStore(tmp_path, Policy(public=True, ttl_seconds=10), clock=lambda: now[0])
    source = store.add_input("a", payload)
    first = store.submit("a", source["input_id"], {}, "one")
    with pytest.raises(JobError, match="active"):
        store.submit("a", source["input_id"], {}, "two")
    store.claim(); store.finish(first["job_id"], seconds=600)
    second = store.submit("a", source["input_id"], {}, "two")
    store.claim(); store.finish(second["job_id"], seconds=600)
    with pytest.raises(JobError, match="daily"):
        store.submit("a", source["input_id"], {}, "three")
    now[0] += 11
    store.cleanup()
    assert not list(store.inputs.iterdir())
    with pytest.raises(JobError, match="expired"):
        store.result("a", first["job_id"])
    now[0] = store.policy.expires_at
    with pytest.raises(JobError, match="expired"):
        store.add_input("a", payload)
    store.close()


def test_options_cannot_bypass_public_limits(payload):
    for options in ({"width":4097}, {"height":4097}, {"samples":513}, {"raw_view":True}, {"width":True}, {"script":"evil"}, [], {"rough":"false"}):
        with pytest.raises(JobError):
            prepare_options(options, payload, public=True, timeout=600)
    normal = prepare_options({"save_blend":True}, payload, public=True, timeout=600)
    assert normal["style_doc"]["output"]["width"] == 2048
    assert normal["style_doc"]["output"]["save_blend"] is True
    assert normal["style_doc"]["renderer"]["require_gpu"] is True


def test_malformed_payload_and_expiring_links(payload):
    for value in (None, {}, {**payload, "cif_text":"data_invalid"}):
        with pytest.raises(JobError):
            validate_input(value, max_atoms=100, max_bytes=100000)
    with pytest.raises(JobError) as exc:
        validate_input(payload, max_atoms=100, max_bytes=1)
    assert exc.value.status == 413
    clock = [100]
    links = Downloads(TOKEN, "https://example.org", clock=lambda: clock[0])
    signed = links.url("a", "job", "render.png", ttl=10).rsplit("/",1)[1]
    assert links.verify(signed) == ("a", "job", "render.png")
    clock[0] = 110
    with pytest.raises(JobError) as exc:
        links.verify(signed)
    assert exc.value.status == 410
