from __future__ import annotations

import base64
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
        "--raw-view",
        "1",
    ])

    assert result["status"] == "completed"
    assert calls
    command = calls[0]
    assert command[2] == "local"
    assert "--payload-json" in command
    assert "--preset" in command
    assert "--rough" in command
    assert "--raw-view" in command


def test_pse_cli_remote_mode_calls_server(monkeypatch, tmp_path: Path):
    pse_path = tmp_path / "sample.pse"
    pse_path.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "_load_payload_from_pse",
        lambda **_kwargs: {
            "version": "1.0",
            "run_id": "testrun",
            "cif_text": "data_demo\n#\n",
            "view": {"pymol_view": [0.0] * 18},
        },
    )
    monkeypatch.setattr(
        cli,
        "_run_runner",
        lambda _command: (_ for _ in ()).throw(AssertionError("_run_runner should not be used for remote mode")),
    )

    calls = {}

    def fake_submit_render(**kwargs):
        calls.update(kwargs)
        return {
            "status": "completed",
            "output_name": "from_server.png",
            "png_base64": base64.b64encode(b"png").decode("ascii"),
        }

    monkeypatch.setattr(cli, "submit_render", fake_submit_render)

    result = cli.run(
        [
            "--pse",
            str(pse_path),
            "--mode",
            "remote",
            "--server-host",
            "localhost",
            "--server-port",
            "8765",
            "--output-dir",
            str(tmp_path),
            "--output-name",
            "cli_remote.png",
        ]
    )

    assert result["status"] == "completed"
    assert result["mode"] == "remote"
    assert calls["host"] == "localhost"
    assert calls["port"] == 8765
    output_png = Path(result["output_png"])
    assert output_png.is_file()
    assert output_png.read_bytes() == b"png"
