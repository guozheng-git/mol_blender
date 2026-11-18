"""Bounded JSON over private local sockets; no pickle, HTTP or remote listener."""
import json
import math
import socket
import struct

MAX_MESSAGE = 1024 * 1024


def seconds(value, *, minimum=0):
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f'Timeout must be finite and at least {minimum} seconds')
    return value


def send(stream, document):
    data = json.dumps(document, allow_nan=False).encode()
    if len(data) > MAX_MESSAGE:
        raise ValueError('Warm worker message is too large')
    stream.sendall(struct.pack('!I', len(data)) + data)


def receive(stream):
    def read(count):
        chunks = bytearray()
        while len(chunks) < count:
            part = stream.recv(count - len(chunks))
            if not part:
                raise ConnectionError('Warm worker connection closed; the request was not replayed')
            chunks.extend(part)
        return bytes(chunks)
    length, = struct.unpack('!I', read(4))
    if not 0 < length <= MAX_MESSAGE:
        raise ValueError('Invalid warm worker message length')
    def invalid(value):
        raise ValueError('Non-finite JSON value')
    result = json.loads(read(length), parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError('Expected a warm worker JSON object')
    return result


def request(session, action, *, timeout=5, **fields):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(timeout)
        stream.connect(session['socket'])
        send(stream, {'id': session['id'], 'action': action, **fields})
        result = receive(stream)
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result
