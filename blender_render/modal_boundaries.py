"""Provider-injected contracts usable without importing or contacting Modal."""
from __future__ import annotations
import json
import shlex


def convert_file(data, suffix, *, selection='(all)', create_sandbox):
    if suffix not in {'.pse', '.pdb', '.cif', '.mmcif'} or len(data) > 50*1024**2:
        raise ValueError('Invalid molecular upload')
    if not isinstance(selection, str) or not selection.strip() or '\x00' in selection:
        raise ValueError('Invalid selection')
    sandbox = create_sandbox()
    try:
        sandbox.filesystem.write_bytes(data, '/tmp/input' + suffix)
        command = ['/usr/bin/python3', '/opt/export.py', '/tmp/input'+suffix,
                   '/tmp/payload.json', '--selection', selection]
        process = sandbox.exec('/bin/sh', '-c', shlex.join(command) + ' >/dev/null 2>&1')
        process.wait()
        if process.returncode:
            raise ValueError('The molecular file could not be converted')
        reader = sandbox.exec('/bin/sh', '-c', '/usr/bin/head -c 52428801 /tmp/payload.json > /tmp/bounded.json 2>/dev/null')
        reader.wait()
        payload = sandbox.filesystem.read_bytes('/tmp/bounded.json')
        if reader.returncode or len(payload) > 50*1024**2:
            raise ValueError('Converted structure is too large')
        return json.loads(payload)
    finally:
        sandbox.terminate()
