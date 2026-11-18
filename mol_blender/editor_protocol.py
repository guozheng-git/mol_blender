"""Private desktop-editor transport and bounded values; never a network service."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import socket
import stat

from .warm_protocol import receive, send

MAX_OPERATIONS = 512
MAX_CHANGES = 64


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('Expected a local editor session or operation ID')
    return value


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Editor directories must not be symlinks')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Editor directory must be owned by you with mode 0700')
    return path


def number(value, minimum, maximum, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected a finite number')
    if not minimum <= value <= maximum or (integer and type(value) is not int):
        raise ValueError(f'Expected {"integer" if integer else "number"} from {minimum} to {maximum}')
    return value


def checked_value(value, spec):
    kind = spec['type']
    if kind == 'color':
        if not isinstance(value, str) or not re.fullmatch(r'#[a-fA-F0-9]{6}', value):
            raise ValueError('Use a #RRGGBB color')
        return value.upper()
    if kind == 'boolean':
        if type(value) is not bool:
            raise ValueError('Expected a boolean')
    elif kind == 'enum':
        if value not in spec['choices']:
            raise ValueError('Expected one of ' + ', '.join(map(str, spec['choices'])))
    elif kind in ('number', 'integer'):
        number(value, spec['minimum'], spec['maximum'], integer=kind == 'integer')
    elif kind == 'vector':
        if not isinstance(value, list) or len(value) != spec['length']:
            raise ValueError('Invalid vector length')
        for item in value:
            number(item, spec['minimum'], spec['maximum'])
    elif kind != 'color':
        raise ValueError('Unsupported control type')
    return value


def exchange(session, message):
    private_directory(Path(session['socket']).parent)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(5)
        stream.connect(session['socket'])
        send(stream, {'token': session['token'], **message})
        response = receive(stream)
    if 'error' in response:
        raise RuntimeError(response['error'])
    return response


def write_json(path, value):
    temporary = Path(path).with_suffix('.tmp')
    with temporary.open('w') as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, allow_nan=False, indent=2)
    temporary.replace(path)
