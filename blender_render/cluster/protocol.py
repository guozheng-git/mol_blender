"""Cluster wire protocol shared by client and server: version, job states, request checks.

Site-tunable limits live in ``config.Limits`` and are loaded from server.json; the files
a render may return are listed in ``blender_render.outputs``.
"""
from __future__ import annotations

from enum import StrEnum
import re
from urllib.parse import urlsplit

from .jsonio import json_bytes

PROTOCOL = 1


class JobStatus(StrEnum):
    """Lifecycle of a cluster job. Members compare equal to their JSON strings."""
    PREPARED = 'prepared'                      # client receipt written, not yet uploaded
    SUBMITTING = 'submitting'
    SUBMISSION_UNKNOWN = 'submission_unknown'  # sbatch outcome lost; needs a human
    QUEUED = 'queued'
    RUNNING = 'running'
    FINALIZING = 'finalizing'
    CANCELLING = 'cancelling'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'


_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.EXPIRED})


def is_terminal(status):
    """The job will never change state again."""
    return status in _TERMINAL


def stop_waiting(status):
    """Polling should end: the job finished, or its submission needs a human to resolve."""
    return is_terminal(status) or status == JobStatus.SUBMISSION_UNKNOWN


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
            parsed.path not in ('', '/') or not parsed.hostname):
        raise ValueError('Use a service origin URL without credentials, query, fragment or path')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1', 'localhost'}):
        raise ValueError('Use HTTPS, or loopback HTTP through an SSH tunnel')
    return url.rstrip('/')


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('Invalid job ID')
    return value


def validate_request(document, limits):
    from ..core import PAYLOAD_KEYS, validate_payload_document
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
