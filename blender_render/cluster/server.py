"""Optional authenticated HTTP gateway. No rendering occurs in the web process."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path
import subprocess

from .auth import authenticate, users
from .common import PROTOCOL, decode_json, digest_file
from .slurm import Slurm
from .store import Conflict, NotFound, Quota, Store


def create_app(config, *, scheduler=None):
    # Optional dependencies are imported only when this service is selected.
    from starlette.applications import Starlette
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route
    from ..config import VERSION
    from ..engine import generate_manifest

    if digest_file(config.image) != config.image_sha256:
        raise ValueError('SIF checksum mismatch; refuse to start with a different environment')
    users(config.tokens_file)
    identity = generate_manifest(Path(__file__).resolve().parents[1])['engine_build']
    store = Store(config, scheduler or Slurm(config), identity)

    async def maintenance():
        while True:
            try:
                await run_in_threadpool(store.sweep)
            except Exception:
                logging.getLogger(__name__).exception('Cluster spool maintenance failed')
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(maintenance())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def guarded(endpoint):
        async def call(request):
            try:
                owner = await run_in_threadpool(authenticate, config.tokens_file,
                                                request.headers.get('authorization', ''))
                if owner is None:
                    return JSONResponse({'error': 'Authentication required'}, status_code=401,
                                        headers={'WWW-Authenticate': 'Bearer', 'Cache-Control': 'no-store'})
                response = await endpoint(request, owner)
                response.headers['Cache-Control'] = 'no-store'
                response.headers['X-Content-Type-Options'] = 'nosniff'
                return response
            except NotFound:
                return JSONResponse({'error': 'Job or artifact not found'}, status_code=404)
            except Conflict as exc:
                return JSONResponse({'error': str(exc)}, status_code=409)
            except Quota as exc:
                return JSONResponse({'error': str(exc)}, status_code=429)
            except (ValueError, RecursionError) as exc:
                return JSONResponse({'error': str(exc)[:1000]}, status_code=400)
            except (OSError, subprocess.SubprocessError):
                return JSONResponse({'error': 'Cluster service unavailable; retry status, not a new submission'}, status_code=503)
        return call

    async def info(request, owner):
        return JSONResponse({'protocol': PROTOCOL, 'version': VERSION, 'engine_build': identity,
                             'limits': vars(config.limits)})

    async def submit(request, owner):
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            return JSONResponse({'error': 'Use application/json with exported structure data'}, status_code=415)
        if request.headers.get('content-encoding', 'identity') != 'identity':
            return JSONResponse({'error': 'Compressed request bodies are not accepted'}, status_code=415)
        if int(request.headers.get('content-length', '0')) > config.limits.max_request_bytes:
            return JSONResponse({'error': 'Request too large'}, status_code=413)
        data = bytearray()
        try:
            async with asyncio.timeout(30):
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data) > config.limits.max_request_bytes:
                        return JSONResponse({'error': 'Request too large'}, status_code=413)
        except TimeoutError:
            return JSONResponse({'error': 'Upload timed out'}, status_code=408)
        result = await run_in_threadpool(store.submit, owner, decode_json(data))
        return JSONResponse(result, status_code=202)

    async def listing(request, owner):
        return JSONResponse(await run_in_threadpool(store.list_jobs, owner))

    async def status(request, owner):
        return JSONResponse(await run_in_threadpool(store.status, owner, request.path_params['identifier']))

    async def recover(request, owner):
        import hashlib
        from .common import job_id
        identifier = hashlib.sha256((owner + '\0' + job_id(request.path_params['request_id'])).encode()).hexdigest()[:32]
        return JSONResponse(await run_in_threadpool(store.status, owner, identifier))

    async def cancel(request, owner):
        return JSONResponse(await run_in_threadpool(store.cancel, owner, request.path_params['identifier']))

    async def delete(request, owner):
        return JSONResponse(await run_in_threadpool(store.delete, owner, request.path_params['identifier']))

    async def artifact(request, owner):
        handle, metadata = await run_in_threadpool(store.open_artifact, owner,
            request.path_params['identifier'], request.path_params['name'])
        def chunks():
            try:
                while chunk := handle.read(1024**2):
                    yield chunk
            finally:
                handle.close()
        return StreamingResponse(chunks(), media_type='application/octet-stream', headers={
            'Content-Length': str(metadata['bytes']), 'X-Content-SHA256': metadata['sha256'],
            'Content-Disposition': 'attachment; filename="' + request.path_params['name'] + '"'})

    app = Starlette(routes=[
        Route('/v1/info', guarded(info)),
        Route('/v1/requests/{request_id}', guarded(recover)),
        Route('/v1/jobs', guarded(submit), methods=['POST']),
        Route('/v1/jobs', guarded(listing), methods=['GET']),
        Route('/v1/jobs/{identifier}', guarded(status), methods=['GET']),
        Route('/v1/jobs/{identifier}', guarded(delete), methods=['DELETE']),
        Route('/v1/jobs/{identifier}/cancel', guarded(cancel), methods=['POST']),
        Route('/v1/jobs/{identifier}/artifacts/{name}', guarded(artifact)),
    ], lifespan=lifespan)
    app.state.store = store
    return app
