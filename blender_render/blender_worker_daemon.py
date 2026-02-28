#!/usr/bin/env python3
"""Persistent Blender-side worker that renders jobs over a unix socket."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sys
import time
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw = list(sys.argv if argv is None else argv)
    if "--" in raw:
        tail = raw[raw.index("--") + 1 :]
    else:
        tail = raw[1:]
    parser = argparse.ArgumentParser(description="Blender worker daemon")
    parser.add_argument("--socket", required=True, help="Unix socket path")
    parser.add_argument("--custom-script", required=True, help="Path to blender_custom_render.py")
    parser.add_argument("--idle-timeout", type=float, default=0.0, help="Exit after idle seconds (0 disables).")
    return parser.parse_args(tail)


def _load_renderer_module(custom_script: Path) -> ModuleType:
    path = Path(custom_script).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).absolute()
    if not path.is_file():
        raise RuntimeError(f"Renderer script not found: {path}")

    spec = importlib.util.spec_from_file_location("bridge_custom_render_worker", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load renderer module from: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "_render"):
        raise RuntimeError("Renderer module does not define _render(args).")
    return module


def _read_json_request(conn: socket.socket) -> dict[str, Any]:
    chunks: list[bytes] = []
    while True:
        data = conn.recv(65536)
        if not data:
            break
        chunks.append(data)
        if b"\n" in data:
            break
    raw = b"".join(chunks).strip()
    if not raw:
        raise RuntimeError("Empty request payload")
    if b"\n" in raw:
        raw = raw.split(b"\n", 1)[0].strip()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Request payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Request payload must be a JSON object")
    return payload


def _write_json_response(conn: socket.socket, payload: dict[str, Any]) -> None:
    conn.sendall((json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))


def _render_job(renderer: ModuleType, request: dict[str, Any]) -> dict[str, Any]:
    structure_path = str(request.get("structure_path", "")).strip()
    view_json = str(request.get("view_json", "")).strip()
    style_json = str(request.get("style_json", "")).strip()
    output_png = str(request.get("output_png", "")).strip()
    run_manifest = str(request.get("run_manifest", "")).strip()

    missing = [
        key
        for key, value in (
            ("structure_path", structure_path),
            ("view_json", view_json),
            ("style_json", style_json),
            ("output_png", output_png),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"render_job missing required fields: {', '.join(missing)}")

    args = argparse.Namespace(
        structure=structure_path,
        cif=structure_path,
        view_json=view_json,
        style_json=style_json,
        output_png=output_png,
        run_manifest=run_manifest,
    )
    started = time.time()
    renderer._render(args)
    elapsed = round(time.time() - started, 3)
    return {
        "status": "completed",
        "output_png": output_png,
        "runtime_seconds": elapsed,
    }


def _run_server(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket).expanduser()
    if not socket_path.is_absolute():
        socket_path = (Path.cwd() / socket_path).absolute()
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        renderer = _load_renderer_module(Path(args.custom_script))
    except Exception as exc:
        print(f"blender_worker_daemon: failed to load renderer: {exc}", file=sys.stderr)
        return 1

    if socket_path.exists():
        try:
            socket_path.unlink()
        except Exception:
            pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        server.listen(8)
        os.chmod(socket_path, 0o600)
    except Exception:
        server.close()
        raise

    running = True
    idle_timeout = max(0.0, float(args.idle_timeout))
    last_activity = time.time()

    while running:
        if idle_timeout > 0:
            remaining = idle_timeout - (time.time() - last_activity)
            if remaining <= 0:
                break
            server.settimeout(min(1.0, max(0.05, remaining)))
        else:
            server.settimeout(1.0)

        try:
            conn, _addr = server.accept()
        except socket.timeout:
            continue
        except Exception as exc:
            print(f"blender_worker_daemon: accept failed: {exc}", file=sys.stderr)
            continue

        with conn:
            last_activity = time.time()
            try:
                request = _read_json_request(conn)
                action = str(request.get("action", "")).strip().lower()
                if action == "ping":
                    _write_json_response(
                        conn,
                        {
                            "status": "ready",
                            "pid": os.getpid(),
                        },
                    )
                    continue
                if action == "shutdown":
                    _write_json_response(conn, {"status": "shutting_down", "pid": os.getpid()})
                    running = False
                    continue
                if action != "render_job":
                    raise RuntimeError(f"Unsupported action: {action or '<empty>'}")

                result = _render_job(renderer, request)
                result["pid"] = os.getpid()
                _write_json_response(conn, result)
            except Exception as exc:
                _write_json_response(
                    conn,
                    {
                        "status": "error",
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                        "pid": os.getpid(),
                    },
                )

    try:
        server.close()
    finally:
        try:
            if socket_path.exists():
                socket_path.unlink()
        except Exception:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return _run_server(args)


if __name__ == "__main__":
    raise SystemExit(main())
