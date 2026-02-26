from __future__ import annotations

from pathlib import Path

import pytest

import blender_render.pymol_bridge_plugin as plugin


class _DummyCode:
    co_filename = "/definitely/missing/pymol_bridge_plugin.py"


class _DummyBridgeRender:
    __code__ = _DummyCode()


def test_runner_path_uses_env_override(monkeypatch, tmp_path: Path):
    expected = tmp_path / "bridge_runner.py"
    expected.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    monkeypatch.setattr(plugin, "__file__", "/also/missing/pymol_bridge_plugin.py", raising=False)
    monkeypatch.setattr(plugin, "bridge_render", _DummyBridgeRender(), raising=False)
    monkeypatch.setenv("PYMOL_BRIDGE_ROOT", str(tmp_path))

    resolved = plugin._runner_path()
    assert resolved == expected


def test_runner_path_error_lists_checked_paths(monkeypatch):
    monkeypatch.setattr(plugin, "__file__", "/missing/a.py", raising=False)
    monkeypatch.setattr(plugin, "bridge_render", _DummyBridgeRender(), raising=False)
    monkeypatch.delenv("PYMOL_BRIDGE_ROOT", raising=False)

    with pytest.raises(RuntimeError, match="Could not locate bridge_runner.py"):
        plugin._runner_path()
