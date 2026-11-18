"""Per-user bearer credentials; only SHA-256 digests are stored by the service."""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets

from .jsonio import atomic_json, read_json


def private_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Credential file must be a regular, private file')
    stat = path.stat()
    if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise ValueError('Credential file must be owned by this account and mode 0600')
    return path


def users(path):
    doc = read_json(private_file(path), 1024**2)
    if not isinstance(doc, dict) or set(doc) != {'users'} or not isinstance(doc['users'], dict):
        raise ValueError('Invalid users file')
    for owner, digest in doc['users'].items():
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', owner) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid user name or token digest')
    if len(set(doc['users'].values())) != len(doc['users']):
        raise ValueError('A token cannot identify more than one user')
    return doc['users']


def authenticate(path, authorization):
    if not authorization.startswith('Bearer ') or not 32 <= len(authorization[7:]) <= 256:
        return None
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    for owner, expected in users(path).items():
        if hmac.compare_digest(digest, expected):
            return owner
    return None


def add_user(path, owner, url, credential_output):
    from .protocol import validate_url
    validate_url(url)
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', owner):
        raise ValueError('Use a short user name containing letters, digits, dot, dash or underscore')
    path, output = Path(path), Path(credential_output)
    current = users(path) if path.exists() else {}
    if owner in current:
        raise ValueError('User already exists; revoke before issuing a replacement credential')
    if output.exists() or output.is_symlink():
        raise ValueError('Credential output already exists')
    token = secrets.token_urlsafe(32)
    # Exclusive creation prevents overwriting a credential or following a symlink.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        from .jsonio import json_bytes
        with os.fdopen(fd, 'wb') as handle:
            handle.write(json_bytes({'url': url.rstrip('/'), 'token': token}))
        current[owner] = hashlib.sha256(token.encode()).hexdigest()
        atomic_json(path, {'users': current})
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {'user': owner, 'credential_file': str(output)}


def revoke_user(path, owner):
    current = users(path)
    if owner not in current:
        raise ValueError('Unknown user')
    del current[owner]
    atomic_json(path, {'users': current})
    return {'revoked': owner}
