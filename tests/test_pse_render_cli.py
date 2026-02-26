from __future__ import annotations

from pathlib import Path

import blender_render.pse_render_cli as cli


def test_pse_cli_builds_runner_command(monkeypatch, tmp_path: Path):
    pse_path = tmp_path / "sample.pse"
    pse_path.write_text("placeholder", encoding="utf-8")

    payload_path = tmp_path / "payload.json"
    runner_path = tmp_path / "bridge_runner.py"
    runner_path.write_text("# runner", encoding="utf-8")

    calls = []

    monkeypatch.setattr(
        cli,
        "_load_payload_from_pse",
        lambda **_kwargs: {
            "version": "1.0",
            "run_id": "testrun",
            "cif_text": "data_demo\n#\n",
            "view": {
                "pymol_view": [
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
            },
        },
    )
    monkeypatch.setattr(cli, "_write_payload", lambda _payload: payload_path)
    monkeypatch.setattr(cli, "_runner_path", lambda: runner_path)
    monkeypatch.setattr(cli, "_run_runner", lambda command: calls.append(command) or {"status": "completed"})

    result = cli.run([
        "--pse",
        str(pse_path),
        "--preset",
        "preset_1_interface",
        "--mode",
        "local",
        "--rough",
        "1",
    ])

    assert result["status"] == "completed"
    assert calls
    command = calls[0]
    assert command[2] == "local"
    assert "--payload-json" in command
    assert "--preset" in command
    assert "--rough" in command

