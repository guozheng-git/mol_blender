from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import blender_render.remote_client as remote_client


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]):
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._data


def test_submit_render_success(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        body = req.data.decode("utf-8")
        captured["body"] = json.loads(body)
        return _FakeHTTPResponse(
            {
                "status": "completed",
                "output_name": "out.png",
                "png_base64": base64.b64encode(b"pngdata").decode("ascii"),
            }
        )

    monkeypatch.setattr(remote_client.urllib_request, "urlopen", fake_urlopen)

    result = remote_client.submit_render(
        host="127.0.0.1",
        port=9000,
        payload={"view": {"pymol_view": [0.0] * 18}, "cif_text": "data_test\n", "pdb_text": "ATOM 1"},
        preset="preset_1_interface",
        timeout=33,
    )

    assert result["status"] == "completed"
    assert captured["url"].endswith(":9000/render")
    assert captured["timeout"] == 33
    assert captured["body"]["preset"] == "preset_1_interface"


def test_write_png_response(tmp_path: Path):
    output = tmp_path / "render.png"
    doc = {"png_base64": base64.b64encode(b"abc").decode("ascii")}

    written = remote_client.write_png_response(response_doc=doc, output_png=output)

    assert written == output
    assert output.read_bytes() == b"abc"


def test_decode_png_invalid_payload_raises():
    with pytest.raises(remote_client.RemoteRenderError):
        remote_client.decode_png_bytes({"png_base64": "###notbase64###"})
