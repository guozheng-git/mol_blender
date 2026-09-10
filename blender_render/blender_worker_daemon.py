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

    from blender_render.engine import protect, RestartRequired
    seal = protect('render')
    if path.resolve() != seal.root / 'blender_custom_render.py':
        raise RestartRequired('Renderer must be the sealed package entrypoint')
    spec = importlib.util.spec_from_file_location("bridge_custom_render_worker", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load renderer module from: {path}")
    module = importlib.util.module_from_spec(spec)
    source = seal.inputs['blender_custom_render.py']
    seal.check()
    exec(compile(source, str(path), 'exec'), module.__dict__)
    seal.check()
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


def _error_response(exc):
    from blender_render.engine import RestartRequired, EngineMismatch, ExecutionUnknown
    condition = ('restart_required' if isinstance(exc, RestartRequired) else
                 'compatibility_blocked' if isinstance(exc, EngineMismatch) else
                 'execution_unknown' if isinstance(exc, ExecutionUnknown) else None)
    return {'status':'error', 'error':str(exc), 'condition':condition,
            'before_execution':getattr(exc, 'before_execution', isinstance(exc, EngineMismatch)),
            'traceback':traceback.format_exc(), 'pid':os.getpid()}


def _render_job(renderer: ModuleType, request: dict[str, Any]) -> dict[str, Any]:
    from blender_render.engine import engine_identity, require_compatible, runtime_observation, RuntimeUnavailable
    require_compatible(request.get('expected_engine'), engine_identity(), request.get('required_capabilities', []))
    if not runtime_observation()['verified']:
        raise RuntimeUnavailable('Worker runtime mismatch; explicit setup required')
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
    from blender_render.engine import protect
    protect('render')
    if not runtime_observation()['verified']:
        raise RuntimeUnavailable('Worker CCD/runtime mismatch; explicit setup required')
    renderer._bridge_dispatch_count = getattr(renderer, '_bridge_dispatch_count', 0) + 1
    started = time.monotonic()
    renderer._render(args)
    elapsed = round(time.monotonic() - started, 3)
    manifest_path = Path(run_manifest)
    manifest = json.loads(manifest_path.read_text())
    manifest['engine'] = engine_identity()
    manifest['worker_runtime'] = runtime_observation()
    manifest['worker_render_count'] = renderer._bridge_dispatch_count
    manifest_path.write_text(json.dumps(manifest, indent=2))
    from blender_render.telemetry import read_timings
    return {
        "status": "completed",
        "output_png": output_png,
        "runtime_seconds": elapsed,
        'timings': read_timings(run_manifest),
    }


def _run_server(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(Path(args.custom_script).resolve().parent.parent))
    socket_path = Path(args.socket).expanduser()
    if not socket_path.is_absolute():
        socket_path = (Path.cwd() / socket_path).absolute()
    socket_path.parent.mkdir(parents=True, exist_ok=True)

    from blender_render.engine import protect, RestartRequired, RuntimeUnavailable
    startup_error = None
    try:
        protect('render')
        renderer = _load_renderer_module(Path(args.custom_script))
        renderer._configure_molecularnodes_import_path()
        # Worker-only runtime closure; no controller imports these dependencies.
        import molecularnodes
        import molecularnodes.nodes.styles
        import biotite.structure
        import biotite.structure.io.pdbx
        renderer._ensure_molecularnodes_ready(molecularnodes)
        protect('render')
        from blender_render.engine import engine_identity, runtime_observation
        loaded_engine = engine_identity()
        loaded_runtime = runtime_observation()
    except Exception as exc:
        # Remain available for a typed readiness refusal and shutdown. An exit
        # without this report would make the controller infer a generic failure.
        startup_error = _error_response(exc if isinstance(exc, RestartRequired) else
                                         RuntimeUnavailable('Worker runtime initialization unavailable; explicit setup required'))

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
                if action == "shutdown":
                    _write_json_response(conn, {"status": "shutting_down", "pid": os.getpid()})
                    running = False
                    continue
                if startup_error:
                    _write_json_response(conn, startup_error)
                    continue
                if action == "ping":
                    protect('render')
                    loaded_runtime = runtime_observation()
                    _write_json_response(
                        conn,
                        {
                            "status": "ready" if loaded_runtime["verified"] else "unavailable",
                            "pid": os.getpid(),
                            'engine': loaded_engine,
                            'runtime': loaded_runtime,
                        },
                    )
                    continue
                if action != "render_job":
                    raise RuntimeError(f"Unsupported action: {action or '<empty>'}")

                result = _render_job(renderer, request)
                result["pid"] = os.getpid()
                _write_json_response(conn, result)
            except Exception as exc:
                _write_json_response(conn, _error_response(exc))

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
