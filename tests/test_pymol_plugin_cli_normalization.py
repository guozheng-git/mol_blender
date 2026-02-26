from __future__ import annotations

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


def test_bridge_render_no_args_uses_local_preset_1(monkeypatch, tmp_path: Path):
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

    result = plugin.bridge_render()

    assert result["status"] == "completed"
    assert calls
    command = calls[0]
    assert command[2] == "local"
    assert "--preset" in command
    assert command[command.index("--preset") + 1] == "preset_1_interface"


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
