import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] /
                       'plugins/structure-studio/skills/structure-studio/scripts'))
import studio_env


def test_missing_uv_cannot_bootstrap_packages(monkeypatch):
    monkeypatch.setattr(studio_env.shutil, 'which', lambda _: None)
    monkeypatch.setattr(studio_env, '_run', lambda *_a, **_k: pytest.fail('must not install'))
    with pytest.raises(studio_env.SetupError, match='Install uv explicitly'):
        studio_env.uv()


def test_import_probe_reports_libraries_without_installing(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, '', 'libGL.so.1: cannot open shared object file')
    monkeypatch.setattr(studio_env, '_run', run)
    with pytest.raises(studio_env.SetupError, match='administrator.*libgl1'):
        studio_env._import_check(Path('/managed/python'), 'import bpy', 'Blender')
    assert calls == [[Path('/managed/python'), '-c', 'import bpy']]


def test_setup_refuses_aliased_or_global_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('STRUCTURE_STUDIO_HOME', str(tmp_path / 'studio'))
    (tmp_path / 'studio').mkdir()
    (tmp_path / 'studio/render').symlink_to(tmp_path)
    with pytest.raises(studio_env.SetupError, match='inside'):
        studio_env._venv(tmp_path / 'studio/render')
    (tmp_path / 'studio/pymol').mkdir()
    with pytest.raises(studio_env.SetupError, match='non-isolated'):
        studio_env._venv(tmp_path / 'studio/pymol')
