from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import blender_render.pymol_bridge_plugin as plugin


class FakeCmd:
    def dss(self, selection):
        assert selection == "(all)"
        return None

    def get_str(self, fmt, selection, state=-1):
        assert selection == "(all)"
        assert state == -1
        if fmt == "cif":
            return "\n".join(
                [
                    "data_test",
                    "loop_",
                    "_atom_site.group_PDB",
                    "_atom_site.id",
                    "ATOM 1",
                ]
            )
        if fmt == "pdb":
            return "ATOM      1  N   GLY A   1      11.104  13.207  10.207  1.00 20.00           N"
        raise KeyError(fmt)

    def get_view(self, quiet=1):
        assert quiet == 1
        return [
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            -50.0,
            0.0,
            0.0,
            0.0,
            2.0,
            120.0,
            50.0,
        ]

    def get(self, key):
        if key == "field_of_view":
            return "50"
        if key == "orthoscopic":
            return "0"
        raise KeyError(key)

    def get_viewport(self):
        return (1600, 900)

    def load_png(self, path, movie=1, quiet=1):
        return None


def test_bridge_render_accepts_quoted_mode_and_preset(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: {"status": "completed", "output_png": ""},
        raising=False,
    )

    result = plugin.bridge_render(
        selection="'(all)'",
        preset="'preset_1_interface'",
        mode="'local'",
    )
    assert result["status"] == "completed"


def test_bridge_render_no_args_uses_remote_preset_1(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = {}

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda _command: (_ for _ in ()).throw(AssertionError("_run_runner should not be called in remote default mode")),
        raising=False,
    )

    def fake_submit_render(**kwargs):
        calls.update(kwargs)
        return {
            "status": "completed",
            "output_name": "from_server.png",
            "png_base64": base64.b64encode(b"png").decode("ascii"),
        }

    monkeypatch.setattr(plugin, "submit_render", fake_submit_render, raising=False)

    result = plugin.bridge_render()

    assert result["status"] == "completed"
    assert result["mode"] == "remote"
    assert calls["host"] == "172.16.30.206"
    assert calls["port"] == 8765
    assert calls["preset"] == "preset_1_interface"


def test_bridge_render_remote_mode_submits_to_server(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = {}

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda _command: (_ for _ in ()).throw(AssertionError("_run_runner should not be called in remote mode")),
        raising=False,
    )

    def fake_submit_render(**kwargs):
        calls.update(kwargs)
        return {
            "status": "completed",
            "output_name": "from_server.png",
            "png_base64": base64.b64encode(b"png").decode("ascii"),
        }

    monkeypatch.setattr(plugin, "submit_render", fake_submit_render, raising=False)

    result = plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="remote",
        server_host="127.0.0.1",
        server_port=9999,
        output_dir=str(tmp_path),
        output_name="remote_result.png",
    )

    assert result["status"] == "completed"
    assert result["mode"] == "remote"
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9999
    output_png = Path(result["output_png"])
    assert output_png.is_file()
    assert output_png.read_bytes() == b"png"


def test_bridge_render_remote_mode_uses_default_server_when_host_omitted(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = {}

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda _command: (_ for _ in ()).throw(AssertionError("_run_runner should not be called in remote mode")),
        raising=False,
    )

    def fake_submit_render(**kwargs):
        calls.update(kwargs)
        return {
            "status": "completed",
            "output_name": "from_server.png",
            "png_base64": base64.b64encode(b"png").decode("ascii"),
        }

    monkeypatch.setattr(plugin, "submit_render", fake_submit_render, raising=False)

    result = plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="remote",
        output_dir=str(tmp_path),
        output_name="remote_default_host.png",
    )

    assert result["status"] == "completed"
    assert result["mode"] == "remote"
    assert calls["host"] == "172.16.30.206"
    assert calls["port"] == 8765
    output_png = Path(result["output_png"])
    assert output_png.is_file()
    assert output_png.read_bytes() == b"png"


def test_bridge_render_forwards_rough_flag(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "completed", "output_png": ""},
        raising=False,
    )

    plugin.bridge_render(selection="(all)", preset="preset_1_interface", mode="local", rough=1)
    command = calls[0]
    assert "--rough" in command
    assert command[command.index("--rough") + 1] == "1"


