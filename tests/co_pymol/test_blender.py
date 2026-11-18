"""Exercise the adapter across a real subprocess, without a GPU allocation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from co_pymol.render import client as adapter
from co_pymol.render.client import BlenderBridge, BlenderConfig, write_json


# Fakes of this checkout's renderer modules, imported by the detached worker in
# place of the real `mol_blender` package (see the `bridge` fixture). Each
# call is appended to MOL_BLENDER_HOME/calls; marker files there steer them.
_FAKE_COMMON = '''
import os
from pathlib import Path
import base64
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=")
root = Path(os.environ["MOL_BLENDER_HOME"])
def record(action):
    root.mkdir(exist_ok=True)
    with (root / "calls").open("a") as f: f.write(action + "\\n")
def deliver(output):
    import json
    output = Path(output).with_suffix(".png")
    artifacts = output.with_suffix(".render")
    artifacts.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"not a PNG" if (root / "invalid_png").exists() else PNG)
    device = "CPU" if (root / "cpu").exists() else "GPU"
    (artifacts / "manifest.json").write_text(json.dumps(
        {"cycles_device": {"device": device, "devices": [{"name": "test GPU"}]}}))
    return {"status": "completed", "artifacts": str(artifacts),
            "outputs": {"render.png": str(output),
                        "manifest.json": str(artifacts / "manifest.json")}}
'''

FAKE_LOCAL = '''
import json, time
from mol_blender._fake import deliver, record, root
from mol_blender.errors import RenderCancelled
def render_payload(payload, *, output, options=None, timeout=600, cancelled=None):
    record("local:render")
    (root / "payload.json").write_text(json.dumps(payload))
    (root / "local_call.json").write_text(json.dumps(
        {"options": options, "timeout": timeout, "output": str(output)}))
    while (root / "hold_render").exists():
        if cancelled and cancelled(): raise RenderCancelled("cancelled")
        time.sleep(0.02)
    return deliver(output)
'''

FAKE_CLUSTER_CLIENT = '''
import json, time
from pathlib import Path
from mol_blender._fake import deliver, record, root
class RemoteError(RuntimeError):
    pass
class Client:
    """Follows the real receipt contract: written before upload, recovered by request ID."""
    def __init__(self, profile="default"):
        self.profile = profile
    def submit(self, payload, options, output, request_id=None):
        receipt = Path(output).with_suffix(".cluster.json")
        if request_id:  # Resent under the receipt's own request ID.
            saved = json.loads(receipt.read_text())
            assert saved["request_id"] == request_id and not saved.get("job_id")
            record("resend")
            (root / "payload.json").write_text(json.dumps(payload))
            receipt.write_text(json.dumps({**saved, "job_id": "job-1", "status": "queued"}))
            return {"job_id": "job-1", "status": "queued", "receipt": str(receipt)}
        record("submitting:" + self.profile)
        while (root / "hold_submit").exists(): time.sleep(0.02)
        receipt = Path(output).with_suffix(".cluster.json")
        receipt.write_text(json.dumps({"profile": self.profile, "request_id": "r" * 32,
                                       "output": str(Path(output)), "status": "prepared"}))
        record("submit")
        (root / "payload.json").write_text(json.dumps(payload))
        if (root / "ambiguous").exists():
            raise RemoteError("Cluster connection failed; recover the existing request")
        receipt.write_text(json.dumps({**json.loads(receipt.read_text()), "job_id": "job-1", "status": "queued"}))
        return {"job_id": "job-1", "status": "queued", "receipt": str(receipt)}
    def recover(self, receipt):
        saved = json.loads(Path(receipt).read_text())
        if saved.get("job_id"):
            return self.status(saved["job_id"])
        record("recover")
        if (root / "offline").exists():
            raise RemoteError("Cluster connection failed")
        if not (root / "received").exists():
            raise RemoteError("Cluster HTTP 404: Job not found")
        return {**self.status("job-1"), "job_id": "job-1"}
    def api(self, method, path, data=None):
        record(method + " " + path)
        if (root / "offline").exists():
            raise RemoteError("Cluster connection failed")
        (root / "cancelled").touch()
        return {"status": "cancelling"}
    def status(self, job_id):
        record("status")
        if (root / "offline").exists():
            raise RemoteError("Cluster connection failed")
        if (root / "cancelled").exists(): return {"status": "cancelled", "job_id": job_id}
        if (root / "unknown").exists():
            return {"status": "submission_unknown", "job_id": job_id}
        if (root / "running").exists():
            return {"status": "running", "stage": "rendering", "job_id": job_id}
        return {"status": "completed", "stage": "completed", "job_id": job_id}
    def fetch(self, job_id, output):
        record("fetch")
        if (root / "fetch_disconnect").exists():
            (root / "fetch_disconnect").unlink()
            raise RemoteError("Cluster connection failed")
        return deliver(output)
'''

CATALOG = [
    {"id": "mint_sky_assembly", "description": "Mint and sky", "aliases": ["mint"]},
    {"id": "pastel_duplex_rounded", "description": "Pastel duplex", "aliases": []},
]


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A checkout whose `mol_blender` package is the fakes above.

    The adapter puts REPO_ROOT on the worker's PYTHONPATH and reads preset
    examples from it, so pointing REPO_ROOT here swaps in the fake renderer.
    """
    repo = tmp_path / "repo"
    package = repo / "mol_blender"
    (package / "cluster").mkdir(parents=True)
    files = {
        "__init__.py": "",
        "_fake.py": _FAKE_COMMON,
        "local.py": FAKE_LOCAL,
        "errors.py": "class RenderCancelled(RuntimeError): pass\n",
        "core.py": "def validate_payload_document(x): return x\n",
        "config.py": (
            "import os\nfrom pathlib import Path\n"
            "def config_root(): return Path(os.environ.get("
            "'MOL_BLENDER_HOME', '~/.config/mol-blender')).expanduser()\n"
        ),
        "presets.py": "def catalog(): return " + repr(CATALOG) + "\n",
        "cluster/__init__.py": "",
        "cluster/client.py": FAKE_CLUSTER_CLIENT,
        # No saved SSH connection: the job API leaves the (fake) tunnel alone.
        "cluster/connect.py": "def load_state(profile): return {}\n",
    }
    for name, content in files.items():
        (package / name).write_text(content)
    # The real job API and wire protocol run against the fakes above.
    real = Path(__file__).resolve().parents[2] / "mol_blender"
    for name in ("jobs.py", "cluster/protocol.py", "cluster/jsonio.py"):
        (package / name).write_text((real / name).read_text())
    monkeypatch.setattr("co_pymol.render.client.REPO_ROOT", repo)
    return repo


def write_config(path: Path, tmp_path: Path, **overrides) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    write_json(
        path,
        {
            "render_python": sys.executable,
            "render_home": str(home),
            "cluster_home": str(home),
            "output_directory": str(tmp_path / "renders"),
            "backend": "cluster",
            "cluster_profile": "test",
            "require_gpu": True,
            "wait_seconds": 30,
            **overrides,
        },
    )
    return path


