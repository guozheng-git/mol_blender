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

    def submit(self, input_id, options=None, idempotency_key=None, *, rerender=False, operation_key=None):
        if rerender and not (idempotency_key or operation_key):
            raise ClientError('An explicit repeat requires a stable operation or idempotency key')
        return self.request("POST", "/v1/jobs", {"input_id": input_id, "options": options or {}, "idempotency_key": idempotency_key or uuid.uuid4().hex, "rerender": rerender, 'operation_key': operation_key})

    def render_file(self, path, options=None, idempotency_key=None, *, rerender=False, operation_key=None, selection='(all)'):
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() not in {'.pse', '.cif', '.mmcif', '.pdb'}:
            raise ClientError('Choose a .pse, .cif, or .pdb file.')
        with path.open('rb') as stream:
            data = stream.read(50 * 1024**2 + 1)
        if len(data) > 50 * 1024**2:
            raise ClientError('File exceeds the 50 MiB upload limit.')
        settings = json.dumps({'options': options or {}, 'idempotency_key': idempotency_key,
            'operation_key': operation_key, 'rerender': rerender, 'selection': selection}, allow_nan=False).encode()
        if len(settings) > 65532:
            raise ClientError('Render request envelope exceeds 64 KiB.')
        return self.request('POST', '/v1/render-files/' + path.suffix.lower()[1:] + '?' + urlencode({'name': path.name}),
            len(settings).to_bytes(4, 'big') + settings + data, raw=True)

    def jobs(self):
        return self.request('GET', '/v1/jobs')

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
        from .delivery import recover_result
        return recover_result(self, job_id, output)


class ConfiguredClient(Client):
    """Construction and all reads are inert with respect to service startup."""
    def __init__(self, profile, read_only, options):
        self.profile, self.read_only, self.options = profile, read_only, options

    def request(self, method, path, body=None, **kwargs):
        start = method == 'POST' and (path == '/v1/structures' or path == '/v1/jobs'
            or path.startswith(('/v1/files/', '/v1/render-files/')))
        connection = _connect_client(self.profile, read_only=self.read_only or not start, **self.options)
        return connection.request(method, path, body, **kwargs)

    def download(self, job_id, output):
        # Resolve credentials without startup, then scope receipts to this
        # concrete service and credential. No upload/submit fallback exists.
        return _connect_client(self.profile, read_only=True, **self.options).download(job_id, output)


def configured_client(profile='local', *, read_only=False, **kwargs):
    return ConfiguredClient(profile, read_only, kwargs)


def start_service(profile='local', **kwargs):
    """Explicit user start command; generic client construction stays inert."""
    return _connect_client(profile, read_only=False, **kwargs).request('GET', '/health')


def _connect_client(profile="local", *, read_only=False, admission=False, **kwargs):
    from .config import load_config
    if profile in {"local", "apptainer"}:
        from .runtime import ensure_local, existing_local
        connection = existing_local() if read_only else ensure_local(**kwargs)
    else:
        config = load_config()
        if profile not in config.get("profiles", {}):
            raise ClientError(f"Unknown profile {profile!r}. Run blender-render profile add first.")
        connection = config["profiles"][profile]
        if connection.get("kind") == "hpc":
            if admission:
                # CLI operations belong to this durable outer queue. Only its
                # gateway backend may connect/allocate, after admission.
                from .runtime import ensure_local, existing_local
                connection = (existing_local(worker_profile=profile) if read_only
                              else ensure_local(worker_profile=profile, **kwargs))
            else:
                from .hpc import connect, existing_connection
                connection = existing_connection(profile) if read_only else connect(profile, connection, **kwargs)
    if connection.get("auth") == "oauth":
        from .oauth_client import access_token
        return Client(connection["url"], access_token(connection["url"]), timeout=120)
    token = os.environ.get(connection.get("token_env", "BLENDER_RENDER_TOKEN"), "")
    if connection.get("token_file"):
        token = Path(connection["token_file"]).expanduser().read_text().strip()
    return Client(connection["url"], token)
