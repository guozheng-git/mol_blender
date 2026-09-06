"""Lab-owned OAuth gateway: private structures never transit the shared service."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .client import configured_client
from .jobs import JobError


class GatewayBackend:
    """Forward the durable lab queue to a configured worker, preserving retries."""
    paid = False

    def __init__(self, store, profile):
        self.store, self.profile, self.active = store, profile, None

    def render(self, request, identifier, cancelled):
        client = configured_client(self.profile)
        source = client.upload(request['payload'])
        options = {key: request[key] for key in ('style_doc','rough','raw_view','color_follow','save_blend') if key in request}
        # The gateway already resolved camera framing and output dimensions.
        options['preserve_view'] = False
        job = client.submit(source['input_id'], options, 'gateway-' + identifier)
        self.store.set_remote_id(identifier, job['job_id'])
        self.active = client, job['job_id']
        deadline = time.monotonic() + request.get('job_timeout_seconds', 1800)
        try:
            while True:
                if cancelled() or time.monotonic() >= deadline:
                    client.cancel(job['job_id'])
                    raise RuntimeError('Lab render cancelled or timed out')
                state = client.status(job['job_id'])
                if state['status'] == 'done':
                    result = client.request('GET', '/v1/jobs/' + job['job_id'] + '/result')
                    response = {'status':'completed', 'manifest':result['manifest'],
                                'runtime_seconds':result['manifest'].get('runtime_seconds')}
                    for artifact in result['artifacts']:
                        name = artifact['name']
                        if name not in {'render.png','render.blend'}:
                            continue
                        data = client.request('GET', f"/v1/jobs/{job['job_id']}/artifacts/{name}", binary=True)
                        if hashlib.sha256(data).hexdigest() != artifact['sha256']:
                            raise RuntimeError('Lab worker artifact checksum mismatch')
                        response['png_base64' if name == 'render.png' else 'blend_base64'] = base64.b64encode(data).decode()
                    return response
                if state['status'] in {'failed','cancelled','expired'}:
                    raise RuntimeError(state.get('error') or 'Lab render ' + state['status'])
                time.sleep(.5)
        except BaseException:
            client.cancel(job['job_id'])
            raise
        finally:
            self.active = None

    def stop(self):
        if self.active:
            client, identifier = self.active
            client.cancel(identifier)


class ApptainerConverter:
    """Short-lived, networkless converter with no host home or credentials.

    The host must permit Apptainer's network and PID isolation. Fail closed
    when isolation is unavailable; never retry conversion on the host Python.
    """
    def __init__(self, image, *, executable='apptainer'):
        self.image = str(Path(image).expanduser().resolve())
        self.executable = executable
        if not Path(self.image).is_file():
            raise ValueError('Configure an existing converter SIF image')
        self.lock = threading.Lock()

    def __call__(self, data, suffix):
        if suffix not in {'.pse','.cif','.mmcif','.pdb'} or len(data) > 50 * 1024**2:
            raise JobError('Invalid molecular upload')
        with self.lock, tempfile.TemporaryDirectory(prefix='blender-convert-') as directory:
            root = Path(directory)
            (root / ('input' + suffix)).write_bytes(data)
            command = [self.executable, 'exec', '--containall', '--cleanenv', '--no-eval',
                       '--net', '--network', 'none', '--no-home', '--no-mount', 'hostfs,bind-paths,home,cwd',
                       '--writable-tmpfs', '--bind', str(root) + ':/work', '--pwd', '/work',
                       self.image, '/usr/bin/python3', '/opt/blender_render/export.py',
                       '/work/input' + suffix, '/work/payload.json']
            process = subprocess.Popen(command, cwd=directory, env={'PATH':os.defpath, 'QT_QPA_PLATFORM':'offscreen'},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                process.wait(timeout=90)
                output = root / 'payload.json'
                if process.returncode:
                    raise JobError('Isolated conversion failed. Check the file and Apptainer isolation support.')
                # Treat the converter's output as hostile, including symlinks
                # and a writer racing the size check. Never follow a link from
                # the sandbox's bind mount into the gateway host filesystem.
                try:
                    descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(descriptor, 'rb') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise ValueError('Output is not a regular file')
                        payload = stream.read(50 * 1024**2 + 1)
                    if len(payload) > 50 * 1024**2:
                        raise ValueError('Output exceeds size limit')
                    return json.loads(payload)
                except (OSError, ValueError) as exc:
                    raise JobError('The isolated converter did not produce a valid structure.') from exc
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def serve_gateway(args):
    import uvicorn
    from .api import create_app, Downloads
    from .auth import github_provider
    from .jobs import JobStore, Policy
    from .mcp_server import create_http
    from .runtime import file_lock
    from .service import RenderService
    from .client import Client
    if args.host not in {'127.0.0.1','::1','localhost'}:
        raise ValueError('Run the gateway behind a TLS reverse proxy on loopback')
    if not args.public_url.startswith('https://'):
        raise ValueError('The lab OAuth gateway requires its public HTTPS URL')
    if not args.allowed_user:
        raise ValueError('Specify at least one permitted GitHub numeric user ID with --allowed-user')
    if any(not user.isdigit() for user in args.allowed_user):
        raise ValueError('Use stable numeric GitHub user IDs in the lab allowlist')
    credentials = json.loads(args.credentials.expanduser().read_text())
    required = ('GITHUB_CLIENT_ID','GITHUB_CLIENT_SECRET','BLENDER_RENDER_ADMIN_TOKEN',
                'BLENDER_RENDER_DOWNLOAD_KEY','BLENDER_RENDER_ENCRYPTION_KEY','BLENDER_RENDER_JWT_KEY')
    if any(not credentials.get(key) for key in required):
        raise ValueError('The gateway credentials file is incomplete')
    with file_lock(args.root / 'service.lock', blocking=False):
        store = JobStore(args.root, Policy(max_job_seconds=args.job_timeout))
        store.resume()
        backend = GatewayBackend(store, args.profile)
        service = RenderService(store, backend)
        provider = github_provider(store, client_id=credentials['GITHUB_CLIENT_ID'], client_secret=credentials['GITHUB_CLIENT_SECRET'],
            base_url=args.public_url, encryption_key=credentials['BLENDER_RENDER_ENCRYPTION_KEY'],
            jwt_key=credentials['BLENDER_RENDER_JWT_KEY'], allowed_users=set(args.allowed_user))
        downloads = Downloads(credentials['BLENDER_RENDER_DOWNLOAD_KEY'], args.public_url)
        converter = ApptainerConverter(args.converter_image)
        mcp = create_http(service, auth=provider, downloads=downloads, converter=converter)
        app = create_app(service, token=credentials['BLENDER_RENDER_ADMIN_TOKEN'], public_url=args.public_url,
            signing_key=credentials['BLENDER_RENDER_DOWNLOAD_KEY'], auth_provider=provider, mcp_server=mcp, converter=converter, tokens={})
        server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=args.port, log_level='warning'))
        app.state.shutdown = lambda: setattr(server, 'should_exit', True)
        try:
            server.run()
        finally:
            service.stop()
            store.close()