def test_bridge_render_forwards_raw_view_flag(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "completed", "output_png": ""},
        raising=False,
    )

    plugin.bridge_render(selection="(all)", preset="preset_1_interface", mode="local", raw_view=1)
    command = calls[0]
    assert "--raw-view" in command
    assert command[command.index("--raw-view") + 1] == "1"


def test_bridge_render_forwards_color_follow_flag(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "completed", "output_png": ""},
        raising=False,
    )

    plugin.bridge_render(selection="(all)", preset="preset_1_interface", mode="local", color_follow=1)
    command = calls[0]
    assert "--color-follow" in command
    assert command[command.index("--color-follow") + 1] == "1"


def test_bridge_render_local_preset1_auto_uses_apptainer(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []
    image = tmp_path / "bridge.sif"
    image.write_bytes(b"sif")

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(plugin, "_default_apptainer_image", lambda: image, raising=False)
    monkeypatch.setattr(plugin.sys, "platform", "linux", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "completed", "output_png": ""},
        raising=False,
    )

    plugin.bridge_render(selection="(all)", preset="preset_1_interface", mode="local", rough=1)
    command = calls[0]
    assert "--use-apptainer" in command
    assert command[command.index("--use-apptainer") + 1] == "1"
    assert "--apptainer-image" in command
    assert command[command.index("--apptainer-image") + 1] == str(image)


def test_bridge_render_interactive_forwards_launcher(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "completed", "output_png": ""},
        raising=False,
    )

    plugin.bridge_render_interactive_open(
        backend="qlogin",
        interactive_launcher="qlogin -p gpu --gres=gpu:a4000:1 --mem=16g -c 2",
    )

    command = calls[0]
    assert command[2] == "interactive-open"
    assert "--interactive-launcher" in command


def test_bridge_render_interactive_submit_uses_active_session(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "completed", "output_png": ""},
        raising=False,
    )
    monkeypatch.setattr(plugin, "_ACTIVE_INTERACTIVE_SESSION_ID", "sess123", raising=False)

    plugin.bridge_render(selection="(all)", preset="preset_1_interface", mode="interactive")

    command = calls[0]
    assert command[2] == "interactive-submit"
    assert "--session-id" in command
    assert command[command.index("--session-id") + 1] == "sess123"


def test_bridge_render_interactive_explicit_session_auto_opens_when_stopped(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)

    def fake_run_runner(command):
        calls.append(command)
        subcommand = command[2]
        if subcommand == "interactive-status":
            return {"status": "stopped", "session_id": "sess_missing"}
        if subcommand == "interactive-open":
            return {"status": "ready", "session_id": "sess_missing", "message": "Interactive GPU session ready"}
        if subcommand == "interactive-submit":
            return {"status": "completed", "output_png": ""}
        raise AssertionError(f"unexpected subcommand: {subcommand}")

    monkeypatch.setattr(plugin, "_run_runner", fake_run_runner, raising=False)

    plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="interactive",
        interactive_session="sess_missing",
    )

    assert [cmd[2] for cmd in calls] == ["interactive-status", "interactive-open", "interactive-submit"]
    submit_command = calls[-1]
    assert submit_command[submit_command.index("--session-id") + 1] == "sess_missing"


def test_bridge_render_interactive_open_forwards_attach_flags(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "ready", "session_id": "sess_attach"},
        raising=False,
    )

    plugin.bridge_render_interactive_open(backend="qlogin", attach=1, attach_only=1)

    command = calls[0]
    assert "--attach" in command
    assert command[command.index("--attach") + 1] == "1"
    assert "--attach-only" in command
    assert command[command.index("--attach-only") + 1] == "1"


def test_bridge_render_sets_auto_close_interactive_session_flag(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "completed", "output_png": ""},
        raising=False,
    )

    monkeypatch.setattr(plugin, "_AUTO_CLOSE_INTERACTIVE_SESSION", False, raising=False)
    plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="local",
        auto_close_interactive_session=1,
    )
    assert plugin._AUTO_CLOSE_INTERACTIVE_SESSION is True

    plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="local",
        auto_close_interactive_session=0,
    )
    assert plugin._AUTO_CLOSE_INTERACTIVE_SESSION is False
    assert len(calls) == 2


