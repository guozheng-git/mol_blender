from __future__ import annotations

from pathlib import Path

import pymol_plugin_bridge.pymol_bridge_plugin as plugin


class FakeCmd:
    def get_str(self, fmt, selection, state=-1):
        assert fmt == "cif"
        return "\n".join(["data_demo", "loop_", "_atom_site.group_PDB", "_atom_site.id", "ATOM 1"])

    def get_view(self, quiet=1):
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

    def load_png(self, *_args, **_kwargs):
        return None


def test_local_mode_does_not_force_apptainer_when_blender_missing(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    fake_runner = tmp_path / "bridge_runner.py"
    fake_runner.write_text("# runner", encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: fake_runner, raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda _payload: payload_path, raising=False)

    def fake_run_runner(command):
        calls.append(command)
        return {"status": "completed", "output_png": ""}

    monkeypatch.setattr(plugin, "_run_runner", fake_run_runner, raising=False)

    result = plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="local",
        blender_exe="/missing/blender",
    )

    assert result["status"] == "completed"
    assert calls
    command = calls[0]
    assert "--use-apptainer" not in command
    assert "--apptainer-image" not in command


def test_apptainer_image_arg_enables_apptainer_even_when_flag_zero(monkeypatch, tmp_path: Path):
    fake = FakeCmd()
    fake_runner = tmp_path / "bridge_runner.py"
    fake_runner.write_text("# runner", encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    image_path = tmp_path / "bridge.sif"
    image_path.write_text("sif", encoding="utf-8")

    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: fake_runner, raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda _payload: payload_path, raising=False)
    monkeypatch.setattr(plugin, "_run_runner", lambda command: calls.append(command) or {"status": "completed"}, raising=False)

    plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="local",
        use_apptainer=0,
        apptainer_image=str(image_path),
    )

    assert calls
    command = calls[0]
    assert "--use-apptainer" in command
    assert "--apptainer-image" in command


def test_use_apptainer_flag_kept_when_explicitly_enabled(
    monkeypatch, tmp_path: Path
):
    fake = FakeCmd()
    fake_runner = tmp_path / "bridge_runner.py"
    fake_runner.write_text("# runner", encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    image_path = tmp_path / "bridge.sif"
    image_path.write_text("sif", encoding="utf-8")

    calls = []

    monkeypatch.setattr(plugin, "_PYMOL_CMD", fake, raising=False)
    monkeypatch.setattr(plugin, "_runner_path", lambda: fake_runner, raising=False)
    monkeypatch.setattr(plugin, "_write_payload", lambda _payload: payload_path, raising=False)
    monkeypatch.setattr(plugin, "_run_runner", lambda command: calls.append(command) or {"status": "completed"}, raising=False)

    plugin.bridge_render(
        selection="(all)",
        preset="preset_1_interface",
        mode="local",
        use_apptainer=1,
        apptainer_image=str(image_path),
    )

    assert calls
    command = calls[0]
    assert "--use-apptainer" in command
    assert "--apptainer-image" in command
    assert command[command.index("--apptainer-image") + 1] == str(image_path)


def test_find_apptainer_executable_falls_back_to_common_paths(monkeypatch):
    monkeypatch.setattr(plugin.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        plugin.Path,
        "is_file",
        lambda self: str(self) == "/usr/bin/apptainer",
        raising=False,
    )

    assert plugin._find_apptainer_executable() == "/usr/bin/apptainer"
