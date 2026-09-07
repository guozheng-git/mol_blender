"""Transient HTTP GPU worker; molecular data never becomes Function I/O."""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import tempfile
import threading
import time
import traceback
from pathlib import Path
from fastapi import FastAPI, Request


def create_worker(token: str, *, timeout: int = 600, backend_factory=None):
    from fastapi.responses import JSONResponse
    from .api import BoundedBody
    from .service import HostBackend
    if len(token) < 32:
        raise ValueError('A worker token is required')
    app = FastAPI()
    flags = {}
    busy = threading.Lock()
    factory = backend_factory or (lambda root: HostBackend(root,
        blender='/opt/runtime/blender-5.0.1-linux-x64/blender', timeout=timeout-20))

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer '+token):
            return JSONResponse({'error':'Unauthorized'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    async def health():
        return {'ready':True}

    @app.post('/cancel/{job_id}')
    async def cancel(job_id: str):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            return JSONResponse({'error':'Invalid job ID'}, status_code=400)
        flags.setdefault(job_id, (threading.Event(), time.monotonic()))[0].set()
        return {'cancel_requested':True}

    @app.post('/render/{job_id}')
    async def render(job_id: str, request: Request):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            return JSONResponse({'error':'Invalid job ID'}, status_code=400)
        if job_id in flags:
            return JSONResponse({'error':'Job already attempted or cancelled'}, status_code=409)
        if not busy.acquire(blocking=False):
            return JSONResponse({'error':'Worker busy'}, status_code=429)
        flag = threading.Event()
        flags[job_id] = (flag, time.monotonic())
        task = None
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            payload['keep_temp'] = False
            payload['job_timeout_seconds'] = timeout-20
            def run():
                with tempfile.TemporaryDirectory(prefix='blender-render-worker-') as root:
                    backend = factory(Path(root))
                    try:
                        return backend.render(payload, job_id, flag.is_set)
                    finally:
                        backend.stop()
            task = asyncio.create_task(asyncio.to_thread(run))
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except Exception as exc:
            # Only code locations and fixed diagnostic categories may enter
            # provider logs. Exception messages can contain molecular data.
            frames = [(Path(f.filename).name, f.lineno, f.name)
                      for f in traceback.extract_tb(exc.__traceback__)]
            hints = [hint for hint in ('No module named', 'exited during startup',
                'timed out', 'GPU', 'CIF', 'Permission denied', 'No such file')
                if hint.lower() in str(exc).lower()]
            logging.getLogger(__name__).error('Worker failure type=%s frames=%s hints=%s',
                type(exc).__name__, frames, hints)
            return JSONResponse({'error':'Render failed or timed out'}, status_code=500)
        finally:
            flag.set()
            if task and not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)
                except Exception:
                    pass
            if task is None or task.done():
                busy.release()
            for key, (_, created) in list(flags.items()):
                if created < time.monotonic()-3600:
                    flags.pop(key, None)

    app.add_middleware(BoundedBody, limit=64*1024**2)
    return app


class HTTPBackend:
    """One reserved job, direct HTTPS delivery, and no automatic render retry."""
    paid = True

    def __init__(self, url, token, timeout=600, *, proxy_key='', proxy_secret=''):
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout
        self.headers = {'Authorization':'Bearer '+token, 'Modal-Key':proxy_key, 'Modal-Secret':proxy_secret}
        self.active = None

    def render(self, request, identifier, cancelled):
        import concurrent.futures
        import httpx
        with httpx.Client(headers=self.headers, timeout=self.timeout+30) as client:
            deadline = time.monotonic()+180
            while True:
                if cancelled():
                    raise RuntimeError('Cancelled')
                response = client.get(self.url+'/health', timeout=15)
                if response.status_code == 200:
                    break
                if response.status_code != 503 or time.monotonic() >= deadline:
                    response.raise_for_status()
                time.sleep(1)
            self.active = identifier
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(client.post, self.url+'/render/'+identifier, json=request)
                    while True:
                        try:
                            response = pending.result(timeout=.25)
                            response.raise_for_status()
                            return response.json()
                        except concurrent.futures.TimeoutError:
                            if cancelled():
                                self.stop()
                                raise RuntimeError('Cancelled')
            finally:
                self.active = None

    def stop(self):
        if self.active:
            import httpx
            try:
                httpx.post(self.url+'/cancel/'+self.active, headers=self.headers, timeout=10)
            except httpx.HTTPError:
                pass
