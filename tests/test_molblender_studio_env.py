"""Real installer processes and file locks, with harmless installation callbacks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/molblender_studio/skills/molblender-studio/scripts"
sys.path.insert(0, str(SCRIPTS))
import studio_env  # noqa: E402


WORKER = '''
import os
from pathlib import Path
import sys
import time
import studio_env

home = studio_env.home()
mode = sys.argv[1]
sleep = time.sleep
def waiting(seconds):
    (home / ("waiting-" + str(os.getpid()))).touch()
    sleep(seconds)
studio_env.time.sleep = waiting

def install(*args):
    # Exclusive creation fails immediately if two installers overlap.
    with (home / "active").open("x"):
        with (home / "installs").open("a") as log:
            log.write(mode + "\\n")
        (home / "entered").touch()
        deadline = time.monotonic() + 15
        while not (home / "release").exists():
            if time.monotonic() > deadline:
                raise RuntimeError("test did not release installer")
            sleep(.01)
        (home / "ready").touch()
    (home / "active").unlink()
    return Path(sys.executable)

studio_env.renderer_status = lambda: {"render_ready": (home / "ready").exists(), "state": {}}
studio_env.setup_pymol = install
studio_env.refresh_renderer = install
if mode == "setup":
    studio_env.setup("pymol")
else:
    studio_env._refresh_once()
'''


def wait_for(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, "worker did not reach expected state"
        time.sleep(.01)


@pytest.fixture
def installers(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLBLENDER_STUDIO_HOME", str(tmp_path))
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    processes = []
    popen = subprocess.Popen

    def start(mode, **kwargs):
        process = popen([sys.executable, str(worker), mode],
                        env=dict(os.environ, PYTHONPATH=str(SCRIPTS)), **kwargs)
        processes.append(process)
        return process

    yield start
    (tmp_path / "release").touch()
    for process in processes:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("first_mode", ["refresh", "setup", "background"])
def test_setup_and_refresh_share_exclusive_ownership(installers, tmp_path, monkeypatch, first_mode):
    if first_mode == "background":
        children = []

        def launch(command, **kwargs):
            assert command[-3:] == ["setup", "--only", "pymol"]
            children.append(installers("setup", **kwargs))
            return children[-1]

        monkeypatch.setattr(studio_env.subprocess, "Popen", launch)
        result = studio_env.setup("pymol", background=True)
        first = children[0]
        assert result["pid"] == first.pid
        assert studio_env.setup("pymol", background=True)["already_running"]
        assert len(children) == 1
    else:
        first = installers(first_mode)
    wait_for(lambda: (tmp_path / "entered").exists())

    # Even without progress metadata, the mutex must exclude another installer.
    # This covers the window before an installer has published its PID.
    (tmp_path / "setup.lock").unlink()
    second = installers("refresh")
    wait_for(lambda: (tmp_path / f"waiting-{second.pid}").exists() or second.poll() is not None)
    assert second.poll() is None
    assert (tmp_path / "installs").read_text().splitlines() == ["refresh" if first_mode == "refresh" else "setup"]
    (tmp_path / "release").touch()
    assert first.wait(timeout=10) == second.wait(timeout=10) == 0
    # The waiting refresher rechecks readiness after acquiring ownership.
    assert len((tmp_path / "installs").read_text().splitlines()) == 1
    assert not (tmp_path / "setup.lock").exists()


def test_failed_install_releases_guard_and_cleans_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLBLENDER_STUDIO_HOME", str(tmp_path))
    with pytest.raises(RuntimeError, match="installation failed"):
        with studio_env._setup_guard("render"):
            raise RuntimeError("installation failed")
    assert studio_env.running_setup() is None
    assert not (tmp_path / "setup.lock").exists()
    with studio_env._setup_guard("pymol", wait=False) as info:
        assert info["pid"] == os.getpid()
        assert json.loads((tmp_path / "setup.lock").read_text())["components"] == "pymol"


@pytest.fixture
def legacy_external_renderer(tmp_path, monkeypatch):
    monkeypatch.setenv("MOLBLENDER_STUDIO_HOME", str(tmp_path))
    current = {"external_renderer": True, "blender_render": str(tmp_path / "bin/blender-render"),
               "blender_render_home": str(tmp_path / "blender-render"), "pymol_python": sys.executable}
    (tmp_path / "state.json").write_text(json.dumps(current))
    return current


@pytest.mark.parametrize("components,background", [(None, False), ("render", False), ("all", False),
                                                   ("render", True), ("all", True)])
def test_legacy_external_renderer_requires_reconfiguration(legacy_external_renderer, monkeypatch,
                                                          components, background):
    def unexpected(*args, **kwargs):
        pytest.fail("Re-register the external renderer before starting any installation")
    monkeypatch.setattr(studio_env, "setup_pymol", unexpected)
    monkeypatch.setattr(studio_env.subprocess, "Popen", unexpected)
    with pytest.raises(studio_env.SetupError, match="setup --use-existing.*--mol-blender-home"):
        if components is None:
            studio_env.renderer_env()
        else:
            studio_env.setup(components, background=background)
    assert studio_env.state() == legacy_external_renderer


def test_reconfigure_legacy_external_renderer_preserves_runtime_paths(legacy_external_renderer, tmp_path):
    executable = tmp_path / "bin/mol-blender"
    executable.parent.mkdir()
    executable.touch()
    original_config = legacy_external_renderer["blender_render_home"]
    studio_env.use_existing(str(executable), original_config)
    assert studio_env.setup("render")["render_ready"]
    configured, env = studio_env.renderer_env()
    assert configured == executable
    assert env["MOL_BLENDER_HOME"] == original_config
    assert studio_env.pymol_python() == Path(legacy_external_renderer["pymol_python"])
