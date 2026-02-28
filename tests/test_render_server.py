from __future__ import annotations

import base64
import shutil
from pathlib import Path

import blender_render.render_server as render_server


class _FakeWorker:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def submit_render(self, **kwargs):
        self.calls.append(kwargs)
        output_png = Path(str(kwargs["output_png"]))
        output_png.parent.mkdir(parents=True, exist_ok=True)
        output_png.write_bytes(b"png-data")
        return {"status": "completed", "runtime_seconds": 0.12}

    def status(self):
        return {"running": True}

    def stop(self):
        return None


class _FallbackWorker(_FakeWorker):
    def submit_render(self, **kwargs):
        self.calls.append(kwargs)
        structure_path = Path(str(kwargs["structure_path"]))
        output_png = Path(str(kwargs["output_png"]))
        output_png.parent.mkdir(parents=True, exist_ok=True)
        if structure_path.suffix.lower() == ".pdb":
            output_png.write_bytes(b"png-data")
        return {"status": "completed", "runtime_seconds": 0.11}


def _payload_with_view() -> dict[str, object]:
    return {
        "version": "1.0",
        "run_id": "testrun",
        "cif_text": "\n".join(
            [
                "data_test",
                "loop_",
                "_atom_site.group_PDB",
                "_atom_site.id",
                "ATOM 1",
            ]
        ),
        "pdb_text": "ATOM      1  N   GLY A   1      11.104  13.207  10.207  1.00 20.00           N",
        "view": {"pymol_view": [0.0] * 18},
        "dss": {"ran": True},
    }


def test_process_render_request_returns_png_base64(tmp_path: Path):
    worker = _FakeWorker()
    state = render_server.RenderServerState(worker=worker, default_job_timeout=120)

    request_doc = {
        "payload": _payload_with_view(),
        "preset": "preset_1_interface",
        "output_name": "result.png",
        "keep_temp": True,
    }
    result = render_server._process_render_request(request_id="req1", request_doc=request_doc, state=state)

    assert result["status"] == "completed"
    assert result["output_name"] == "result.png"
    assert base64.b64decode(result["png_base64"]) == b"png-data"
    assert worker.calls
    assert str(worker.calls[0]["structure_path"]).endswith(".cif")

    shutil.rmtree(Path(result["temp_dir"]), ignore_errors=True)


def test_process_render_request_falls_back_to_pdb_for_non_preset1(tmp_path: Path):
    worker = _FallbackWorker()
    state = render_server.RenderServerState(worker=worker, default_job_timeout=120)

    request_doc = {
        "payload": _payload_with_view(),
        "preset": "preset_2_frosted_blue",
        "output_name": "result.png",
        "keep_temp": False,
    }
    result = render_server._process_render_request(request_id="req2", request_doc=request_doc, state=state)

    assert result["status"] == "completed"
    assert len(worker.calls) == 2
    assert Path(str(worker.calls[0]["structure_path"])).suffix.lower() == ".cif"
    assert Path(str(worker.calls[1]["structure_path"])).suffix.lower() == ".pdb"
