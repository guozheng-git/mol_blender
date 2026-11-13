"""Batch recovery must only attach to confirmed saved jobs, never resubmit."""

import json
from pathlib import Path

from co_pymol.render.client import write_json
from test_blender import bridge, calls, fake_repo  # noqa: F401 (fixtures)


def test_batch_recovery_only_resumes_saved_unfinished_jobs(bridge, monkeypatch):  # noqa: F811
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "_launch", lambda *args: None)
        receipt = bridge.submit_batch(
            {"cif_text": "frozen scene", "view": {"viewport_px": [800, 450]}},
            presets=["mint", "neon", "unknown", "cancelled"],
            pse=b"one frozen session",
            name="pipeline",
            width=800,
            height=0,
            samples=16,
            keep_colors=True,
            save_blend=False,
        )
    runs = {run["preset"]: Path(run["directory"]) for run in receipt["runs"]}
    for preset, state in (
        ("mint", {"status": "needs_attention", "job_id": "original-mint-job"}),
        ("neon", {"status": "completed", "job_id": "original-neon-job"}),
        ("unknown", {"status": "submitting"}),
        ("cancelled", {"status": "cancelled", "job_id": "cancelled-job"}),
    ):
        write_json(runs[preset] / "state.json", state)

    bridge.result(receipt["name"])
    for process in bridge._processes:
        process.wait(timeout=10)

    # Only the confirmed unfinished job is observed and fetched.
    assert calls(bridge) == ["status", "fetch"]
    states = {
        preset: json.loads((path / "state.json").read_text())
        for preset, path in runs.items()
    }
    assert states["mint"]["status"] == "completed"
    assert states["mint"]["job_id"] == "original-mint-job"
    assert states["neon"] == {"status": "completed", "job_id": "original-neon-job"}
    assert states["unknown"]["status"] == "needs_attention"
    assert "will not resubmit" in states["unknown"]["error"]
    assert states["cancelled"]["status"] == "cancelled"
    assert bridge.status(receipt["name"])["status"] == "needs_attention"
