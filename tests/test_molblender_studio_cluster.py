"""molblender-studio lab rendering: shared profiles, submit, fetch later, device reporting.

Every subprocess is replaced: no local render, no PyMOL, no network.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins/molblender_studio/skills/molblender-studio/scripts"))

import studio  # noqa: E402
import studio_env  # noqa: E402

STUDIO_HOME = "/studio/cache/mol-blender"


class Recorder:
    """Stands in for subprocess.run; answers by the mol-blender subcommand.

    Studio launches `mol-blender job …` for renders and every lab step; the job
    command itself connects once and closes only what it opened (tests/test_jobs.py).
    """

    def __init__(self, replies):
        self.calls = []
        self.replies = replies

    def commands(self):
        return [" ".join(command[1:3]) for command, _ in self.calls]

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs.get("env") or {}))
        key = command[1] if command[1] not in ("cluster", "job") else command[1] + " " + command[2]
        reply = self.replies[key]
        reply = reply(command) if callable(reply) else reply
        code, out = reply if isinstance(reply, tuple) else (0, reply)
        return type("Result", (), {"returncode": code, "stdout": out if isinstance(out, str) else json.dumps(out),
                                   "stderr": out if code else ""})()


@pytest.fixture
def studio_installed(tmp_path, monkeypatch):
    executable = tmp_path / "render/bin/mol-blender"
    executable.parent.mkdir(parents=True)
    executable.write_text("")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MOL_BLENDER_HOME", "/shell/.runtime")  # e.g. a checkout's activate script
    monkeypatch.setattr(studio_env, "state", lambda: {"mol_blender": str(executable), "mol_blender_home": STUDIO_HOME,
                                                     "external_renderer": True})
    monkeypatch.setattr(studio_env, "pymol_python", lambda: Path("/studio/pymol/bin/python"))
    monkeypatch.setattr(studio_env, "doctor", lambda: {"gpu_likely": False})  # A CPU-only laptop.
    return tmp_path


def lab_profile(home, name="lab"):
    folder = Path(home) / "home/.config/mol-blender/clusters"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + ".json")).write_text("{}")


def structure(tmp_path):
    source = tmp_path / "1stp.cif"
    source.write_text((ROOT / "examples/1stp.cif").read_text())
    return source


def lab_env_ok(env, home):
    """Lab commands: Studio's renderer home (same presets as local), the terminal's lab profiles."""
    return (env["MOL_BLENDER_HOME"] == STUDIO_HOME
            and env["MOL_BLENDER_CLUSTER_HOME"] == str(home / "home/.config/mol-blender"))


def test_cluster_env_uses_the_terminals_profiles_and_studios_renderer_home(studio_installed):
    _, local = studio_env.renderer_env()
    assert local["MOL_BLENDER_HOME"] == STUDIO_HOME
    _, lab = studio_env.cluster_env()
    # Not one inherited from a shell (/shell/.runtime): profiles never depend on how the app was launched.
    assert lab_env_ok(lab, studio_installed)
    assert studio_env.shared_cluster_home() == studio_installed / "home/.config/mol-blender"


def test_profiles_are_read_from_the_shared_folder(studio_installed):
    assert studio_env.cluster_profiles() == []
    lab_profile(studio_installed)
    lab_profile(studio_installed, "other")
    assert studio_env.cluster_profiles() == ["lab", "other"]


def test_lab_render_submits_without_waiting_or_cpu_caps(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    receipt = str(studio_installed / "out/1stp-mint_sky_assembly.cluster.json")
    run = Recorder({"job submit": {"job_id": "a" * 32, "slurm_id": "22908125", "status": "queued", "receipt": receipt,
                                   "connection": "closed"}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    args = studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab", "--out-dir", str(studio_installed / "out")])
    result = studio.cmd_render(args)

    assert run.commands() == ["job submit"]  # Connecting is the job command's business, not Studio's.
    command, env = run.calls[0]
    assert command[command.index("--backend") + 1] == "lab" and command[command.index("--profile") + 1] == "lab"
    assert command[command.index("--pymol-python") + 1] == "/studio/pymol/bin/python"
    assert "--keep-connected" not in command
    assert "--wait" not in command and "--timeout" not in command and "--samples" not in command  # No CPU caps.
    assert lab_env_ok(env, studio_installed)
    assert result["status"] == "queued" and result["slurm_id"] == "22908125" and "lab-fetch" in result["next"][0]
    assert result["connection"] == "closed"
    pending = json.loads(Path(result["pending_file"]).read_text())
    assert pending["receipt"] == receipt and pending["profile"] == "lab"


def test_keep_connected_is_passed_to_the_job_command(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    run = Recorder({"job submit": {"job_id": "a" * 32, "receipt": "r.cluster.json", "connection": "kept open"}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_render(studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab",
                                                            "--keep-connected", "--out-dir", str(studio_installed / "out")]))
    assert "--keep-connected" in run.calls[0][0] and result["connection"] == "kept open"


def test_local_render_uses_the_same_job_command(studio_installed, monkeypatch):
    run = Recorder({"job submit": {"status": "completed", "outputs": {"render.png": "x.png"}}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    args = studio.parser().parse_args(["render", str(structure(studio_installed)), "--out-dir", str(studio_installed / "out")])
    studio.cmd_render(args)
    command, env = run.calls[0]
    assert command[1:3] == ["job", "submit"] and command[command.index("--backend") + 1] == "local"
    assert env["MOL_BLENDER_HOME"] == STUDIO_HOME
    assert "--samples" in command and "--timeout" in command  # This laptop still gets its CPU caps.


def test_lab_render_without_a_connection_explains_how_to_connect(studio_installed, monkeypatch):
    monkeypatch.setattr(studio.subprocess, "run", lambda *a, **k: pytest.fail("nothing should run"))
    args = studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab"])
    with pytest.raises(ValueError, match="No lab connection.*lab connect"):
        studio.cmd_render(args)


def pending_file(tmp_path):
    output = tmp_path / "out/1stp.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    context = {"preset": "mint_sky_assembly", "cpu_adjustments": {}, "input": "1stp.cif", "session": None,
               "animation": None, "output": str(output), "started": 0, "profile": "lab",
               "receipt": str(output.with_suffix(".cluster.json")), "job_id": "a" * 32,
               "args": {"workspace": "", "url": "", "job": "", "label": "", "note": "", "scene": "", "view": ""}}
    path = tmp_path / "out/1stp.lab.json"
    path.write_text(json.dumps(context))
    return path, output


def test_lab_fetch_while_queued_returns_status_and_keeps_waiting(studio_installed, monkeypatch):
    path, _ = pending_file(studio_installed)
    run = Recorder({"job fetch": {"status": "queued", "job_id": "a" * 32, "connection": "closed"}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["status"] == "queued" and result["next"] and result["error"] is None
    assert result["connection"] == "closed"
    command = run.calls[0][0]
    assert command[3] == str(path.with_suffix("").with_suffix(".cluster.json"))  # Observed by its receipt.
    assert command[command.index("--wait") + 1] == "0.0"


@pytest.mark.parametrize("state, error", [("failed", "GPU missing"), ("prepared", "upload never arrived")])
def test_lab_fetch_stops_on_states_polling_cannot_change(studio_installed, monkeypatch, state, error):
    path, _ = pending_file(studio_installed)
    reply = {"status": state, "error" if state == "failed" else "message": error}
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"job fetch": reply}))
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["status"] == state and error in result["error"] and result["next"] == []


def test_lab_fetch_keeps_an_unconfirmed_slurm_submission_recoverable(studio_installed, monkeypatch):
    path, _ = pending_file(studio_installed)
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"job fetch": {"status": "submission_unknown", "job_id": "a" * 32}}))
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["status"] == "submission_unknown" and "lab-fetch" in result["next"][0]  # Check again later.


def test_lab_fetch_completed_maps_outputs_and_reports_the_gpu(studio_installed, monkeypatch):
    path, output = pending_file(studio_installed)
    manifest = output.with_suffix(".render") / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"cycles_device": {"device": "GPU", "compute_type": "CUDA",
                                                      "devices": ["NVIDIA A100 80GB PCIe"]}}))
    fetched = {"status": "completed", "job_id": "a" * 32, "connection": "closed",
               "outputs": {"render.png": str(output), "manifest.json": str(manifest)}}
    run = Recorder({"job fetch": fetched})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path), "--wait", "600"]))
    assert run.commands() == ["job fetch"]  # Waiting, observing and fetching happen in one job command.
    command = run.calls[0][0]
    assert command[command.index("--wait") + 1] == "600.0" and command[command.index("--interval") + 1] == "15"
    assert result["device"]["summary"] == "Rendered on lab GPU (CUDA)" and result["connection"] == "closed"
    assert result["outputs"]["render.png"] == str(output) and Path(result["result_file"]).is_file()
    assert all(lab_env_ok(env, studio_installed) for _, env in run.calls)


def test_server_rejection_says_the_lab_server_may_be_too_old(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    error = (1, "mol-blender: HTTP 400: Style JSON validation failed: materials.ptm: Additional properties")
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"job submit": error}))
    args = studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab", "--preset", "northern_nights",
                                       "--out-dir", str(studio_installed / "out")])
    with pytest.raises(RuntimeError, match="(?s)may need a newer lab server.*materials.ptm"):
        studio.cmd_render(args)
    assert not list((studio_installed / "out").glob("*.lab.json"))


@pytest.mark.parametrize("failure", ["lost_response", "timeout", "invalid_response"])
def test_ambiguous_submission_is_discoverable_and_recovers_without_resubmitting(studio_installed, monkeypatch, failure):
    lab_profile(studio_installed)
    out = studio_installed / "out"
    receipts = []

    def submit(command):
        output = Path(command[command.index("-o") + 1])
        receipt = output.with_suffix(".cluster.json")
        # This is exactly where Client.submit persists identity before POST /v1/jobs.
        context = json.loads(output.with_suffix(".lab.json").read_text())
        assert context["receipt"] == str(receipt)
        receipt.write_text(json.dumps({"profile": "lab", "request_id": "b" * 32, "output": str(output)}))
        receipts.append(receipt)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        if failure == "invalid_response":
            return "truncated JSON"
        return (1, "Cluster connection failed; recover the existing request before submitting again. "
                   "Recover with: mol-blender cluster status " + str(receipt))

    run = Recorder({"job submit": submit})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_render(studio.parser().parse_args(
        ["render", str(structure(studio_installed)), "--lab", "--out-dir", str(out)]))
    assert result["status"] == "submission_unknown" and "Do not submit again" in result["message"]
    assert result["receipt"] == str(receipts[0]) and "lab-fetch" in result["next"][0]
    pending = studio.cmd_lab_pending(studio.parser().parse_args(["lab-pending", "--out-dir", str(out)]))
    assert pending["pending"] == [result["pending_file"]]

    def recover(command):
        assert command[3] == str(receipts[0])  # The job command looks it up by its receipt.
        return {"status": "completed", "job_id": "a" * 32,
                "outputs": {"render.png": str(receipts[0].with_suffix(".png"))}}

    run.replies["job fetch"] = recover
    fetched = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", result["pending_file"]]))
    assert Path(fetched["result_file"]).is_file()
    assert run.commands().count("job submit") == 1
    assert studio.cmd_lab_pending(studio.parser().parse_args(["lab-pending", "--out-dir", str(out)]))["pending"] == []


def test_lab_animation_upload_uses_temporary_inputs_and_cleans_them(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    source = structure(studio_installed)
    monkeypatch.setattr(studio, "run_pymol_session", lambda command: {"session": str(source)})

    def prepare(args, source, recipe, staging):
        bundle, overrides = staging / "bundle.json", staging / "overrides.json"
        bundle.write_text('{"cif_text":"test"}')
        overrides.write_text('{}')
        return bundle, overrides, {"template": recipe}, {}

    monkeypatch.setattr(studio, "prepare_animation", prepare)
    inputs = []

    def submit(command):
        inputs.extend([Path(command[3]), Path(command[command.index("--overrides-json") + 1])])
        assert all(path.is_file() for path in inputs)
        return {"status": "queued", "receipt": "r.cluster.json", "job_id": "a" * 32}

    monkeypatch.setattr(studio.subprocess, "run", Recorder({"job submit": submit}))
    out = studio_installed / "out"
    studio.cmd_render(studio.parser().parse_args(
        ["render", str(source), "--lab", "--recipe", "rock", "--out-dir", str(out)]))
    assert all(not path.exists() for path in inputs)
    assert not list(out.glob("*.bundle.json")) and not list(out.glob("*.overrides.json"))


def test_a_failed_disconnect_is_reported_without_hiding_the_receipt(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    run = Recorder({"job submit": {"status": "queued", "receipt": "r.cluster.json", "job_id": "a" * 32,
                                   "connection": "disconnect failed: timed out"}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_render(studio.parser().parse_args(
        ["render", str(structure(studio_installed)), "--lab", "--out-dir", str(studio_installed / "out")]))
    assert result["receipt"] == "r.cluster.json" and Path(result["pending_file"]).is_file()
    assert "timed out" in result["connection"]


def test_pending_context_cannot_be_overwritten_by_another_render_or_failed_update(tmp_path, monkeypatch):
    path = tmp_path / "render.lab.json"
    context = {"receipt": "render.cluster.json", "profile": "lab"}
    studio.artifact_files.write_json(context, path, exclusive=True)
    with pytest.raises(FileExistsError):
        studio.artifact_files.write_json({"receipt": "other.cluster.json"}, path, exclusive=True)
    assert json.loads(path.read_text()) == context

    def fail_replace(*args):
        raise OSError("update failed")

    monkeypatch.setattr(studio.artifact_files.os, "replace", fail_replace)
    with pytest.raises(OSError, match="update failed"):
        studio.artifact_files.write_json({**context, "job_id": "a" * 32}, path)
    assert json.loads(path.read_text()) == context
    assert list(tmp_path.iterdir()) == [path]


def test_local_device_summary(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cycles_device": {"device": "GPU", "compute_type": "METAL"}}))
    assert studio.render_device({"outputs": {"manifest.json": str(manifest)}})["summary"] == "Rendered on this computer GPU (METAL)"
    assert studio.render_device({"outputs": {}}) is None


def test_lab_passthrough_uses_shared_profiles(studio_installed, monkeypatch):
    run = Recorder({"cluster connection": {"profile": "lab", "connection": None}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_lab(studio.parser().parse_args(["lab", "connection", "--profile", "lab"]))
    command, env = run.calls[0]
    assert command[1:] == ["cluster", "connection", "--profile", "lab"] and lab_env_ok(env, studio_installed)
    assert result == {"profile": "lab", "connection": None}
    assert studio.cmd_lab(studio.parser().parse_args(["lab"]))["profiles"] == []


@pytest.mark.parametrize("response, expected", [("", {}), ("[]", []), ('{"jobs": []}', {"jobs": []})])
def test_cluster_passthrough_and_internal_commands_share_response_handling(studio_installed, monkeypatch, response, expected):
    run = Recorder({"cluster list": response})
    monkeypatch.setattr(studio.subprocess, "run", run)
    assert studio.cmd_lab(studio.parser().parse_args(["lab", "list"])) == expected
    assert studio.run_cluster("list") == expected


def test_lab_profiles_can_be_listed_without_an_installed_renderer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    lab_profile(tmp_path)

    def no_runtime():
        pytest.fail("listing profiles must not resolve or install a renderer")

    monkeypatch.setattr(studio_env, "cluster_env", no_runtime)
    assert studio.cmd_lab(studio.parser().parse_args(["lab"]))["profiles"] == ["lab"]


def test_a_queued_lab_job_reserves_its_output_name(tmp_path):
    first = tmp_path / "1stp-mint_sky_assembly.png"
    first.with_suffix(".cluster.json").write_text("{}")  # Job 1 queued; its PNG doesn't exist yet.
    assert studio.unique(first) == tmp_path / "1stp-mint_sky_assembly-2.png"
    (tmp_path / "1stp-mint_sky_assembly-2.lab.json").write_text("{}")
    assert studio.unique(first) == tmp_path / "1stp-mint_sky_assembly-3.png"


def test_next_commands_quote_paths_with_spaces(studio_installed, monkeypatch):
    import shlex
    lab_profile(studio_installed)
    out = studio_installed / "Application Support" / "out"
    run = Recorder({"job submit": {"job_id": "a" * 32, "receipt": str(out / "x.cluster.json")}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_render(studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab", "--out-dir", str(out)]))
    words = shlex.split(result["next"][0])
    assert words[2] == "lab-fetch" and words[3] == result["pending_file"] and " " in words[3]


def test_unreachable_lab_message_fits_a_fetch():
    detail = "Cluster connection failed; recover the existing request before submitting again. Recover with: cluster status r.cluster.json"
    message = studio.lab_error(detail)
    assert "lab connect" in message and "still fetch" in message and detail in message


def test_render_record_keeps_the_device(tmp_path):
    from PIL import Image
    image = tmp_path / "r.png"
    Image.new("RGB", (4, 3)).save(image)
    device = {"device": "GPU", "compute_type": "CUDA", "summary": "Rendered on lab GPU (CUDA)"}
    doc = studio.artifact_files.record({"outputs": {"render.png": str(image)}, "preset": "p", "device": device}, {"image": "id"})
    assert doc["device"] == device


def test_first_use_without_saved_settings_says_what_to_ask(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    error = (1, "mol-blender: First connection needs: --ssh-host, --node, --remote-executable, --server-config")
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"job submit": error}))
    with pytest.raises(RuntimeError, match="SSH alias.*login node"):
        studio.cmd_render(studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab"]))


def test_lab_pending_lists_unfetched_renders(tmp_path):
    (tmp_path / "a.lab.json").write_text("{}")
    (tmp_path / "b.lab.json").write_text("{}")
    (tmp_path / "b.result.json").write_text("{}")  # Already fetched.
    result = studio.cmd_lab_pending(studio.parser().parse_args(["lab-pending", "--out-dir", str(tmp_path)]))
    assert result["pending"] == [str(tmp_path / "a.lab.json")] and "lab-fetch" in result["next"][0]


@pytest.fixture
def renderer_layout(tmp_path, monkeypatch):
    """A Studio home with an installed renderer and a plugin wheel, both with chosen identities."""
    import zipfile
    home = tmp_path / "studio-home"
    site = home / "render/lib/python3.11/site-packages/mol_blender"
    site.mkdir(parents=True)
    (home / "render/bin").mkdir(parents=True)
    for name in ("python", "mol-blender"):
        (home / "render/bin" / name).write_text("")
    monkeypatch.setenv("MOLBLENDER_STUDIO_HOME", str(home))
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    monkeypatch.setattr(studio_env, "WHEELS", wheels)
    (home / "state.json").write_text(json.dumps({"render_python": str(home / "render/bin/python"),
                                                 "mol_blender": str(home / "render/bin/mol-blender")}))

    def set_identities(installed, bundled, wheel_bytes=b""):
        (site / "engine-manifest.json").write_text(json.dumps({"engine_build": installed}))
        for old in wheels.glob("*.whl"):
            old.unlink()
        with zipfile.ZipFile(wheels / "pymol_plugin_bridge-0-py3-none-any.whl", "w") as archive:
            archive.writestr("mol_blender/engine-manifest.json", json.dumps({"engine_build": bundled}))
            archive.writestr("padding", wheel_bytes)  # Different bytes, same code.
        return site
    return set_identities


def test_a_rebuilt_wheel_with_the_same_code_is_ready(renderer_layout):
    renderer_layout("same", "same", wheel_bytes=b"rebuild 2")
    assert studio_env.doctor()["render_ready"] and studio_env.doctor()["renderer_update"] == "none"


@pytest.mark.parametrize("operation", [studio_env.doctor, studio_env.renderer_env])
def test_readiness_uses_one_consistent_snapshot(renderer_layout, monkeypatch, operation):
    from unittest.mock import Mock

    renderer_layout("same", "same")
    reads = []
    for name in ("state", "installed_identity", "bundled_identity"):
        reader = Mock(wraps=getattr(studio_env, name))
        monkeypatch.setattr(studio_env, name, reader)
        reads.append(reader)
    operation()
    assert [reader.call_count for reader in reads] == [1, 1, 1]


def test_new_renderer_code_is_refreshed_offline_before_use(renderer_layout, monkeypatch):
    site = renderer_layout("old", "new")
    assert studio_env.doctor()["renderer_update"] == "automatic"
    commands = []

    def run(command, **kwargs):
        commands.append([str(c) for c in command])
        if "freeze" in command:
            return type("R", (), {"stdout": ""})()
        if "install" in command:
            (site / "engine-manifest.json").write_text(json.dumps({"engine_build": "new"}))
        return type("R", (), {"stdout": ""})()

    monkeypatch.setattr(studio_env, "_run", run)
    monkeypatch.setattr(studio_env, "uv", lambda: Path("uv"))
    monkeypatch.setattr(studio_env, "_import_check", lambda *a: None)
    monkeypatch.setattr(studio_env, "REQUIREMENTS", site)  # No pins: dependencies trivially match.
    (site / "render-requirements.txt").write_text("")
    studio_env.renderer_env()
    install = next(c for c in commands if "install" in c)
    assert {"--offline", "--no-deps", "--reinstall"} <= set(install)
    assert studio_env.renderer_status()["render_ready"]


def test_changed_dependencies_stop_instead_of_rendering_with_old_code(renderer_layout, monkeypatch):
    site = renderer_layout("old", "new")
    (site / "render-requirements.txt").write_text("bpy==5.0.1 \\\n    --hash=sha256:x\n")
    monkeypatch.setattr(studio_env, "REQUIREMENTS", site)
    monkeypatch.setattr(studio_env, "uv", lambda: Path("uv"))
    monkeypatch.setattr(studio_env, "_run", lambda command, **k: type("R", (), {"stdout": "bpy==4.2.0\n"})())
    with pytest.raises(studio_env.SetupError, match="pinned dependencies.*setup --background"):
        studio_env.renderer_env()


@pytest.mark.parametrize("lab", [False, True])
def test_session_stills_render_the_bundle_exported_with_the_session(studio_installed, monkeypatch, lab):
    """The PyMOL run that saves the .pse also exports the payload; the renderer never reloads it."""
    if lab:
        lab_profile(studio_installed)
    source = structure(studio_installed)
    sessions = []

    def session(command):
        sessions.append(command)
        bundle = Path(command[command.index("--bundle") + 1])
        bundle.write_text('{"cif_text":"test"}')
        return {"session": str(studio_installed / "1stp-view.pse"), "bundle": str(bundle)}

    monkeypatch.setattr(studio, "run_pymol_session", session)
    rendered = []

    def render(command):
        rendered.append(command)
        assert Path(command[3]).is_file()  # Still present while the renderer reads it.
        png = str(studio_installed / "out/1stp-view-mint_sky_assembly.png")
        return ({"status": "queued", "receipt": png[:-4] + ".cluster.json", "job_id": "a" * 32} if lab
                else {"status": "completed", "outputs": {"render.png": png}})

    monkeypatch.setattr(studio.subprocess, "run", Recorder({"job submit": render}))
    args = ["render", str(source), "--fit", "--out-dir", str(studio_installed / "out")] + (["--lab"] if lab else [])
    studio.cmd_render(studio.parser().parse_args(args))
    command = rendered[0]
    assert Path(command[3]).name == "bundle.json"
    assert "--pymol-python" not in command
    assert "--bundle" in sessions[0]


def test_requirement_pins_are_parsed_from_hash_locked_files():
    text = "numpy==1.26.4 \\\n    --hash=sha256:abc\nMolecular_Nodes==4.5.10 ; python_version >= '3.11'\n# comment\n"
    assert studio_env._pins(text) == {"numpy": "1.26.4", "molecular-nodes": "4.5.10"}
