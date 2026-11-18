"""Repeated PyMOL previews through the real HTTP job API and controller process."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mol_blender import cluster_bridge, job_cli, pymol_bridge_plugin as plugin
from mol_blender.cluster.client import Client, RemoteError
from mol_blender.cluster.jsonio import read_json
from mol_blender.cluster.slurm import SubmissionUnknown
from mol_blender.config import cluster_root, save_config
from test_cluster_client import completed, live_service
from test_cluster_jobs import config, payload


@pytest.fixture
def previews(live_service, monkeypatch):
    client, store = live_service
    save_config({'local_python': sys.executable})
    monkeypatch.setattr(plugin, 'build_render_payload', lambda *a, **kw: payload())
    loaded = []
    context = SimpleNamespace(load_png=lambda path, **kw: loaded.append((Path(path), Path(path).read_bytes())))
    return store, context, loaded


def complete_on_submit(store, monkeypatch):
    submit = store.scheduler.submit
    def ready(directory, identifier):
        result = submit(directory, identifier)
        completed(store, identifier, color='green' if len(store.scheduler.submissions) % 2 else 'blue')
        return result
    monkeypatch.setattr(store.scheduler, 'submit', ready)


def receipts():
    return list((cluster_root() / 'pymol-previews').rglob('*.cluster.json'))


def test_repeated_previews_wait_display_and_start_distinct_jobs(previews, monkeypatch, tmp_path):
    store, cmd, loaded = previews
    complete_on_submit(store, monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'render.png').write_bytes(b'keep image')
    (tmp_path / 'render.cluster.json').write_text('keep unrelated receipt')
    for _ in range(2):
        assert plugin.bridge_render(cluster='lab', _self=cmd) is None
    assert len(set(store.scheduler.submissions)) == 2
    assert len(loaded) == 2 and loaded[0][1] != loaded[1][1]
    assert all(not path.parent.parent.exists() for path, _ in loaded)
    assert not receipts()
    assert (tmp_path / 'render.png').read_bytes() == b'keep image'
    assert (tmp_path / 'render.cluster.json').read_text() == 'keep unrelated receipt'


def test_async_and_timed_out_preview_resume_after_reload(previews):
    store, cmd, loaded = previews
    queued = plugin.bridge_render(cluster='lab', wait=0, _self=cmd)
    receipt = Path(queued['receipt'])
    request = read_json(receipt)['request_id']
    # No session/global variable carries the pending job across plugin reloads.
    importlib.reload(cluster_bridge)
    timed_out = plugin.bridge_render(cluster='lab', timeout=.01, _self=cmd)
    assert timed_out['waiting_stopped'] and timed_out['receipt'] == str(receipt)
    assert read_json(receipt)['request_id'] == request
    assert len(store.scheduler.submissions) == 1 and not loaded
    completed(store, queued['job_id'])
    assert plugin.bridge_render(cluster='lab', _self=cmd) is None
    assert len(store.scheduler.submissions) == 1 and len(loaded) == 1
    assert not receipt.parent.exists()


@pytest.mark.parametrize('accepted', [False, True])
def test_lost_upload_response_resumes_same_identity(previews, monkeypatch, accepted):
    store, cmd, loaded = previews
    # Keep real HTTP, but execute the controller in-process to inject a lost
    # transport response precisely before/after the gateway accepts the upload.
    monkeypatch.setattr(cluster_bridge, 'command', lambda args, **kw:
                        job_cli.execute(job_cli.parser().parse_args(args)))
    api = Client.api
    def lose_reply(self, method, path, data=None):
        if method == 'POST':
            if accepted:
                api(self, method, path, data)
            raise RemoteError('injected lost upload reply')
        return api(self, method, path, data)
    monkeypatch.setattr(Client, 'api', lose_reply)
    with pytest.raises(RemoteError, match='lost upload reply'):
        plugin.bridge_render(cluster='lab', _self=cmd)
    receipt, = receipts()
    request = read_json(receipt)['request_id']
    assert 'job_id' not in read_json(receipt)
    assert len(store.scheduler.submissions) == int(accepted)
    monkeypatch.setattr(Client, 'api', api)
    recovered = plugin.bridge_render(cluster='lab', wait=0, _self=cmd)
    assert read_json(receipt)['request_id'] == request
    assert len(store.scheduler.submissions) == 1
    completed(store, recovered['job_id'])
    assert plugin.bridge_render(cluster='lab', _self=cmd) is None
    assert len(store.scheduler.submissions) == 1 and not receipts()


def test_unknown_slurm_submission_never_starts_another_job(previews, monkeypatch):
    store, cmd, loaded = previews
    def uncertain(directory, identifier):
        store.scheduler.submissions.append(identifier)
        raise SubmissionUnknown('injected lost sbatch reply')
    monkeypatch.setattr(store.scheduler, 'submit', uncertain)
    first = plugin.bridge_render(cluster='lab', _self=cmd)
    again = plugin.bridge_render(cluster='lab', _self=cmd)
    assert first['status'] == again['status'] == 'submission_unknown'
    assert first['job_id'] == again['job_id']
    assert len(store.scheduler.submissions) == 1 and len(receipts()) == 1
    assert not loaded


def test_display_failure_refetches_same_job_to_new_download(previews, monkeypatch):
    store, cmd, loaded = previews
    complete_on_submit(store, monkeypatch)
    first = Path(plugin.bridge_render(cluster='lab', _self=SimpleNamespace(load_png=lambda *a, **kw: 0)))
    assert first.is_file() and len(receipts()) == 1
    assert plugin.bridge_render(cluster='lab', _self=cmd) is None
    assert len(store.scheduler.submissions) == 1
    assert loaded[0][0] != first
    assert not first.parent.parent.exists()


def test_interruption_after_download_does_not_block_recovery(previews, monkeypatch):
    store, cmd, loaded = previews
    complete_on_submit(store, monkeypatch)
    submit = cluster_bridge.submit
    def interrupted(*args, **kwargs):
        submit(*args, **kwargs)
        raise RuntimeError('injected interruption after fetching')
    monkeypatch.setattr(cluster_bridge, 'submit', interrupted)
    with pytest.raises(RuntimeError, match='after fetching'):
        plugin.bridge_render(cluster='lab', _self=cmd)
    receipt, = receipts()
    previous, = receipt.parent.glob('download-*/render.png')
    monkeypatch.setattr(cluster_bridge, 'submit', submit)
    assert plugin.bridge_render(cluster='lab', _self=cmd) is None
    assert len(store.scheduler.submissions) == 1 and loaded[0][0] != previous
    assert not receipt.parent.exists()


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'expired'])
def test_confirmed_terminal_failure_preserves_receipt_and_allows_next_preview(tmp_path, monkeypatch, status):
    monkeypatch.setenv('MOL_BLENDER_HOME', str(tmp_path))
    monkeypatch.setattr(plugin, 'build_render_payload', lambda *a, **kw: payload())
    outputs = []
    def terminal(data, **kw):
        output = kw['output']
        outputs.append(output)
        receipt = output.with_suffix('.cluster.json')
        receipt.write_text(json.dumps({'status': status, 'request_id': str(len(outputs))}))
        return {'status': status, 'receipt': str(receipt)}
    monkeypatch.setattr(cluster_bridge, 'submit', terminal)
    for _ in range(2):
        assert plugin.bridge_render(cluster='lab', _self=SimpleNamespace())['status'] == status
    assert outputs[0] != outputs[1]
    assert all(output.with_suffix('.cluster.json').is_file() for output in outputs)


def test_preview_lock_and_profile_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('MOL_BLENDER_HOME', str(tmp_path))
    with cluster_bridge.preview_output('lab') as first:
        with pytest.raises(RuntimeError, match='already active'):
            with cluster_bridge.preview_output('lab'):
                pytest.fail('concurrent preview acquired the same profile')
        with cluster_bridge.preview_output('other') as other:
            assert other != first
            cluster_bridge.finish_preview(other)
    # Reopening a command selects the same pending output, not a fresh request.
    probe = subprocess.run([sys.executable, '-c',
        "from mol_blender.cluster_bridge import preview_output\n"
        "with preview_output('lab') as output:\n    print(output)\n"],
        env=cluster_bridge.controller_environment(), capture_output=True, text=True, check=True)
    assert Path(probe.stdout.strip()) == first
    with cluster_bridge.preview_output('lab') as recovered:
        assert recovered == first
        cluster_bridge.finish_preview(recovered)
