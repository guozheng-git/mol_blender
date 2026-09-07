from fastapi.testclient import TestClient
from blender_render.worker_http import create_worker


def test_host_cancellation_interrupts_before_waiting_for_socket_lock(monkeypatch):
    import threading
    from blender_render.service import HostBackend
    from blender_render import render_server
    entered, interrupted, cancelled = (threading.Event() for _ in range(3))
    socket_lock = threading.Lock()
    class Worker:
        def interrupt(self):
            interrupted.set()
        def stop(self):
            with socket_lock:
                pass
    def blocked_render(**kwargs):
        with socket_lock:
            entered.set()
            assert interrupted.wait(2), 'Cancellation waited for the render socket lock'
        return {'status':'cancelled'}
    monkeypatch.setattr(render_server, '_process_render_request', blocked_render)
    backend = HostBackend.__new__(HostBackend)
    backend.worker, backend.timeout = Worker(), 600
    results = []
    task = threading.Thread(target=lambda:results.append(backend.render({}, 'a'*32, cancelled.is_set)))
    task.start()
    assert entered.wait(1)
    cancelled.set()
    task.join(timeout=1)
    assert not task.is_alive() and results == [{'status':'cancelled'}]
    backend.stop()


def test_worker_auth_idempotence_and_process_cleanup(tmp_path):
    stopped = []
    roots = []
    class Backend:
        def __init__(self, root):
            roots.append(root)
        def render(self, payload, identifier, cancelled):
            assert payload['keep_temp'] is False
            return {'status':'completed','png_base64':'test'}
        def stop(self):
            stopped.append(True)
    with TestClient(create_worker('x'*40, backend_factory=Backend)) as client:
        assert client.get('/health').status_code == 401
        headers={'Authorization':'Bearer '+'x'*40}
        job='a'*32
        assert client.post('/render/'+job,json={},headers=headers).status_code == 200
        assert stopped == [True] and not roots[0].exists()
        assert client.post('/render/'+job,json={},headers=headers).status_code == 409
        cancelled='b'*32
        assert client.post('/cancel/'+cancelled,headers=headers).status_code == 200
        assert client.post('/render/'+cancelled,json={},headers=headers).status_code == 409