def test_exit_hook_closes_active_interactive_session_when_enabled(monkeypatch, tmp_path: Path):
    captured = {"command": None}

    def fake_subprocess_run(command, capture_output, text, check):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout='{"status":"closed"}\n', stderr="")

    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin.subprocess, "run", fake_subprocess_run, raising=False)
    monkeypatch.setattr(plugin, "_AUTO_CLOSE_INTERACTIVE_SESSION", True, raising=False)
    monkeypatch.setattr(plugin, "_ACTIVE_INTERACTIVE_SESSION_ID", "sess_exit", raising=False)

    plugin._close_active_interactive_session_at_exit()

    command = captured["command"]
    assert isinstance(command, list)
    assert command[2] == "interactive-close"
    assert "--session-id" in command
    assert command[command.index("--session-id") + 1] == "sess_exit"
    assert "--wait" in command
    assert command[command.index("--wait") + 1] == "0"
    assert plugin._ACTIVE_INTERACTIVE_SESSION_ID in {None, ""}


def test_interactive_open_starts_auto_close_watchdog_when_enabled(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []
    popen_calls = []

    class _DummyProc:
        def poll(self):
            return None

        def terminate(self):
            return None

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return _DummyProc()

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "ready", "session_id": "sess_watchdog"},
        raising=False,
    )
    monkeypatch.setattr(plugin.subprocess, "Popen", fake_popen, raising=False)
    monkeypatch.setattr(plugin, "_AUTO_CLOSE_WATCHDOGS", {}, raising=False)
    monkeypatch.setattr(plugin, "_AUTO_CLOSE_INTERACTIVE_SESSION", False, raising=False)
    monkeypatch.setattr(plugin, "_ACTIVE_INTERACTIVE_SESSION_ID", "", raising=False)

    plugin.bridge_render(mode="interactive_open", attach=1, auto_close_interactive_session=1)

    assert calls and calls[0][2] == "interactive-open"
    assert popen_calls
    matching = [item[0] for item in popen_calls if "sess_watchdog" in item[0]]
    assert matching
    assert matching[0][1] == "-c"


def test_interactive_open_does_not_start_watchdog_when_auto_close_disabled(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []
    popen_calls = []

    class _DummyProc:
        def poll(self):
            return None

        def terminate(self):
            return None

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return _DummyProc()

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(
        plugin,
        "_run_runner",
        lambda command: calls.append(command) or {"status": "ready", "session_id": "sess_no_watchdog"},
        raising=False,
    )
    monkeypatch.setattr(plugin.subprocess, "Popen", fake_popen, raising=False)
    monkeypatch.setattr(plugin, "_AUTO_CLOSE_WATCHDOGS", {}, raising=False)
    monkeypatch.setattr(plugin, "_AUTO_CLOSE_INTERACTIVE_SESSION", False, raising=False)
    monkeypatch.setattr(plugin, "_ACTIVE_INTERACTIVE_SESSION_ID", "", raising=False)

    plugin.bridge_render(mode="interactive_open", attach=1)

    assert calls and calls[0][2] == "interactive-open"
    assert popen_calls == []


def test_interactive_submit_with_explicit_session_sets_active_for_auto_close(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    calls = []
    popen_calls = []

    class _DummyProc:
        def poll(self):
            return None

        def terminate(self):
            return None

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return _DummyProc()

    def fake_run_runner(command):
        calls.append(command)
        subcommand = command[2]
        if subcommand == "interactive-status":
            return {"status": "running", "session_id": "sess_explicit"}
        if subcommand == "interactive-submit":
            return {"status": "completed", "output_png": ""}
        raise AssertionError(f"unexpected subcommand: {subcommand}")

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: tmp_path / "bridge_runner.py", raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda payload: tmp_path / "payload.json", raising=False)
    monkeypatch.setattr(plugin, "_run_runner", fake_run_runner, raising=False)
    monkeypatch.setattr(plugin.subprocess, "Popen", fake_popen, raising=False)
    monkeypatch.setattr(plugin, "_AUTO_CLOSE_WATCHDOGS", {}, raising=False)
    monkeypatch.setattr(plugin, "_AUTO_CLOSE_INTERACTIVE_SESSION", False, raising=False)
    monkeypatch.setattr(plugin, "_ACTIVE_INTERACTIVE_SESSION_ID", "", raising=False)

    plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="interactive",
        interactive_session="sess_explicit",
        auto_close_interactive_session=1,
    )

    assert [cmd[2] for cmd in calls] == ["interactive-status", "interactive-submit"]
    assert plugin._ACTIVE_INTERACTIVE_SESSION_ID == "sess_explicit"
    matching = [item[0] for item in popen_calls if "sess_explicit" in item[0]]
    assert matching