@pytest.fixture
def bridge(fake_repo, tmp_path):
    config = write_config(tmp_path / "blender.json", tmp_path)
    manager = BlenderBridge()
    manager.configure(str(config))
    yield manager
    for process in manager._processes:
        process.wait(timeout=10)


@pytest.fixture
def local_bridge(fake_repo, tmp_path):
    config = write_config(tmp_path / "blender.json", tmp_path, backend="local")
    manager = BlenderBridge()
    manager.configure(str(config))
    yield manager
    for process in manager._processes:
        process.wait(timeout=10)


def submit(bridge, *, pse=None, name=None, **kwargs):
    return bridge.submit(
        {"cif_text": "frozen scene", "view": {"viewport_px": [800, 450]}},
        preset="mint_sky_assembly",
        width=800,
        height=0,
        samples=16,
        keep_colors=True,
        save_blend=True,
        pse=pse,
        name=name,
        **kwargs,
    )["request_id"]


def wait_for(bridge, request_id, status):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        observed = bridge.status(request_id)
        if observed["status"] == status:
            return observed
        time.sleep(0.02)
    pytest.fail(f"Expected {status}, got {observed}")


def wait_for_runs(bridge, request_id, status):
    """Wait until every run of a batch reports status."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        observed = bridge.status(request_id)
        if observed["runs"] and all(r["status"] == status for r in observed["runs"]):
            return observed
        time.sleep(0.02)
    pytest.fail(f"Expected every run {status}, got {observed}")


def calls(bridge):
    log = Path(bridge.config.render_home) / "calls"
    return log.read_text().splitlines() if log.exists() else []


def stop_helpers(bridge):
    """Simulate PyMOL or the computer stopping while helpers are mid-job."""
    for process in bridge._processes:
        process.kill()
        process.wait(timeout=5)
    bridge._processes.clear()


@pytest.fixture
def preset_backend(bridge, fake_repo):
    from PIL import Image

    root = fake_repo
    directory = root / "examples/presets"
    directory.mkdir(parents=True)
    examples = []
    for entry, color in zip(CATALOG, ("cyan", "pink"), strict=True):
        path = directory / (entry["id"] + ".png")
        Image.new("RGB", (100, 70), color).save(path)
        examples.append(
            {
                "preset": entry["id"],
                "thumbnail": str(path.relative_to(root)),
                "source_id": "TEST",
                "thumbnail_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    write_json(directory / "gallery.json", examples)
    return bridge


def test_presets_produce_cached_gallery_without_allocating(preset_backend):
    from PIL import Image

    bridge = preset_backend
    catalog = bridge.presets(images=True)
    assert len(catalog["presets"]) == 2
    assert all(p["preview_available"] for p in catalog["presets"])
    preview = Path(catalog["preview_path"])
    with Image.open(preview) as image:
        assert image.format == "PNG"
        image.verify()
    modified = preview.stat().st_mtime_ns
    assert bridge.presets(images=True)["preview_path"] == str(preview)
    assert preview.stat().st_mtime_ns == modified
    assert not (Path(bridge.config.render_home) / "calls").exists()
    assert not Path(bridge.config.output_directory).exists()


def test_missing_examples_still_return_preset_choices(preset_backend):
    bridge = preset_backend
    (adapter.REPO_ROOT / "examples/presets/gallery.json").unlink()
    catalog = bridge.presets(images=True)
    assert len(catalog["presets"]) == 2
    assert "preview_path" not in catalog
    assert "unavailable" in catalog["preview_warning"]


def test_changed_preview_is_not_presented_as_verified_example(preset_backend):
    bridge = preset_backend
    (
        adapter.REPO_ROOT / "examples/presets/mint_sky_assembly.png"
    ).write_bytes(b"changed")
    catalog = bridge.presets(images=True)
    assert catalog["presets"][0]["preview_available"] is False
    assert catalog["presets"][1]["preview_available"] is True


def test_preset_tool_returns_native_image_without_rendering(
    preset_backend, monkeypatch
):
    from co_pymol.server import create_server

    bridge = preset_backend
    monkeypatch.setattr("co_pymol.tools.blender.BlenderBridge", lambda: bridge)

    async def check():
        server = create_server()
        result = await server.call_tool("blender_presets", {"images": True})
        assert any(getattr(item, "type", None) == "image" for item in result)
        text = next(item.text for item in result if item.type == "text")
        assert len(json.loads(text)["presets"]) == 2

    asyncio.run(check())
    assert not (Path(bridge.config.render_home) / "calls").exists()


def test_chat_progress_does_not_confuse_capture_with_remote_rendering():
    starting = BlenderBridge.presentation({"status": "starting"})
    assert "saved locally" in starting["message"]
    assert starting["next_action"] == "return_to_user"
    rendering = BlenderBridge.presentation(
        {
            "status": "running",
            "backend": "cluster",
            "remote": {"stage": "rendering", "internal": "diagnostics"},
        }
    )
    assert rendering["stage"] == "rendering"
    assert rendering["message"] == "Blender is rendering."
    assert rendering["backend"] == "cluster"
    assert "remote" not in rendering
    interrupted = BlenderBridge.presentation(
        {
            "status": "needs_attention",
            "job_id": "original",
            "remote": {"stage": "rendering"},
            "error": "Connection interrupted",
        }
    )
    assert interrupted["stage"] == "needs_attention"
    assert interrupted["next_action"] == "blender_result"
    assert interrupted["error"] == "Connection interrupted"


def test_default_presets_are_text_only_without_building_gallery(
    preset_backend, monkeypatch
):
    from co_pymol.server import create_server

    bridge = preset_backend
    monkeypatch.setattr("co_pymol.tools.blender.BlenderBridge", lambda: bridge)

    async def check():
        result = await create_server().call_tool("blender_presets", {})
        assert all(item.type == "text" for item in result)
        catalog = json.loads(result[0].text)
        assert len(catalog["presets"]) == 2
        assert "preview_path" not in catalog
        assert catalog["presets"][0]["description"] == "Mint and sky"

    asyncio.run(check())
    home = Path(bridge.config.render_home)
    assert not (home / "co-pymol-previews").exists()
    assert not (home / "calls").exists()


def test_batch_preset_selection_is_explicit_and_deduplicated(preset_backend):
    bridge = preset_backend
    assert bridge.select_presets(None, True) == [
        "mint_sky_assembly",
        "pastel_duplex_rounded",
    ]
    assert bridge.select_presets(["mint", "mint_sky_assembly"], False) == [
        "mint_sky_assembly"
    ]
    for selected, all_presets in ((None, False), (["mint"], True), (["bad"], False)):
        with pytest.raises(ValueError):
            bridge.select_presets(selected, all_presets)
    assert not Path(bridge.config.output_directory).exists()


def submit_batch(bridge, *, name="quiet-lynx"):
    return bridge.submit_batch(
        {"cif_text": "frozen scene", "view": {"viewport_px": [800, 450]}},
        presets=["mint_sky_assembly", "pastel_duplex_rounded"],
        pse=b"one frozen session",
        name=name,
        width=800,
        height=0,
        samples=16,
        keep_colors=True,
        save_blend=False,
    )


def test_batch_returns_and_other_pymol_tool_runs_while_remote_is_blocked(
    preset_backend, monkeypatch
):
    from co_pymol.server import create_server
    from co_pymol.scene.session import pymol_lock, pymol_session

    bridge = preset_backend
    gate = Path(bridge.config.render_home) / "hold_submit"
    gate.touch()
    captures = []

    class Cmd:
        def count_atoms(self):
            assert pymol_lock.locked()
            return 42

    def capture_scene(*args):
        assert pymol_lock.locked()
        captures.append("scene")
        return {"cif_text": "frozen scene", "view": {"viewport_px": [800, 450]}}

    def capture_pse(*args):
        assert pymol_lock.locked()
        captures.append("pse")
        return b"one frozen session"

    monkeypatch.setattr("co_pymol.tools.blender.BlenderBridge", lambda: bridge)
    monkeypatch.setattr("co_pymol.tools.blender.capture_scene", capture_scene)
    monkeypatch.setattr("co_pymol.tools.blender.capture_pse", capture_pse)
    monkeypatch.setattr(pymol_session, "_cmd", Cmd())

    async def check():
        server = create_server()
        result = await asyncio.wait_for(
            server.call_tool("mol_blender_batch", {"all_presets": True}), timeout=3
        )
        blocks = result[0] if isinstance(result, tuple) else result
        receipt = json.loads(next(item.text for item in blocks if item.type == "text"))
        assert receipt["status"] == "accepted_locally"
        assert receipt["count"] == 2
        assert receipt["next_action"] == "return_to_user"
        assert captures == ["pse", "scene"]
        assert not pymol_lock.locked()
        # Wait for the detached helpers to reach the fake blocked network call.
        deadline = time.monotonic() + 3
        log = Path(bridge.config.render_home) / "calls"
        while not log.exists() or log.read_text().count("submitting:test") < 2:
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        assert "submit" not in calls(bridge)
        following = await asyncio.wait_for(
            server.call_tool("run", {"code": "print(cmd.count_atoms())"}), timeout=1
        )
        following = following[0] if isinstance(following, tuple) else following
        assert (
            next(item.text for item in following if item.type == "text").strip() == "42"
        )
        return receipt

    try:
        receipt = asyncio.run(check())
        directory = Path(receipt["directory"])
        assert list(directory.rglob("scene.pse")) == [directory / "scene.pse"]
        assert list(directory.rglob("scene.bundle.json")) == [
            directory / "scene.bundle.json"
        ]
        for run in receipt["runs"]:
            request = json.loads((Path(run["directory"]) / "request.json").read_text())
            assert request["scene_path"] == str(directory / "scene.bundle.json")
            assert request["batch_id"] == receipt["batch_id"]
    finally:
        gate.unlink()
    wait_for(bridge, receipt["name"], "completed")
    restarted = BlenderBridge()
    restarted._config = bridge.config
    recent = restarted.recent()["renders"]
    assert len(recent) == 2
    assert {run["batch_id"] for run in recent} == {receipt["batch_id"]}
    assert restarted.result(recent[0]["name"])[1].startswith(b"\x89PNG")
    assert calls(bridge).count("submit") == 2


def test_batch_recovers_existing_jobs_after_disconnect_without_resubmission(bridge):
    offline = Path(bridge.config.render_home) / "offline"
    offline.touch()
    receipt = submit_batch(bridge)
    state = wait_for_runs(bridge, receipt["name"], "recovering")
    assert all(run["job_id"] == "job-1" for run in state["runs"])
    stop_helpers(bridge)
    offline.unlink()
    restarted = BlenderBridge()
    restarted._config = bridge.config
    restarted.result(receipt["name"])
    try:
        wait_for(restarted, receipt["name"], "completed")
    finally:
        for process in restarted._processes:
            process.wait(timeout=5)
    assert calls(bridge).count("submit") == 2


def test_batch_can_cancel_before_remote_submission(bridge, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "_launch", lambda *args: None)
        receipt = submit_batch(bridge)
    assert bridge.status(receipt["name"])["status"] == "starting"
    bridge.cancel(receipt["name"])
    state = wait_for(bridge, receipt["name"], "cancelled")
    assert state["counts"] == {"cancelled": 2}
    assert not (Path(bridge.config.render_home) / "calls").exists()


def test_batch_recovery_does_not_repeat_ambiguous_submissions(bridge):
    (Path(bridge.config.render_home) / "ambiguous").touch()
    receipt = submit_batch(bridge)
    for process in bridge._processes:
        process.wait(timeout=5)
    assert bridge.status(receipt["name"])["counts"] == {"needs_attention": 2}
    bridge.result(receipt["name"])
    for process in bridge._processes:
        process.wait(timeout=5)
    assert calls(bridge).count("submit") == 2


def test_saved_result_survives_restart_and_rejects_modified_png(bridge):
    request_id = submit(bridge)
    complete = wait_for(bridge, request_id, "completed")
    restarted = BlenderBridge()
    restarted._config = bridge.config
    state, png = restarted.result(request_id)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert state["png_path"] == complete["png_path"]
    request = json.loads((Path(complete["directory"]) / "request.json").read_text())
    assert "height" not in request["options"]  # upstream preserves aspect ratio
    assert request["options"]["color_follow"] is True
    Path(complete["png_path"]).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="Saved PNG changed"):
        restarted.result(request_id)
    assert calls(bridge).count("submit") == 1


def test_named_render_is_found_after_restart_without_chat_history(bridge):
    request_id = submit(bridge, name="5GGS Binding Site", pse=b"saved session")
    completed = wait_for(bridge, request_id, "completed")
    assert Path(completed["directory"]).name == "5ggs-binding-site"
    request = json.loads((Path(completed["directory"]) / "request.json").read_text())
    assert request["request_id"] == request_id
    assert request["name"] == "5ggs-binding-site"
    restarted = BlenderBridge()
    restarted._config = bridge.config
    before = calls(bridge)
    recent = restarted.recent()["renders"]
    assert len(recent) == 1
    assert recent[0]["name"] == "5ggs-binding-site"
    state, image = restarted.result(recent[0]["name"])
    assert state["request_id"] == request_id
    assert state["status"] == "completed"
    assert image is not None
    assert Path(state["outputs"]["scene.pse"]).read_bytes() == b"saved session"
    assert calls(bridge) == before  # listing and a saved result are offline


@pytest.mark.parametrize("name", [None, "", "  "])
def test_omitted_or_blank_name_gets_an_automatic_name(bridge, monkeypatch, name):
    monkeypatch.setattr(
        "co_pymol.render.client.random_render_name", lambda: "mellow-otter"
    )
    request_id = submit(bridge, name=name)
    completed = wait_for(bridge, request_id, "completed")
    assert completed["name"] == "mellow-otter"
    assert request_id != "mellow-otter"
    assert bridge.status("mellow-otter")["request_id"] == request_id


def test_automatic_name_collision_chooses_another_name(bridge, monkeypatch):
    existing = Path(bridge.config.output_directory) / "mellow-otter"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("keep")
    names = iter(["mellow-otter", "curious-heron"])
    monkeypatch.setattr("co_pymol.render.client.random_render_name", lambda: next(names))
    request_id = submit(bridge)
    assert wait_for(bridge, request_id, "completed")["name"] == "curious-heron"
    assert (existing / "keep.txt").read_text() == "keep"


def test_concurrent_custom_names_never_overwrite_each_other(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_launch", lambda *args: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(submit, bridge, name="binding-site", pse=content)
            for content in (b"first scene", b"second scene")
        ]
        ids = [future.result() for future in futures]
    states = [bridge.status(request_id) for request_id in ids]
    assert len(set(ids)) == 2
    assert {s["name"] for s in states} == {"binding-site", "binding-site-2"}
    assert {Path(s["outputs"]["scene.pse"]).read_bytes() for s in states} == {
        b"first scene",
        b"second scene",
    }


@pytest.mark.parametrize(
    "name", ["../escape", "/tmp/escape", "a/b", "a\\b", ".", "x" * 65, "a" * 32]
)
def test_invalid_names_do_not_write_or_submit(bridge, name):
    with pytest.raises(ValueError, match="name"):
        submit(bridge, name=name)
    assert not Path(bridge.config.output_directory).exists()
    assert not bridge._processes


def test_recent_is_newest_first_and_skips_unrelated_folders(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_launch", lambda *args: None)
    first = submit(bridge, name="first")
    second = submit(bridge, name="second")
    root = Path(bridge.config.output_directory)
    unrelated = root / "other-project"
    unrelated.mkdir()
    (unrelated / "request.json").write_text("broken json")
    (root / "symlink").symlink_to(root / "first", target_is_directory=True)
    recent = bridge.recent(limit=1)
    assert recent["total"] == 2
    assert [r["request_id"] for r in recent["renders"]] == [second]
    (root / "first" / "state.json").write_text("broken state")
    recent = bridge.recent()["renders"]
    assert recent[1]["request_id"] == first
    assert recent[1]["status"] == "needs_attention"
    assert "unreadable" in recent[1]["error"]


def test_legacy_uuid_directory_still_lists_and_returns_its_image(bridge):
    request_id = "a" * 32
    directory = Path(bridge.config.output_directory) / request_id
    directory.mkdir(parents=True)
    image = b"previously verified image"
    (directory / "render.png").write_bytes(image)
    write_json(
        directory / "request.json",
        {
            "request_id": request_id,
            "options": {"preset": "mint_sky_assembly"},
        },
    )
    write_json(
        directory / "state.json",
        {
            "status": "completed",
            "png_path": str(directory / "render.png"),
            "png_sha256": hashlib.sha256(image).hexdigest(),
        },
    )
    assert bridge.recent()["renders"][0]["request_id"] == request_id
    state, recovered = bridge.result(request_id)
    assert recovered == image
    assert state["name"] == request_id
    assert not bridge._processes


def test_pse_is_saved_before_submission_and_survives_result_recovery(
    bridge, monkeypatch
):
    from co_pymol.scene.session import pymol_lock

    pse = b"frozen session bytes"
    launch = bridge._launch

    def checked_launch(cfg, directory, action):
        assert not pymol_lock.locked()
        assert (directory / "scene.pse").read_bytes() == pse
        assert (directory / "scene.pse").stat().st_mode & 0o777 == 0o600
        return launch(cfg, directory, action)

    monkeypatch.setattr(bridge, "_launch", checked_launch)
    request_id = submit(bridge, pse=pse)
    wait_for(bridge, request_id, "completed")
    restarted = BlenderBridge()
    restarted._config = bridge.config
    state, image = restarted.result(request_id)
    assert image is not None
    assert state["outputs"]["scene.pse"] == state["pse_path"]
    assert Path(state["pse_path"]).read_bytes() == pse
    assert state["pse_sha256"] == hashlib.sha256(pse).hexdigest()
    submitted = json.loads(
        (Path(bridge.config.render_home) / "payload.json").read_text()
    )
    assert submitted == {"cif_text": "frozen scene", "view": {"viewport_px": [800, 450]}}
    assert calls(bridge).count("submit") == 1


def test_pse_write_failure_prevents_submission(bridge, monkeypatch):
    original_open = Path.open

    def open_file(path, *args, **kwargs):
        if path.name == "scene.pse":
            raise OSError("disk full")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(OSError, match="disk full"):
        submit(bridge, pse=b"frozen session")
    assert not bridge._processes
    assert not (Path(bridge.config.render_home) / "calls").exists()


def test_disconnect_recovers_existing_job_without_resubmitting(bridge):
    offline = Path(bridge.config.render_home) / "offline"
    offline.touch()
    request_id = submit(bridge, name="quiet-lynx")
    state = wait_for(bridge, request_id, "recovering")
    assert state["job_id"] == "job-1"
    stop_helpers(bridge)
    offline.unlink()
    restarted = BlenderBridge()
    restarted._config = bridge.config
    name = restarted.recent()["renders"][0]["name"]
    restarted.result(name)
    wait_for(restarted, name, "completed")
    for process in restarted._processes:
        process.wait(timeout=5)
    assert calls(bridge).count("submit") == 1


def test_ambiguous_submission_result_recovers_the_saved_request(bridge):
    (Path(bridge.config.render_home) / "ambiguous").touch()
    request_id = submit(bridge)
    state = wait_for(bridge, request_id, "needs_attention")
    assert "Cluster connection failed" in state["error"]
    receipt = Path(state["directory"]) / "render.cluster.json"
    original = json.loads(receipt.read_text())
    for process in bridge._processes:
        process.wait(timeout=5)
    assert bridge.result(request_id)[1] is None
    # result() resumes a saved receipt even before a job ID is known. Counting
    # live Popen objects races the original helper's exit and does not establish
    # whether a new job was submitted. Observe the completed recovery instead.
    for process in bridge._processes:
        process.wait(timeout=5)
    assert calls(bridge).count("submit") == 1
    assert calls(bridge).count("recover") == 1
    assert calls(bridge).count("resend") == 1
    assert json.loads(receipt.read_text())["request_id"] == original["request_id"]
    assert bridge.status(request_id)["status"] == "completed"


def test_a_submission_the_gateway_never_received_is_resent_under_its_request_id(bridge):
    (Path(bridge.config.render_home) / "ambiguous").touch()
    request_id = submit(bridge)
    directory = Path(wait_for(bridge, request_id, "needs_attention")["directory"])
    for process in bridge._processes:
        process.wait(timeout=5)
    (Path(bridge.config.render_home) / "ambiguous").unlink()
    bridge._launch(bridge.config, directory, "recover")
    for process in bridge._processes:
        process.wait(timeout=5)
    state = bridge.status(request_id)
    assert state["status"] == "completed" and state["job_id"] == "job-1"
    assert calls(bridge).count("recover") == 1  # Looked up by its receipt's request ID first.
    assert calls(bridge).count("resend") == 1  # Then sent again with that ID, never a new one.
    assert calls(bridge).count("submit") == 1


def test_a_helper_that_cannot_connect_over_ssh_still_recovers_by_its_receipt(bridge, fake_repo):
    home = Path(bridge.config.render_home)
    (home / "ambiguous").touch()
    request_id = submit(bridge)
    wait_for(bridge, request_id, "needs_attention")
    for process in bridge._processes:
        process.wait(timeout=5)
    # Now the saved SSH connection cannot be opened (MFA, VPN down, login node away).
    (fake_repo / "mol_blender/cluster/connect.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def load_state(profile): return {'ssh_host': 'lab'}\n"
        "def state_path(profile, create=False):\n"
        "    return Path(os.environ['MOL_BLENDER_HOME']) / 'connections' / (profile + '.json')\n"
        "def connect(profile): raise RuntimeError('ssh: MFA required')\n")
    (home / "ambiguous").unlink()
    (home / "received").touch()
    bridge.result(request_id)  # Recovery carries on with the saved profile.
    assert wait_for(bridge, request_id, "completed")["job_id"] == "job-1"
    assert calls(bridge).count("submit") == 1


def test_ambiguous_submission_the_gateway_received_is_adopted_from_its_receipt(bridge):
    home = Path(bridge.config.render_home)
    (home / "ambiguous").touch()
    request_id = submit(bridge)
    wait_for(bridge, request_id, "needs_attention")
    for process in bridge._processes:
        process.wait(timeout=5)
    assert bridge.presentation(bridge.status(request_id))["next_action"] == "blender_result"
    (home / "ambiguous").unlink()
    (home / "received").touch()  # The upload reached the gateway after all.
    bridge.result(request_id)
    state = wait_for(bridge, request_id, "completed")
    assert state["job_id"] == "job-1"
    receipt = json.loads((Path(state["directory"]) / "render.cluster.json").read_text())
    assert receipt["job_id"] == "job-1"  # Later checks use the job directly.
    assert calls(bridge).count("submit") == 1


def test_unconfirmed_slurm_submission_stays_recoverable(bridge):
    home = Path(bridge.config.render_home)
    (home / "unknown").touch()
    request_id = submit(bridge)
    state = wait_for(bridge, request_id, "needs_attention")
    assert state["job_id"] == "job-1" and "reconciliation" in state["error"]
    assert bridge.presentation(state)["next_action"] == "blender_result"
    for process in bridge._processes:
        process.wait(timeout=5)
    (home / "unknown").unlink()  # The gateway reconciled it into a real job.
    bridge.result(request_id)
    wait_for(bridge, request_id, "completed")
    assert calls(bridge).count("submit") == 1


def test_temporary_disconnect_resumes_automatically(bridge):
    offline = Path(bridge.config.render_home) / "offline"
    offline.touch()
    request_id = submit(bridge)
    state = wait_for(bridge, request_id, "recovering")
    assert state["job_id"] == "job-1"
    assert "Cluster connection failed" in state["error"]
    offline.unlink()
    wait_for(bridge, request_id, "completed")
    assert calls(bridge).count("submit") == 1


def test_interrupted_download_resumes_same_job(bridge):
    (Path(bridge.config.render_home) / "fetch_disconnect").touch()
    request_id = submit(bridge)
    state = wait_for(bridge, request_id, "needs_attention")
    assert state["job_id"] == "job-1"
    assert bridge.presentation(state)["next_action"] == "blender_result"
    for process in bridge._processes:
        process.wait(timeout=5)
    bridge.result(request_id)
    wait_for(bridge, request_id, "completed")
    assert calls(bridge).count("fetch") == 2
    assert calls(bridge).count("submit") == 1


def test_cancel_running_cluster_job_reaches_the_service(bridge):
    home = Path(bridge.config.render_home)
    (home / "running").touch()
    request_id = submit(bridge)
    state = wait_for(bridge, request_id, "running")
    assert state["job_id"] == "job-1"
    assert bridge.presentation(state)["stage"] == "rendering"
    bridge.cancel(request_id)
    wait_for(bridge, request_id, "cancelled")
    assert calls(bridge).count("POST /v1/jobs/job-1/cancel") == 1
    assert "fetch" not in calls(bridge)


def test_cancel_disconnected_job_uses_backend_cancel(bridge):
    offline = Path(bridge.config.render_home) / "offline"
    offline.touch()
    request_id = submit(bridge)
    wait_for(bridge, request_id, "recovering")
    stop_helpers(bridge)
    offline.unlink()
    bridge.cancel(bridge.status(request_id)["name"])
    wait_for(bridge, request_id, "cancelled")
    assert "POST /v1/jobs/job-1/cancel" in calls(bridge)
    assert "fetch" not in calls(bridge)
    assert calls(bridge).count("submit") == 1


def test_invalid_png_is_not_an_image(bridge):
    (Path(bridge.config.render_home) / "invalid_png").touch()
    request_id = submit(bridge)
    state = wait_for(bridge, request_id, "needs_attention")
    assert "invalid PNG" in state["error"]
    state, image = bridge.result(request_id)
    assert image is None
    assert state["status"] != "completed"


def test_cpu_fallback_is_rejected_when_gpu_required(bridge):
    (Path(bridge.config.render_home) / "cpu").touch()
    request_id = submit(bridge)
    state = wait_for(bridge, request_id, "needs_attention")
    assert "does not confirm GPU" in state["error"]
    assert state.get("png_path") is None


def test_request_id_cannot_escape_output_directory(bridge):
    with pytest.raises(ValueError, match="request_id"):
        bridge.status("../request")


def test_network_work_starts_only_after_pymol_lock_is_released(bridge, monkeypatch):
    from co_pymol.tools.blender import register_blender_tools
    from co_pymol.scene.session import pymol_lock, pymol_session

    registered = {}

    class Server:
        def tool(self, **kwargs):
            def register(fn):
                registered[fn.__name__] = fn
                return fn

            return register

    def capture(*args):
        assert pymol_lock.locked()
        return {"snapshot": True}

    def capture_session(cmd):
        assert pymol_lock.locked()
        return b"frozen session"

    def submit_scene(payload, **kwargs):
        assert not pymol_lock.locked()
        assert payload == {"snapshot": True}
        assert kwargs["pse"] == b"frozen session"
        assert kwargs["name"] == "binding-site"
        return {"status": "starting"}

    monkeypatch.setattr("co_pymol.tools.blender.BlenderBridge", lambda: bridge)
    monkeypatch.setattr("co_pymol.tools.blender.capture_scene", capture)
    monkeypatch.setattr("co_pymol.tools.blender.capture_pse", capture_session)
    monkeypatch.setattr(pymol_session, "_cmd", object())
    monkeypatch.setattr(bridge, "submit", submit_scene)
    register_blender_tools(Server())
    result = registered["mol_blender"](name="Binding Site")
    assert json.loads(result)["status"] == "starting"


def test_mcp_registration_and_native_image_content(bridge, monkeypatch):
    from co_pymol.server import create_server

    monkeypatch.setattr("co_pymol.tools.blender.BlenderBridge", lambda: bridge)
    request_id = submit(bridge, pse=b"frozen session")
    wait_for(bridge, request_id, "completed")

    async def check():
        server = create_server()
        names = {tool.name for tool in await server.list_tools()}
        assert {
            "render",
            "snapshot",
            "mol_blender",
            "blender_result",
            "mol_blender_renders",
        } <= names
        render_tool = next(
            t for t in await server.list_tools() if t.name == "mol_blender"
        )
        assert "name" in render_tool.inputSchema["properties"]
        assert "name" not in render_tool.inputSchema.get("required", [])
        listed = await server.call_tool("mol_blender_renders", {})
        # FastMCP may return structured content alongside the text blocks.
        blocks = listed[0] if isinstance(listed, tuple) else listed
        listing = json.loads(next(item.text for item in blocks if item.type == "text"))
        name = listing["renders"][0]["name"]
        result = await server.call_tool("blender_result", {"request_id": name})
        assert any(getattr(item, "type", None) == "image" for item in result)
        text = next(item.text for item in result if item.type == "text")
        assert json.loads(text)["outputs"]["scene.pse"].endswith("/scene.pse")

    asyncio.run(check())


def test_local_backend_renders_with_this_checkout(local_bridge):
    bridge = local_bridge
    request_id = submit(bridge, pse=b"local session")
    complete = wait_for(bridge, request_id, "completed")
    assert calls(bridge) == ["local:render"]
    directory = Path(complete["directory"])
    call = json.loads((Path(bridge.config.render_home) / "local_call.json").read_text())
    assert call["output"] == str(directory / "render.png")
    assert call["timeout"] == bridge.config.wait_seconds
    assert call["options"]["preset"] == "mint_sky_assembly"
    assert call["options"]["color_follow"] is True
    assert complete["backend"] == "local"
    assert complete["gpu"]["device"] == "GPU"
    assert isinstance(complete["worker_pid"], int)
    state, png = bridge.result(request_id)
    assert png.startswith(b"\x89PNG") and state["png_path"] == str(directory / "render.png")


def test_request_backend_lab_routes_to_the_cluster(local_bridge):
    bridge = local_bridge
    request_id = submit(bridge, backend="lab")
    complete = wait_for(bridge, request_id, "completed")
    assert complete["backend"] == "cluster"
    assert complete["job_id"] == "job-1"
    assert calls(bridge)[:2] == ["submitting:test", "submit"]
    assert "local:render" not in calls(bridge)
    request = json.loads((Path(complete["directory"]) / "request.json").read_text())
    assert request["config"]["backend"] == "cluster"
    # Only that request moved; the configured default is unchanged.
    assert bridge.config.backend == "local"
    with pytest.raises(ValueError, match="backend"):
        submit(bridge, backend="somewhere")


def test_local_cancel_waits_for_the_renderer_to_stop(local_bridge):
    bridge = local_bridge
    hold = Path(bridge.config.render_home) / "hold_render"
    hold.touch()
    try:
        request_id = submit(bridge)
        state = wait_for(bridge, request_id, "rendering")
        process = bridge._processes[0]
        assert state["worker_pid"] == process.pid
        assert bridge.cancel(request_id)["cancel_requested"]
        wait_for(bridge, request_id, "cancelled")
        assert process.wait(timeout=5) == 0
    finally:
        hold.unlink()
    state = bridge.status(request_id)
    assert state["status"] == "cancelled"
    assert not (Path(state["directory"]) / "render.png").exists()


def test_cancelling_one_local_batch_child_preserves_its_sibling(local_bridge):
    bridge = local_bridge
    hold = Path(bridge.config.render_home) / "hold_render"
    hold.touch()
    try:
        receipt = bridge.submit_batch(
            {"cif_text": "scene", "view": {"viewport_px": [800, 450]}},
            presets=["mint", "pastel"], pse=b"session", name="batch-cancel",
            width=800, height=0, samples=16, keep_colors=True, save_blend=False,
        )
        first, second = receipt["runs"]
        wait_for(bridge, first["request_id"], "rendering")
        bridge.cancel(first["request_id"])
        wait_for(bridge, first["request_id"], "cancelled")
        wait_for(bridge, second["request_id"], "rendering")
    finally:
        hold.unlink()
    wait_for(bridge, second["request_id"], "completed")
    assert bridge.status(first["request_id"])["status"] == "cancelled"
    assert not (Path(first["directory"]) / "render.png").exists()


@pytest.mark.parametrize("job_id", ["original-job", None])
def test_unconfirmed_cluster_cancellation_stays_recoverable(bridge, monkeypatch, job_id):
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "_launch", lambda *args: None)
        request_id = submit(bridge)
    directory = Path(bridge.status(request_id)["directory"])
    saved = json.loads((directory / "state.json").read_text())
    saved.update(status="recovering" if job_id else "submitting")
    if job_id:
        saved["job_id"] = job_id
        # A confirmed job always has the receipt the client wrote before uploading.
        write_json(directory / "render.cluster.json", {"profile": "test", "request_id": "r" * 32,
                                                       "output": str(directory / "render.png"), "job_id": job_id})
    write_json(directory / "state.json", saved)
    (directory / "cancel.request").touch()
    offline = Path(bridge.config.render_home) / "offline"
    offline.touch()
    # Fast-forward only the worker's retry clock; exercise its real exception
    # and receipt paths without a 30-second disconnected-network wait.
    script = '''
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('worker', sys.argv[1])
worker = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(Path(sys.argv[1]).parent))
spec.loader.exec_module(worker)
clock = [0.]
worker.time.monotonic = lambda: clock[0]
worker.time.sleep = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
worker.run(Path(sys.argv[2]), recover=True)
'''
    subprocess.run([sys.executable, '-c', script,
                    str(Path(adapter.__file__).with_name('worker.py')), str(directory)],
                   cwd=directory, env=bridge._environment(bridge.config), check=True, timeout=10)
    state = bridge.status(request_id)
    assert "submit" not in calls(bridge)
    if not job_id:
        # No receipt means the client never reached its upload: nothing exists to cancel.
        assert state["status"] == "cancelled"
        return
    assert state["status"] == "needs_attention"
    assert state["job_id"] == job_id
    assert "may still be running" in state["error"]
    offline.unlink()
    bridge.result(request_id)
    wait_for(bridge, request_id, "cancelled")
    assert f"POST /v1/jobs/{job_id}/cancel" in calls(bridge)


@pytest.mark.parametrize("cluster_home", [None, "/lab/profiles"])
def test_lab_runs_keep_the_renderer_home_and_find_profiles_apart(tmp_path, cluster_home):
    """Presets resolve from render_home for both backends; profiles from cluster_home."""
    cfg = BlenderConfig.from_dict({"render_python": sys.executable, "render_home": str(tmp_path / "runtime"),
                                   "cluster_home": cluster_home, "backend": "cluster"})
    env = BlenderBridge._environment(cfg)
    assert env["MOL_BLENDER_HOME"] == str(tmp_path / "runtime")
    assert env["MOL_BLENDER_CLUSTER_HOME"] == (cluster_home or str(Path.home() / ".config/mol-blender"))
    assert BlenderBridge._environment(cfg.using("local"))["MOL_BLENDER_HOME"] == env["MOL_BLENDER_HOME"]


def test_custom_renderer_home_survives_worker_handoff(fake_repo, tmp_path, monkeypatch):
    custom = tmp_path / "custom-runtime"
    custom.mkdir()
    write_json(custom / "config.json", {"local_python": sys.executable})
    monkeypatch.setenv("MOL_BLENDER_HOME", str(custom))
    cfg = BlenderConfig.from_dict({})
    assert cfg.render_python == sys.executable
    assert cfg.render_home == str(custom)
    env = BlenderBridge._environment(cfg)
    result = subprocess.run([sys.executable, '-c',
        'from mol_blender.config import config_root; print(config_root())'],
        env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == str(custom)


def test_stopped_local_worker_needs_attention(local_bridge):
    bridge = local_bridge
    hold = Path(bridge.config.render_home) / "hold_render"
    hold.touch()
    try:
        request_id = submit(bridge)
        wait_for(bridge, request_id, "rendering")
        stop_helpers(bridge)
    finally:
        hold.unlink()
    state = bridge.status(request_id)
    assert state["status"] == "needs_attention"
    assert "stopped before finishing" in state["error"]


def test_legacy_profile_setting_selects_the_cluster_profile(tmp_path):
    cfg = BlenderConfig.from_dict({"profile": "lab-2", "render_python": sys.executable})
    assert cfg.cluster_profile == "lab-2"
    assert cfg.backend == "local"
    assert cfg.using("gpu").backend == cfg.using("lab").backend == "cluster"
    assert cfg.using("gpu").cluster_profile == "lab-2"


@pytest.mark.parametrize(
    "settings",
    [
        {"backend_root": "/old/backend"},
        {"backend_python": "/old/python"},
        {"backend": "remote"},
        {"render_python": "/missing/python"},
        {"cluster_profile": "../escape"},
        {"wait_seconds": 1},
    ],
)
def test_unknown_or_invalid_settings_are_rejected(settings):
    with pytest.raises(ValueError) as error:
        BlenderConfig.from_dict({"render_python": sys.executable, **settings})
    if "backend_root" in settings or "backend_python" in settings:
        assert "Unknown Blender settings" in str(error.value)


@pytest.fixture
def project_bridge(bridge, tmp_path):
    bridge._config = replace(bridge.config, project_directory=str(tmp_path / "demo"))
    return bridge


def project_submit(bridge, *, batch=False, **kwargs):
    payload = kwargs.pop(
        "payload",
        {
            "cif_text": "frozen scene",
            "view": {"viewport_px": [800, 450]},
            "provenance": {"enabled_objects": ["5GGS"]},
        },
    )
    options = dict(
        width=800,
        height=0,
        samples=16,
        keep_colors=True,
        save_blend=True,
        pse=b"frozen project session",
    )
    options.update(kwargs)
    if batch:
        return bridge.submit_batch(
            payload, presets=["mint_sky_assembly", "pastel_duplex_rounded"], **options
        )
    return bridge.submit(payload, preset="mint_sky_assembly", **options)


@pytest.mark.parametrize("batch", [False, True])
def test_project_layout_and_results_survive_restart(project_bridge, batch):
    bridge = project_bridge
    receipt = project_submit(bridge, batch=batch, view="Binding Site")
    complete = wait_for(bridge, receipt["request_id"], "completed")
    root = Path(bridge.config.project_directory) / "figures/5GGS/binding-site"
    assert list(root.rglob("scene.pse")) == [root / "scene.pse"]
    assert list(root.rglob("scene.bundle.json")) == [root / "scene.bundle.json"]
    assert (root / "scene.pse").read_bytes() == b"frozen project session"
    assert receipt["protein"] == "5GGS"
    assert receipt["view"] == "binding-site"
    assert complete["outputs"]["scene.pse"] == str(root / "scene.pse")
    restarted = BlenderBridge()
    restarted._config = bridge.config
    recent = restarted.recent()["renders"]
    assert len(recent) == (2 if batch else 1)
    for run in recent:
        path = root / "renders" / run["preset"]
        assert run["directory"] == str(path)
        assert run["name"] == f"5ggs--binding-site--{run['preset']}"
        request = json.loads((path / "request.json").read_text())
        assert request["scene_path"] == str(root / "scene.bundle.json")
        state, png = restarted.result(run["name"])
        assert png.startswith(b"\x89PNG")
        assert state["protein"] == "5GGS"
    assert restarted.status("5ggs--binding-site")["status"] == "completed"


def test_project_views_are_versioned_without_overwriting(project_bridge, monkeypatch):
    bridge = project_bridge
    monkeypatch.setattr(bridge, "_launch", lambda *args: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda pse: project_submit(bridge, pse=pse),
                (b"first orientation", b"second orientation"),
            )
        )
    assert {r["view"] for r in receipts} == {"overview", "overview-2"}
    assert {Path(r["pse_path"]).read_bytes() for r in receipts} == {
        b"first orientation",
        b"second orientation",
    }
    other = project_submit(bridge, protein="1ABC")
    assert other["view"] == "overview"
    assert other["name"] == "1abc--overview--mint_sky_assembly"
    assert bridge.recent()["total"] == 3


def test_project_keeps_legacy_jobs_discoverable_and_recoverable(bridge, tmp_path):
    old = submit(bridge, name="clever-swan")
    wait_for(bridge, old, "completed")
    bridge._config = replace(bridge.config, project_directory=str(tmp_path / "demo"))
    new = project_submit(bridge)
    wait_for(bridge, new["request_id"], "completed")
    restarted = BlenderBridge()
    restarted._config = bridge.config
    assert restarted.recent()["total"] == 2
    assert restarted.result("clever-swan")[1].startswith(b"\x89PNG")
    assert restarted.result(new["name"])[1].startswith(b"\x89PNG")


def test_project_recovery_uses_the_original_children(project_bridge):
    bridge = project_bridge
    offline = Path(bridge.config.render_home) / "offline"
    offline.touch()
    receipt = project_submit(bridge, batch=True)
    wait_for_runs(bridge, receipt["name"], "recovering")
    stop_helpers(bridge)
    offline.unlink()
    bridge.result(receipt["name"])
    wait_for(bridge, receipt["name"], "completed")
    assert calls(bridge).count("submit") == 2


@pytest.mark.parametrize(
    "options",
    [
        {"protein": "../escape"},
        {"protein": "/tmp/escape"},
        {"protein": ""},
        {"view": "../escape"},
        {"name": "overview", "view": "binding-site"},
        {"payload": {"provenance": {"enabled_objects": ["A", "B"]}}},
    ],
)
def test_project_rejects_invalid_or_ambiguous_names_before_writing(
    project_bridge, options
):
    bridge = project_bridge
    with pytest.raises(ValueError):
        project_submit(bridge, **options)
    assert not Path(bridge.config.project_directory).exists()
    assert not bridge._processes


def test_project_does_not_write_through_a_protein_symlink(project_bridge, tmp_path):
    bridge = project_bridge
    figures = Path(bridge.config.project_directory) / "figures"
    figures.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (figures / "5GGS").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        project_submit(bridge)
    assert list(elsewhere.iterdir()) == []


def test_project_mcp_tool_passes_names_and_resolves_preset_aliases(
    preset_backend, monkeypatch, tmp_path
):
    from co_pymol.server import create_server
    from co_pymol.scene.session import pymol_session

    bridge = preset_backend
    bridge._config = replace(bridge.config, project_directory=str(tmp_path / "demo"))
    monkeypatch.setattr("co_pymol.tools.blender.BlenderBridge", lambda: bridge)
    monkeypatch.setattr(
        "co_pymol.tools.blender.capture_scene",
        lambda *args: {"provenance": {"enabled_objects": ["A", "B"]}},
    )
    monkeypatch.setattr("co_pymol.tools.blender.capture_pse", lambda *args: b"session")
    monkeypatch.setattr(pymol_session, "_cmd", object())

    async def check():
        result = await create_server().call_tool(
            "mol_blender",
            {
                "preset": "mint",
                "protein": "5GGS",
                "view": "Binding Site",
            },
        )
        blocks = result[0] if isinstance(result, tuple) else result
        return json.loads(next(item.text for item in blocks if item.type == "text"))

    receipt = asyncio.run(check())
    assert receipt["name"] == "5ggs--binding-site--mint_sky_assembly"
    assert receipt["next_action"] == "return_to_user"
    wait_for(bridge, receipt["request_id"], "completed")


@pytest.mark.parametrize("backend", ["local", "cluster"])
def test_completion_ignores_existing_desktop_alert_preferences(
    bridge, tmp_path, backend
):
    bridge._config = replace(bridge.config, backend=backend)
    app = tmp_path / "Blender Renders.app"
    binary = app / "Contents/MacOS/RenderNotifier"
    binary.parent.mkdir(parents=True)
    record = tmp_path / "alert-sent"
    binary.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"Path({str(record)!r}).touch()\n"
    )
    binary.chmod(0o700)
    preferences = Path.home() / ".config/co-pymol/notifications.json"
    preferences.parent.mkdir(parents=True)
    write_json(preferences, {"enabled": True, "app": str(app)})

    request_id = submit(bridge)
    for process in bridge._processes:
        process.wait(timeout=5)
    state = bridge.status(request_id)
    assert state["status"] == "completed"
    assert Path(state["png_path"]).is_file()
    saved = json.loads((Path(state["directory"]) / "state.json").read_text())
    assert "notification" not in saved
    assert "notification" not in state
    bridge.result(request_id)
    assert not record.exists()


def saved_session(path: Path) -> Path:
    pymol2 = pytest.importorskip("pymol2")
    with pymol2.PyMOL() as instance:
        instance.cmd.fragment("ala", "one")
        instance.cmd.viewport(1200, 500)
        instance.cmd.save(str(path))
    return path


def test_cli_renders_saved_session_without_live_pymol(
    preset_backend, tmp_path, monkeypatch, capsys
):
    from co_pymol import cli
    from co_pymol.scene import capture as pymol_blender

    session = saved_session(tmp_path / "figure.pse")
    exported = {}

    def capture(cmd, selection, state, *, all_states=False):
        exported.update(
            objects=cmd.get_names("objects"),
            viewport=cmd.get_viewport(),
            selection=selection,
        )
        return {"cif_text": "from pse", "provenance": {"enabled_objects": ["one"]}}

    monkeypatch.setattr(pymol_blender, "capture_scene", capture)
    config = tmp_path / "blender.json"
    code = cli.main(
        ["render", str(session), "--preset", "mint", "--name", "fig", "--wait"]
        + ["--selection", "chain A", "--config", str(config)]
    )
    receipt = json.loads(capsys.readouterr().out)
    assert code == 0
    assert exported == {
        "objects": ["one"],
        "viewport": (1200, 500),
        "selection": "chain A",
    }
    assert receipt["status"] == "completed"
    assert receipt["name"] == "fig"
    assert receipt["preset"] == "mint_sky_assembly"
    assert Path(receipt["png_path"]).is_file()
    # The user's file is the snapshot: saved byte for byte beside the render.
    assert Path(receipt["outputs"]["scene.pse"]).read_bytes() == session.read_bytes()

    cli.main(["renders", "--config", str(config)])
    listed = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in listed["renders"]] == ["fig"]


def test_cli_render_batches_several_presets(
    preset_backend, tmp_path, monkeypatch, capsys
):
    from co_pymol import cli
    from co_pymol.scene import capture as pymol_blender

    session = saved_session(tmp_path / "figure.pse")
    monkeypatch.setattr(
        pymol_blender, "capture_scene", lambda *a: {"cif_text": "from pse"}
    )
    code = cli.main(
        ["render", str(session), "--all-presets", "--wait"]
        + ["--config", str(tmp_path / "blender.json")]
    )
    receipt = json.loads(capsys.readouterr().out)
    assert code == 0
    assert receipt["kind"] == "batch"
    assert receipt["counts"] == {"completed": 2}


def test_cli_render_rejects_non_session_files(tmp_path, capsys):
    from co_pymol import cli

    structure = tmp_path / "model.cif"
    structure.write_text("data_x\n")
    assert cli.main(["render", str(structure)]) == 1
    assert "not a saved PyMOL session" in capsys.readouterr().err
