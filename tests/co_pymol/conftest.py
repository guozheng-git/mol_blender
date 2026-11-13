"""Shared isolation for every co_pymol test module."""

import pytest


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path_factory):
    # Detached render helpers inherit this environment. Keep their settings and
    # outputs separate from the user's real configuration.
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CO_PYMOL_BLENDER_CONFIG", raising=False)
