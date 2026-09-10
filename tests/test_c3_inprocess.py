"""In-process replacements for the two inherited A/B process contracts.

These prove snapshot/persistence behavior, not a fresh-interpreter release gate.
"""
import json
from types import SimpleNamespace
from test_developer import style


def test_source_snapshot_contract_with_injected_git(tmp_path, monkeypatch):
    from blender_render import developer
    from test_developer import test_snapshot_tracks_python_and_assets
    monkeypatch.setattr(developer.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    test_snapshot_tracks_python_and_assets(tmp_path)


def test_catalog_persistence_with_inprocess_inspection(tmp_path, monkeypatch, style):
    from blender_render import authoring
    from test_developer import test_save_restart_reuse_and_revision
    import subprocess
    def inspect(command, **kwargs):
        return json.dumps(authoring.inspect_preset('user/macro', tmp_path))
    monkeypatch.setattr(subprocess, 'check_output', inspect)
    test_save_restart_reuse_and_revision(style, tmp_path)
