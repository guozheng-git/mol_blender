from __future__ import annotations

from blender_render.pymol_bridge_plugin import try_load_preview


class LoadSuccessCmd:
    def __init__(self):
        self.calls = []

    def load_png(self, path, movie=1, quiet=1):
        self.calls.append((path, movie, quiet))


class LoadFailCmd:
    def load_png(self, path, movie=1, quiet=1):
        raise RuntimeError("preview failed")


def test_try_load_preview_success_path():
    cmd = LoadSuccessCmd()
    assert try_load_preview(cmd, "/tmp/render.png") is True
    assert cmd.calls == [("/tmp/render.png", 1, 1)]


def test_try_load_preview_failure_path():
    cmd = LoadFailCmd()
    assert try_load_preview(cmd, "/tmp/render.png") is False
