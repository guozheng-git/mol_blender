"""Verified local result receipts; no rendering or compute admission."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from .config import config_root


def _atomic(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def recover_result(client, job_id, output):
    from .runtime import file_lock
    scope = hashlib.sha256((client.url + '\0' + client.token + '\0' + job_id).encode()).hexdigest()
    receipt_path = config_root() / 'result-receipts' / (scope + '.json')
    with file_lock(receipt_path.with_suffix('.lock')):
        return _recover(client, job_id, output, receipt_path)


def _recover(client, job_id, output, receipt_path):
    from .client import ClientError
    output = Path(output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Check destination writability before any potentially consuming request.
    with tempfile.TemporaryFile(dir=output.parent):
        pass
    try:
        receipt = json.loads(receipt_path.read_text())
    except FileNotFoundError:
        receipt = {'version': 2, 'job_id': job_id, 'artifacts': [], 'outputs': {}}
    base = '/v1/jobs/' + quote(job_id, safe='')
    manifest = {}
    try:
        result = client.request('GET', base + '/result?inventory=true')
        if result.get('delivery_policy') != 'explicit_ack_v1':
            raise ClientError('Service does not support recoverable delivery; upgrade it before download. No render was submitted.')
        if 'expected_artifacts' not in result:
            raise ClientError('Service lacks expected artifact inventory; upgrade it before recovery.')
        receipt['artifacts'] = [dict(item, expected=True) for item in result['expected_artifacts']]
        receipt['inventory_complete'] = result['inventory_complete']
        receipt['version'] = 2
        manifest = result.get('manifest', {})
    except (ClientError, OSError):
        if not receipt['artifacts']:
            raise
        # Offline recovery still requires an earlier complete inventory and
        # independently verified local bytes. Never infer a shrinking inventory.
        for item in receipt['artifacts']:
            item['availability'] = 'unknown'
    destinations = {}
    for item in receipt['artifacts']:
        name = item['name']
        if name not in {'render.png', 'render.blend', 'manifest.json'}:
            raise ClientError('Unexpected artifact name')
        destinations[name] = output if name == 'render.png' else output.with_suffix('.blend' if name == 'render.blend' else '.manifest.json')
    if len(set(destinations.values())) != len(destinations):
        raise ClientError('Output paths overlap; choose a PNG filename')
    _atomic(receipt_path, json.dumps(receipt).encode())
    paths, errors = {}, {}
    for item in receipt['artifacts']:
        name, path = item['name'], destinations[item['name']]
        item['verified_locally'] = False
        item['checksum_failure'] = False
        item['required'] = item.get('required', name in {'render.png', 'render.blend'})
        try:
            verified = False
            # Reconstruct a moved destination from previously verified output.
            for candidate in (path, Path(receipt['outputs'].get(name, str(path)))):
                if candidate.is_file():
                    raw = candidate.read_bytes()
                    if len(raw) == item['size'] and hashlib.sha256(raw).hexdigest() == item['sha256']:
                        verified = True
                        if candidate != path:
                            _atomic(path, raw)
                        break
                    item['checksum_failure'] = True
            if not verified:
                if item.get('availability') != 'available' or item.get('sha256') is None or item.get('size') is None:
                    raise ClientError('artifact_unavailable: expected artifact has no verified local copy and is unavailable remotely')
                raw = client.request('GET', base + '/artifacts/' + name + '?delivery=ack', binary=True)
                if len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                    item['checksum_failure'] = True
                    raise ClientError('Artifact checksum does not match')
                _atomic(path, raw)
            item['verified_locally'] = True
            paths[name] = str(path)
            receipt['outputs'][name] = str(path)
            # Persist verification before acknowledgement permits server deletion.
            _atomic(receipt_path, json.dumps(receipt).encode())
            try:
                client.request('POST', base + '/artifacts/' + name + '/ack', {'sha256': item['sha256']})
            except (ClientError, OSError):
                # Output is safe locally; server retains it until ack or TTL.
                pass
        except (ClientError, OSError) as exc:
            errors[name] = str(exc)
    _atomic(receipt_path, json.dumps(receipt).encode())
    if 'manifest.json' in paths:
        manifest = json.loads(Path(paths['manifest.json']).read_text())
    missing = [a['name'] for a in receipt['artifacts'] if a['required'] and not a['verified_locally']]
    if 'render.png' not in paths and 'render.png' not in missing:
        missing.insert(0, 'render.png')
    complete = receipt.get('inventory_complete', False) and not missing
    if not receipt.get('inventory_complete', False):
        errors['inventory'] = 'artifact_unavailable: original required inventory could not be reconstructed'
    return {'job_id': job_id, 'outputs': paths, 'manifest': manifest,
            'complete': complete, 'errors': errors, 'missing_required': missing,
            'error_code': None if complete else 'artifact_unavailable',
            'artifacts': receipt['artifacts'],
            'next_action': 'done' if complete else 'recover_verified_local_copy_or_stop'}
