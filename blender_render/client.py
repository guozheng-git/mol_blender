"""Dependency-free client for the versioned asynchronous API."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode, quote


class ClientError(RuntimeError):
    def __init__(self, message, status=0, code="client_error"):
        super().__init__(message)
        self.status, self.code = status, code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ClientError("Unexpected redirect from render service; check the configured endpoint.")


class Client:
    def __init__(self, url, token, timeout=30):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ClientError("Provide an HTTP(S) service endpoint without embedded credentials.")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ClientError("Remote services require HTTPS or a loopback SSH tunnel.")
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, body=None, *, binary=False, raw=False):
        if method == "POST" and body is None:
            body = {}
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        data = None
        if body is not None:
            data = body if raw else json.dumps(body, allow_nan=False).encode()
            headers["Content-Type"] = "application/octet-stream" if raw else "application/json"
        try:
            with self.opener.open(Request(self.url + path, data=data, headers=headers, method=method), timeout=self.timeout) as response:
                raw = response.read()
                return raw if binary else json.loads(raw)
        except HTTPError as exc:
            try:
                problem = json.loads(exc.read())
            except Exception:
                problem = {}
            raise ClientError(problem.get("error", "Service returned HTTP " + str(exc.code)), exc.code, problem.get("code", "http_error")) from exc
        except URLError as exc:
            raise ClientError("Cannot reach render service: " + str(exc.reason)) from exc

    def upload(self, payload, name="structure.cif"):
        return self.request("POST", "/v1/structures", {"payload": payload, "name": name})

    def upload_file(self, path):
        path = Path(path).expanduser().resolve()
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in {"pse", "cif", "mmcif", "pdb"}:
            raise ClientError("Choose a .pse, .cif, or .pdb file.")
        with path.open("rb") as stream:
            data = stream.read(50 * 1024**2 + 1)
        if len(data) > 50 * 1024**2:
            raise ClientError("File exceeds the 50 MiB upload limit.")
        return self.request("POST", "/v1/files/" + suffix + '?' + urlencode({'name': path.name}), data, raw=True)

    def structures(self):
        return self.request("GET", "/v1/structures")

    def delete_structure(self, input_id):
        return self.request("DELETE", "/v1/structures/" + quote(input_id, safe=''))

    def submit(self, input_id, options=None, idempotency_key=None, *, rerender=False):
        return self.request("POST", "/v1/jobs", {"input_id": input_id, "options": options or {}, "idempotency_key": idempotency_key or uuid.uuid4().hex, "rerender": rerender})

    def status(self, job_id):
        return self.request("GET", "/v1/jobs/" + job_id)

    def cancel(self, job_id):
        return self.request("POST", "/v1/jobs/" + job_id + "/cancel")

    def delete_result(self, job_id):
        return self.request("DELETE", "/v1/jobs/" + job_id + "/result")

    def wait(self, job_id, timeout=1800, progress=None):
        deadline, previous = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            state = self.status(job_id)
            marker = (state["status"], state["queue_position"])
            if progress and marker != previous:
                progress(state)
                previous = marker
            if state["status"] == "done":
                return self.request("GET", "/v1/jobs/" + job_id + "/result")
            if state["status"] in {"failed", "cancelled", "expired", "delivered", "deleted"}:
                raise ClientError(state.get("error") or "Render " + state["status"])
            time.sleep(.5)
        raise ClientError(f"Render {job_id} is still pending. Reconnect with status/result; do not resubmit.")

    def download(self, job_id, output):
        output = Path(output).expanduser().resolve()
        result = self.request("GET", "/v1/jobs/" + job_id + "/result")
        paths = {}
        for item in result["artifacts"]:
            name = item["name"]
            if name not in {"render.png", "render.blend", "manifest.json"}:
                raise ClientError("Unexpected artifact name")
            path = output if name == "render.png" else output.with_suffix(".blend" if name == "render.blend" else ".manifest.json")
            raw = self.request("GET", f"/v1/jobs/{job_id}/artifacts/{name}", binary=True)
            import hashlib
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ClientError("Artifact checksum does not match")
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".partial")
            temp.write_bytes(raw)
            os.replace(temp, path)
            paths[name] = str(path)
        return {"job_id": job_id, "outputs": paths, "manifest": result["manifest"]}


def configured_client(profile="local", **kwargs):
    from .config import load_config
    if profile in {"local", "apptainer"}:
        from .runtime import ensure_local
        connection = ensure_local(**kwargs)
    else:
        config = load_config()
        if profile not in config.get("profiles", {}):
            raise ClientError(f"Unknown profile {profile!r}. Run blender-render profile add first.")
        connection = config["profiles"][profile]
        if connection.get("kind") == "hpc":
            from .hpc import connect
            connection = connect(profile, connection, **kwargs)
    if connection.get("auth") == "oauth":
        from .oauth_client import access_token
        return Client(connection["url"], access_token(connection["url"]), timeout=120)
    token = os.environ.get(connection.get("token_env", "BLENDER_RENDER_TOKEN"), "")
    if connection.get("token_file"):
        token = Path(connection["token_file"]).expanduser().read_text().strip()
    return Client(connection["url"], token)
