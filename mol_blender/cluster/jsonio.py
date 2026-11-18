"""Bounded JSON and file helpers used across the cluster package."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read_json(path, limit=20 * 1024**2):
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


