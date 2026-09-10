"""Exercise the actual runner's prerequisites-only path with no external calls."""
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from blender_render import config, engine, worker_ipc
from test_hpc_negotiation import profile


@pytest.mark.parametrize('available', [True, False])
def test_preflight_reports_ipc_without_limiting_durable_registry(tmp_path, monkeypatch, available):
    main = runpy.run_path(str(Path(__file__).parents[1]/'scripts/validate_c3_acceptance.py'))['main']
    root = tmp_path/'checkout'
    (root/'examples').mkdir(parents=True)
    (root/'examples/1sps_pose.pse').write_bytes(b'not converted in preflight')
    executable = tmp_path/'fake-runtime'
    executable.write_text('never executed')
    executable.chmod(0o700)
    doc = profile(tmp_path)
    doc['registry'] = str(tmp_path/('durable-registry-'+'x'*180))
    monkeypatch.setitem(main.__globals__, 'ROOT', root)
    monkeypatch.setattr(config, 'load_config', lambda:{'blender':str(executable),
        'pymol_python':str(executable), 'profiles':{'private':doc}})
    monkeypatch.setattr(engine, 'reviewed_checkout', lambda path:{'clean':True})
    def git(command, **kwargs):
        assert command == ['git', 'rev-parse', 'HEAD']
        return 'reviewed\n'
    monkeypatch.setattr(subprocess, 'check_output', git)
    output = tmp_path/'evidence'
    monkeypatch.setattr(sys, 'argv', ['acceptance', '--commit', 'reviewed', '--profile', 'private',
        '--output', str(output), '--prerequisites-only'])
    if not available:
        def deny(*a, **kw):
            raise PermissionError('no IPC storage')
        monkeypatch.setattr(worker_ipc.tempfile, 'mkdtemp', deny)
    assert main() == (0 if available else 2)
    report = json.loads((output/'acceptance.json').read_text())
    if available:
        assert report['worker_ipc']['durable_root_independent']
        assert report['worker_ipc']['socket_path_bytes'] <= worker_ipc.MAX_SOCKET_BYTES
        assert not report['blocked_reasons']
    else:
        assert report['blocked_reasons'][0].startswith('Worker IPC prerequisite:')
