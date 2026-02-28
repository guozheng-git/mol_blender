"""HTTP client helpers for remote Blender render server."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


class RemoteRenderError(RuntimeError):
    """Raised when remote render operations fail."""


def _normalize_base_url(host: str, port: int) -> str:
    host_text = str(host or "").strip()
    if not host_text:
        host_text = "127.0.0.1"
    if host_text.startswith("http://") or host_text.startswith("https://"):
        base = host_text.rstrip("/")
        if ":" in base.rsplit("/", 1)[-1]:
            return base
        return f"{base}:{int(port)}"
    return f"http://{host_text}:{int(port)}"


def _decode_json_response(data: bytes, *, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive parsing
        raise RemoteRenderError(f"{context}: could not decode JSON response") from exc
    if not isinstance(payload, dict):
        raise RemoteRenderError(f"{context}: expected JSON object response")
    return payload


def _http_json(
    *,
    method: str,
    base_url: str,
    path: str,
    body: dict[str, Any] | None,
    timeout: float,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    request_body = b""
    headers = {"Accept": "application/json"}
    if body is not None:
        request_body = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib_request.Request(url=url, data=request_body if body is not None else None, method=method, headers=headers)
    try:
        with urllib_request.urlopen(req, timeout=max(1.0, float(timeout))) as resp:
            response_data = resp.read()
    except urllib_error.HTTPError as exc:
        error_payload = exc.read() if hasattr(exc, "read") else b""
        if error_payload:
            parsed = _decode_json_response(error_payload, context=f"HTTP {exc.code}")
            message = str(parsed.get("error", "")).strip() or f"HTTP {exc.code}"
            raise RemoteRenderError(message)
        raise RemoteRenderError(f"HTTP error {exc.code} while calling {url}") from exc
    except urllib_error.URLError as exc:
        raise RemoteRenderError(f"Could not reach render server at {url}: {exc}") from exc

    return _decode_json_response(response_data, context=f"{method} {path}")


def submit_render(
    *,
    host: str,
    port: int,
    payload: dict[str, Any],
    preset: str = "",
    style_doc: dict[str, Any] | None = None,
    rough: bool = False,
    raw_view: bool = False,
    color_follow: bool = False,
    save_blend: bool = False,
    blend_output: str = "",
    keep_temp: bool = False,
    output_name: str = "",
    timeout: float = 1800.0,
) -> dict[str, Any]:
    base_url = _normalize_base_url(host, int(port))
    request_doc: dict[str, Any] = {
        "payload": payload,
        "rough": bool(rough),
        "raw_view": bool(raw_view),
        "color_follow": bool(color_follow),
        "save_blend": bool(save_blend),
        "blend_output": str(blend_output or ""),
        "keep_temp": bool(keep_temp),
        "output_name": str(output_name or ""),
    }
    preset_text = str(preset or "").strip()
    if style_doc is not None:
        request_doc["style_doc"] = style_doc
    elif preset_text:
        request_doc["preset"] = preset_text

    response = _http_json(
        method="POST",
        base_url=base_url,
        path="/render",
        body=request_doc,
        timeout=timeout,
    )
    status = str(response.get("status", "")).strip().lower()
    if status not in {"completed", "finished", "ok"}:
        message = str(response.get("error", "")).strip() or f"Remote render failed with status={status or '<unknown>'}"
        raise RemoteRenderError(message)
    if not isinstance(response.get("png_base64"), str):
        raise RemoteRenderError("Remote render response did not include 'png_base64'.")
    return response


def healthz(*, host: str, port: int, timeout: float = 5.0) -> dict[str, Any]:
    base_url = _normalize_base_url(host, int(port))
    return _http_json(method="GET", base_url=base_url, path="/healthz", body=None, timeout=timeout)


def decode_png_bytes(response_doc: dict[str, Any]) -> bytes:
    value = response_doc.get("png_base64")
    if not isinstance(value, str) or not value.strip():
        raise RemoteRenderError("Remote response contains empty png_base64 payload.")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise RemoteRenderError("Remote response png_base64 payload is invalid.") from exc


def write_png_response(*, response_doc: dict[str, Any], output_png: Path) -> Path:
    output_path = Path(output_png).expanduser()
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).absolute()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(decode_png_bytes(response_doc))
    return output_path


def ensure_png_suffix(name: str) -> str:
    filename = str(name or "").strip()
    if not filename:
        return "render.png"
    if filename.lower().endswith(".png"):
        return filename
    return f"{filename}.png"
