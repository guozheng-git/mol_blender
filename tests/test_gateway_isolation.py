import json
from pathlib import Path

import pytest

from blender_render.gateway import ApptainerConverter
from blender_render.jobs import JobError


@pytest.mark.parametrize('output_kind', ['symlink', 'fifo', 'invalid_json'])
def test_converter_rejects_host_links_and_nonregular_output(monkeypatch, tmp_path, output_kind):
    import blender_render.gateway as gateway
    image = tmp_path / 'converter.sif'
    image.write_bytes(b'test image')
    private = tmp_path / 'private.json'
    private.write_text(json.dumps({'private':'must not leave host'}))

    class Process:
        pid, returncode = 123456789, 0

        def __init__(self, command, **kwargs):
            assert '--containall' in command and '--cleanenv' in command
            assert command[command.index('--network') + 1] == 'none'
            assert 'HOME' not in kwargs['env']
            output = Path(kwargs['cwd']) / 'payload.json'
            if output_kind == 'symlink':
                output.symlink_to(private)
            elif output_kind == 'fifo':
                gateway.os.mkfifo(output)
            else:
                output.write_text('not JSON')

        def wait(self, timeout):
            assert timeout == 90

    killed = []
    monkeypatch.setattr(gateway.subprocess, 'Popen', Process)
    monkeypatch.setattr(gateway.os, 'killpg', lambda pid, signal: killed.append(pid))
    with pytest.raises(JobError, match='valid structure'):
        ApptainerConverter(image)(b'untrusted session', '.pse')
    assert killed == [Process.pid]


def test_modal_startup_failure_stops_deployment(monkeypatch):
    pytest.importorskip('modal')
    from blender_render import modal_app
    stopped = []
    def fail():
        raise OSError('persistent state unavailable')
    monkeypatch.setattr(modal_app, 'create_gateway', fail)
    monkeypatch.setattr(modal_app, 'stop_current_app', lambda: stopped.append(True))
    with pytest.raises(OSError, match='persistent state unavailable'):
        modal_app.gateway.get_raw_f()()
    assert stopped == [True]
