"""Small HTTP client; PyMOL invokes it through the configured controller Python."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .auth import private_file
from .config import Limits
from .jsonio import atomic_json, decode_json, json_bytes, read_json
from .. import outputs
from .protocol import PROTOCOL, JobStatus, job_id, stop_waiting, validate_request, validate_url


class RemoteError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RemoteError('Cluster redirects are refused; configure the final service origin')


def profile_path(name):
    from ..config import cluster_root
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name):
        raise ValueError('Invalid cluster profile name')
    return cluster_root() / 'clusters' / (name + '.json')


def configure(name, credential_file=None, ca_file=None, *, url=None):
    if (credential_file is None) == (url is None):
        raise ValueError('Supply either a credential file or an open service URL')
    if url is not None:
        doc = {'url': validate_url(url), 'authentication': 'none'}
    else:
        doc = read_json(private_file(credential_file), 64 * 1024)
        if set(doc) != {'url', 'token'}:
            raise ValueError('Credential must contain url and token only')
        validate_url(doc['url'])
        if not isinstance(doc['token'], str) or not 32 <= len(doc['token']) <= 256:
            raise ValueError('Invalid credential')
    if ca_file:
        doc['ca_file'] = str(Path(ca_file).expanduser().resolve(strict=True))
    target = profile_path(name)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        raise ValueError('Profile already exists; choose another name or remove the old profile explicitly')
    atomic_json(target, doc)
    return {'profile': name, 'url': doc['url']}


class Client:
    def __init__(self, profile='default'):
        self.profile = profile
        path = profile_path(profile)
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"No cluster profile '{profile}' in {path.parent}; run `blender-render cluster connect` "
                             "or `cluster configure` first")
        config = read_json(private_file(path), 64 * 1024)
        if set(config) - {'url', 'token', 'ca_file', 'authentication'} or 'url' not in config:
            raise ValueError('Invalid cluster profile')
        self.url = validate_url(config['url'])
        mode = config.get('authentication', 'token')
        self.token = config.get('token')
        if mode == 'none':
            if 'token' in config:
                raise ValueError('Open cluster profiles must not contain a token')
        elif mode != 'token' or not isinstance(self.token, str) or not 32 <= len(self.token) <= 256:
            raise ValueError('Invalid cluster credential')
        context = ssl.create_default_context(cafile=config.get('ca_file'))
        # Proxy credentials and redirects must not receive the service token.
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    def open(self, method, path, data=None):
        headers = {'Content-Type': 'application/json'}
        if self.token is not None:
            headers['Authorization'] = 'Bearer ' + self.token
        request = Request(self.url + path, data=None if data is None else json_bytes(data), method=method,
                          headers=headers)
        try:
            return self.opener.open(request, timeout=60)
        except HTTPError as exc:
            try:
                message = decode_json(exc.read(4096)).get('error', 'Request rejected')
            except (ValueError, AttributeError):
                message = 'Request rejected'
            raise RemoteError(f'Cluster HTTP {exc.code}: {message}') from None
        except (URLError, TimeoutError, OSError):
            raise RemoteError('Cluster connection failed; recover the existing request before submitting again') from None

    def api(self, method, path, data=None):
        with self.open(method, path, data) as response:
            body = response.read(1024**2 + 1)
        if len(body) > 1024**2:
            raise RemoteError('Cluster response exceeds limit')
        return decode_json(body)

    def submit(self, payload, options, output, *, wait=False, timeout=600):
        from ..authoring import resolve_authored
        from ..config import config_root
        output = Path(output).expanduser().absolute().with_suffix('.png')
        receipt = output.with_suffix('.cluster.json')
        if receipt.exists() or output.exists() or output.with_suffix('.render').exists():
            raise ValueError('Output or cluster receipt exists; recover/fetch that job or choose a new name')
        info = self.api('GET', '/v1/info')
        if info.get('protocol') != PROTOCOL:
            raise RemoteError('Unsupported cluster protocol')
        style = resolve_authored(options, payload, config_root() / 'presets')['style_doc']
        if not style.get('geometry', {}).get('polar_contacts'):
            # Preserve the older server payload contract for existing presets.
            payload = {key: value for key, value in payload.items() if key != 'polar_contacts'}
        document = validate_request({'protocol': PROTOCOL, 'request_id': uuid.uuid4().hex,
            'payload': payload, 'style': style},
            # Older servers may still advertise limits this client no longer knows.
            Limits(**{k: v for k, v in info['limits'].items() if k in Limits.__dataclass_fields__}))
        output.parent.mkdir(parents=True, exist_ok=True)
        record = {'protocol': PROTOCOL, 'profile': self.profile, 'url': self.url,
                  'request_id': document['request_id'], 'output': str(output), 'status': JobStatus.PREPARED}
        # Persist recovery identity before uploading. No coordinates or tokens in this receipt.
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(json_bytes(record))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            result = self.api('POST', '/v1/jobs', document)
        except RemoteError as exc:
            raise RemoteError(f'{exc}. Recover with: blender-render cluster status {receipt}') from None
        record.update(result)
        atomic_json(receipt, record)
        if wait:
            result = self.wait(result['job_id'], timeout=timeout)
            record.update(result)
            atomic_json(receipt, record)
            if result['status'] == JobStatus.COMPLETED:
                result = self.fetch(result['job_id'], output)
        return {**result, 'receipt': str(receipt)}

    def recover(self, receipt):
        record = read_json(receipt, 64 * 1024)
        if record['url'] != self.url or record['profile'] != self.profile:
            raise ValueError('Receipt belongs to a different cluster profile')
        if record.get('job_id'):
            return self.status(record['job_id'])
        return self.api('GET', '/v1/requests/' + job_id(record['request_id']))

    def status(self, identifier):
        return self.api('GET', '/v1/jobs/' + job_id(identifier))

    def wait(self, identifier, timeout=600):
        if not 0 < timeout <= 7 * 86400:
            raise ValueError('Wait timeout must be between 0 and seven days')
        deadline = time.monotonic() + timeout
        while True:
            result = self.status(identifier)
            if stop_waiting(result['status']):
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {**result, 'message': 'Still queued/running; fetch this job later. No new job was submitted.'}
            time.sleep(min(3, remaining))

    def fetch(self, identifier, output):
        from PIL import Image
        result = self.status(identifier)
        if result['status'] != JobStatus.COMPLETED:
            raise RemoteError('Job is ' + result['status'] + '; fetch after completion')
        files = result['artifacts']
        if not files or set(files) - outputs.NAMES or 'render.png' not in files:
            raise RemoteError('Invalid artifact manifest')
        output = Path(output).expanduser().absolute().with_suffix('.png')
        artifacts = outputs.folder(output)
        destinations = {name: outputs.destination(output, name) for name in files}
        if artifacts.exists() or any(p.exists() or p.is_symlink() for p in destinations.values()):
            raise ValueError('Output already exists; choose a new filename')
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.cluster-download-', dir=output.parent) as temporary:
            stage = Path(temporary)
            for name, meta in files.items():
                size, expected = meta['bytes'], meta['sha256']
                if (type(size) is not int or not 0 < size <= Limits().max_artifact_bytes or
                        not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
                    raise RemoteError('Invalid artifact size or digest')
                digest, count = hashlib.sha256(), 0
                with self.open('GET', '/v1/jobs/' + job_id(identifier) + '/artifacts/' + name) as response, (stage / name).open('wb') as handle:
                    while chunk := response.read(min(1024**2, size - count + 1)):
                        count += len(chunk)
                        if count > size:
                            raise RemoteError('Artifact exceeds declared size')
                        digest.update(chunk)
                        handle.write(chunk)
                if count != size or digest.hexdigest() != expected:
                    raise RemoteError('Artifact integrity check failed')
            with Image.open(stage / 'render.png') as png:
                if png.format != 'PNG':
                    raise RemoteError('Render is not a PNG')
                png.verify()
            published = []
            artifacts.mkdir()  # Exclusive reservation; never replace an existing render.
            try:
                for name, destination in destinations.items():
                    os.link(stage / name, destination)
                    published.append(destination)
                atomic_json(artifacts / 'cluster.json', result)
            except Exception:
                for path in published:
                    path.unlink()
                artifacts.rmdir()
                raise
        return {**result, 'outputs': {name: str(p) for name, p in destinations.items()}, 'artifacts': str(artifacts)}
