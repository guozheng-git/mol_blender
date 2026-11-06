"""Structure Studio lab rendering: shared profiles, submit, fetch later, device reporting.

Every subprocess is replaced: no local render, no PyMOL, no network.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins/structure-studio/skills/structure-studio/scripts"))

import studio  # noqa: E402
import studio_env  # noqa: E402

STUDIO_HOME = "/studio/cache/blender-render"


class Recorder:
    """Stands in for subprocess.run; answers by the blender-render subcommand."""

    def __init__(self, replies, already_open=False):
        self.calls = []
        self.replies = {"cluster connect": {"status": "connected", "tunnel_reused": already_open},
                        "cluster disconnect": {"status": "disconnected"}, **replies}

    def commands(self):
        return [" ".join(command[1:3]) for command, _ in self.calls]

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs.get("env") or {}))
        key = command[1] if command[1] != "cluster" else "cluster " + command[2]
        reply = self.replies[key]
        reply = reply(command) if callable(reply) else reply
        code, out = reply if isinstance(reply, tuple) else (0, reply)
        return type("Result", (), {"returncode": code, "stdout": out if isinstance(out, str) else json.dumps(out),
                                   "stderr": out if code else ""})()


@pytest.fixture
def studio_installed(tmp_path, monkeypatch):
    executable = tmp_path / "render/bin/blender-render"
    executable.parent.mkdir(parents=True)
    executable.write_text("")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BLENDER_RENDER_HOME", "/shell/.runtime")  # e.g. a checkout's activate script
    monkeypatch.setattr(studio_env, "state", lambda: {"blender_render": str(executable), "blender_render_home": STUDIO_HOME})
    monkeypatch.setattr(studio_env, "_render_ready", lambda: True)
    monkeypatch.setattr(studio_env, "pymol_python", lambda: Path("/studio/pymol/bin/python"))
    monkeypatch.setattr(studio_env, "doctor", lambda: {"gpu_likely": False})  # A CPU-only laptop.
    return tmp_path


def lab_profile(home, name="lab"):
    folder = Path(home) / "home/.config/blender-render/clusters"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (name + ".json")).write_text("{}")


def structure(tmp_path):
    source = tmp_path / "1stp.cif"
    source.write_text((ROOT / "examples/1stp.cif").read_text())
    return source


def test_cluster_env_uses_the_terminals_folder_and_local_renders_keep_studios(studio_installed):
    _, local = studio_env.renderer_env()
    assert local["BLENDER_RENDER_HOME"] == STUDIO_HOME
    _, lab = studio_env.cluster_env()
    assert "BLENDER_RENDER_HOME" not in lab  # Neither Studio's nor one inherited from a shell.
    assert studio_env.shared_cluster_home() == studio_installed / "home/.config/blender-render"


def test_profiles_are_read_from_the_shared_folder(studio_installed):
    assert studio_env.cluster_profiles() == []
    lab_profile(studio_installed)
    lab_profile(studio_installed, "other")
    assert studio_env.cluster_profiles() == ["lab", "other"]


def test_lab_render_submits_without_waiting_or_cpu_caps(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    receipt = str(studio_installed / "out/1stp-mint_sky_assembly.cluster.json")
    run = Recorder({"cluster submit": {"job_id": "a" * 32, "slurm_id": "22908125", "status": "queued", "receipt": receipt}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    args = studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab", "--out-dir", str(studio_installed / "out")])
    result = studio.cmd_render(args)

    command, env = next(call for call in run.calls if call[0][1:3] == ["cluster", "submit"])
    assert command[1:4] == ["cluster", "submit", command[3]] and command[command.index("--profile") + 1] == "lab"
    assert command[command.index("--pymol-python") + 1] == "/studio/pymol/bin/python"
    assert "--wait" not in command and "--timeout" not in command and "--samples" not in command  # No CPU caps.
    assert "BLENDER_RENDER_HOME" not in env
    assert result["status"] == "queued" and result["slurm_id"] == "22908125" and "lab-fetch" in result["next"][0]
    pending = json.loads(Path(result["pending_file"]).read_text())
    assert pending["receipt"] == receipt and pending["profile"] == "lab"


def test_local_render_is_unchanged(studio_installed, monkeypatch):
    run = Recorder({"render": {"status": "completed", "outputs": {"render.png": "x.png"}}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    args = studio.parser().parse_args(["render", str(structure(studio_installed)), "--out-dir", str(studio_installed / "out")])
    studio.cmd_render(args)
    command, env = run.calls[0]
    assert command[1] == "render" and env["BLENDER_RENDER_HOME"] == STUDIO_HOME
    assert "--samples" in command  # This laptop still gets its CPU caps.


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
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"cluster status": {"status": "queued", "job_id": "a" * 32}}))
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["status"] == "queued" and result["next"] and result["error"] is None


def test_lab_fetch_failed_job_stops_polling(studio_installed, monkeypatch):
    path, _ = pending_file(studio_installed)
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"cluster status": {"status": "failed", "error": "GPU missing"}}))
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["status"] == "failed" and result["error"] == "GPU missing" and result["next"] == []


def test_lab_fetch_completed_maps_outputs_and_reports_the_gpu(studio_installed, monkeypatch):
    path, output = pending_file(studio_installed)
    manifest = output.with_suffix(".render") / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"cycles_device": {"device": "GPU", "compute_type": "CUDA",
                                                      "devices": ["NVIDIA A100 80GB PCIe"]}}))
    fetched = {"status": "completed", "job_id": "a" * 32,
               "outputs": {"render.png": str(output), "manifest.json": str(manifest)}}
    run = Recorder({"cluster status": {"status": "completed"}, "cluster fetch": fetched})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["device"]["summary"] == "Rendered on lab GPU (CUDA)"
    assert result["outputs"]["render.png"] == str(output) and Path(result["result_file"]).is_file()
    assert all("BLENDER_RENDER_HOME" not in env for _, env in run.calls)


def test_lab_fetch_without_outputs_does_not_crash(studio_installed, monkeypatch):
    path, _ = pending_file(studio_installed)
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"cluster status": {"status": "completed"},
                                                            "cluster fetch": {"status": "running"}}))
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["status"] == "running" and "try again" in result["message"]


def test_server_rejection_says_the_lab_server_may_be_too_old(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    error = (1, "blender-render: HTTP 400: Style JSON validation failed: materials.ptm: Additional properties")
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"cluster submit": error}))
    args = studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab", "--preset", "northern_nights",
                                       "--out-dir", str(studio_installed / "out")])
    with pytest.raises(RuntimeError, match="(?s)may need a newer lab server.*materials.ptm"):
        studio.cmd_render(args)


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
    assert command[1:] == ["cluster", "connection", "--profile", "lab"] and "BLENDER_RENDER_HOME" not in env
    assert result == {"profile": "lab", "connection": None}
    assert studio.cmd_lab(studio.parser().parse_args(["lab"]))["profiles"] == []


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
    run = Recorder({"cluster submit": {"job_id": "a" * 32, "receipt": str(out / "x.cluster.json")}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_render(studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab", "--out-dir", str(out)]))
    words = shlex.split(result["next"][0])
    assert words[2] == "lab-fetch" and words[3] == result["pending_file"] and " " in words[3]


def test_unreachable_lab_message_fits_a_fetch():
    message = studio.lab_error("blender-render: Cluster connection failed; recover the existing request before submitting again")
    assert "lab connect" in message and "submitting again" not in message and "still fetch" in message


def test_render_record_keeps_the_device(tmp_path):
    from PIL import Image
    image = tmp_path / "r.png"
    Image.new("RGB", (4, 3)).save(image)
    device = {"device": "GPU", "compute_type": "CUDA", "summary": "Rendered on lab GPU (CUDA)"}
    doc = studio.artifact_files.record({"outputs": {"render.png": str(image)}, "preset": "p", "device": device}, {"image": "id"})
    assert doc["device"] == device


def test_lab_render_opens_and_closes_its_own_connection(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    run = Recorder({"cluster submit": {"job_id": "a" * 32, "receipt": "r.cluster.json"}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_render(studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab",
                                                            "--out-dir", str(studio_installed / "out")]))
    assert run.commands() == ["cluster connect", "cluster submit", "cluster disconnect"]
    assert result["connection"] == "closed"


def test_a_connection_opened_elsewhere_is_left_open(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    run = Recorder({"cluster submit": {"job_id": "a" * 32, "receipt": "r.cluster.json"}}, already_open=True)
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_render(studio.parser().parse_args(["render", str(structure(studio_installed)), "--lab",
                                                            "--out-dir", str(studio_installed / "out")]))
    assert "cluster disconnect" not in run.commands() and result["connection"].startswith("left open")


def test_lab_fetch_waits_then_fetches_and_closes(studio_installed, monkeypatch):
    path, output = pending_file(studio_installed)
    states = iter(["queued", "running", "completed"])
    monkeypatch.setattr(studio.time, "sleep", lambda seconds: None)
    run = Recorder({"cluster status": lambda command: {"status": next(states)},
                    "cluster fetch": {"status": "completed", "job_id": "a" * 32, "outputs": {"render.png": str(output)}}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path), "--wait", "600"]))
    assert run.commands() == ["cluster connect", "cluster status", "cluster status", "cluster status",
                              "cluster fetch", "cluster disconnect"]
    assert result["outputs"]["render.png"] == str(output) and result["connection"] == "closed"


def test_lab_fetch_closes_even_when_still_queued(studio_installed, monkeypatch):
    path, _ = pending_file(studio_installed)
    run = Recorder({"cluster status": {"status": "queued"}})
    monkeypatch.setattr(studio.subprocess, "run", run)
    result = studio.cmd_lab_fetch(studio.parser().parse_args(["lab-fetch", str(path)]))
    assert result["status"] == "queued" and run.commands()[-1] == "cluster disconnect"


def test_first_use_without_saved_settings_says_what_to_ask(studio_installed, monkeypatch):
    lab_profile(studio_installed)
    error = (1, "blender-render: First connection needs: --ssh-host, --node, --remote-executable, --server-config")
    monkeypatch.setattr(studio.subprocess, "run", Recorder({"cluster connect": error}))
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
    site = home / "render/lib/python3.11/site-packages/blender_render"
    site.mkdir(parents=True)
    (home / "render/bin").mkdir(parents=True)
    for name in ("python", "blender-render"):
        (home / "render/bin" / name).write_text("")
    monkeypatch.setenv("STRUCTURE_STUDIO_HOME", str(home))
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    monkeypatch.setattr(studio_env, "WHEELS", wheels)
    (home / "state.json").write_text(json.dumps({"render_python": str(home / "render/bin/python"),
                                                 "blender_render": str(home / "render/bin/blender-render")}))

    def set_identities(installed, bundled, wheel_bytes=b""):
        (site / "engine-manifest.json").write_text(json.dumps({"engine_build": installed}))
        for old in wheels.glob("*.whl"):
            old.unlink()
        with zipfile.ZipFile(wheels / "pymol_plugin_bridge-0-py3-none-any.whl", "w") as archive:
            archive.writestr("blender_render/engine-manifest.json", json.dumps({"engine_build": bundled}))
            archive.writestr("padding", wheel_bytes)  # Different bytes, same code.
        return site
    return set_identities


def test_a_rebuilt_wheel_with_the_same_code_is_ready(renderer_layout):
    renderer_layout("same", "same", wheel_bytes=b"rebuild 2")
    assert studio_env._render_ready() and studio_env.doctor()["renderer_update"] == "none"


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
    assert studio_env._render_ready()


def test_changed_dependencies_stop_instead_of_rendering_with_old_code(renderer_layout, monkeypatch):
    site = renderer_layout("old", "new")
    (site / "render-requirements.txt").write_text("bpy==5.0.1 \\\n    --hash=sha256:x\n")
    monkeypatch.setattr(studio_env, "REQUIREMENTS", site)
    monkeypatch.setattr(studio_env, "uv", lambda: Path("uv"))
    monkeypatch.setattr(studio_env, "_run", lambda command, **k: type("R", (), {"stdout": "bpy==4.2.0\n"})())
    with pytest.raises(studio_env.SetupError, match="pinned dependencies.*setup --background"):
        studio_env.renderer_env()


def test_requirement_pins_are_parsed_from_hash_locked_files():
    text = "numpy==1.26.4 \\\n    --hash=sha256:abc\nMolecular_Nodes==4.5.10 ; python_version >= '3.11'\n# comment\n"
    assert studio_env._pins(text) == {"numpy": "1.26.4", "molecular-nodes": "4.5.10"}
