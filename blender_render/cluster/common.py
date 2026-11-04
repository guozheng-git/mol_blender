"""Bounded, data-only cluster protocol. No web framework or scheduler imports."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

PROTOCOL = 1
MAX_REQUEST = 20 * 1024**2
MAX_ARTIFACT = 1024**3
ARTIFACTS = frozenset({'render.png', 'render.mp4', 'render.gif', 'frames.zip',
                       'render.blend', 'manifest.json', 'style.json'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'expired'})
PAYLOAD_KEYS = frozenset({'version', 'generated_at_utc', 'selection', 'state', 'run_id',
    'structure_summary', 'view', 'dss', 'ss_hints', 'cif_text', 'pdb_text', 'color_hints',
    'provenance', 'state_coordinates'})


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('', '/') or not parsed.hostname):
        raise ValueError('Use a service origin URL without credentials, query, fragment or path')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1', 'localhost'}):
        raise ValueError('Use HTTPS, or loopback HTTP through an SSH tunnel')
    return url.rstrip('/')


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read_json(path, limit=MAX_REQUEST):
    with Path(path).open('rb') as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('JSON exceeds size limit')
    return decode_json(raw)


def decode_json(raw):
    def invalid(value):
        raise ValueError('Non-finite JSON is not supported')
    try:
        return json.loads(raw, parse_constant=invalid)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid JSON') from exc


def atomic_json(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def one_line(text, limit=500):
    """Collapse command output to a single bounded line for error messages."""
    return ' '.join((text or '').split())[:limit]


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid job ID')
    return value


@dataclass(frozen=True)
class Limits:
    max_request_bytes: int = MAX_REQUEST
    max_pixels: int = 4096 * 4096
    max_samples: int = 512
    max_frames: int = 360
    max_artifact_bytes: int = MAX_ARTIFACT
    max_active_per_user: int = 4
    max_active_total: int = 32
    max_retained_per_user: int = 32
    max_spool_bytes: int = 50 * 1024**3
    retention_seconds: int = 86400
    render_timeout: int = 3600

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise ValueError('All cluster limits must be positive integers')
        if self.max_request_bytes > MAX_REQUEST or self.max_artifact_bytes > MAX_ARTIFACT:
            raise ValueError('Configured transfer limits exceed protocol limits')


def validate_request(document, limits):
    from ..core import validate_payload_document
    from ..spec import resolve_options
    if not isinstance(document, dict) or set(document) != {'protocol', 'request_id', 'payload', 'style'}:
        raise ValueError('Expected protocol, request_id, payload and style only')
    if type(document['protocol']) is not int or document['protocol'] != PROTOCOL:
        raise ValueError('Unsupported cluster protocol')
    job_id(document['request_id'])
    payload = document['payload']
    if not isinstance(payload, dict) or set(payload) - PAYLOAD_KEYS:
        raise ValueError('Only exported structure data is accepted; no sessions, paths or scripts')
    if 'run_id' in payload and (not isinstance(payload['run_id'], str) or
                               not re.fullmatch('[A-Za-z0-9_-]{1,64}', payload['run_id'])):
        raise ValueError('Export run_id must be a short identifier, never a path')
    view = payload.get('view')
    if not isinstance(view, dict) or set(view) - {'pymol_view', 'fov_deg', 'orthoscopic', 'viewport_px'}:
        raise ValueError('View accepts camera values only, never filenames or paths')
    validate_payload_document(payload)
    resolved = resolve_options({'style_doc': document['style']}, payload)['style_doc']
    output = resolved['output']
    if output['width'] * output['height'] > limits.max_pixels or output['samples'] > limits.max_samples:
        raise ValueError('Render exceeds server pixel/sample limits')
    animation = output.get('animation') or {}
    frames = len(animation.get('path', [])) or animation.get('frames', 1)
    if frames > limits.max_frames:
        raise ValueError('Animation exceeds server frame limit')
    if animation.get('states'):
        coords = payload.get('state_coordinates')
        if not coords or max(animation['states']) > len(coords['coordinates']):
            raise ValueError('Animation requires exported molecular states')
        if animation['states'][animation.get('anchor', 0)] != coords['base']:
            raise ValueError('Animation anchor must match exported state')
    normalized = {**document, 'style': resolved}
    if len(json_bytes(normalized)) > limits.max_request_bytes:
        raise ValueError('Request exceeds server size limit')
    return normalized
