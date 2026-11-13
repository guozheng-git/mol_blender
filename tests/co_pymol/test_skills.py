"""Bundled skill installation, upgrades and preservation of user content."""

from __future__ import annotations

import json
import subprocess

import pytest

from co_pymol import cli
from co_pymol.utils import skills


@pytest.fixture(autouse=True)
def no_desktop_setup(monkeypatch):
    monkeypatch.setattr(cli, "prepare_notifications", lambda: "Notifications: test")


@pytest.mark.parametrize(
    ("client", "root"),
    [("codex", ".agents"), ("cursor", ".cursor"), ("claude", ".claude")],
)
def test_setup_installs_complete_skill_for_selected_client_only(
    monkeypatch, tmp_path, client, root
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    assert cli.main(["setup", client]) == 0

    installed = tmp_path / root / "skills"
    notification = installed / skills.NOTIFICATION_SKILL / "SKILL.md"
    assert (
        notification.read_bytes()
        == (skills.SKILLS / skills.NOTIFICATION_SKILL / "SKILL.md").read_bytes()
    )
    # The remote-backend setup skill was retired with the separate backend.
    assert [p.name for p in installed.iterdir()] == [skills.NOTIFICATION_SKILL]
    for other in {".agents", ".cursor", ".claude"} - {root}:
        assert not (tmp_path / other / "skills").exists()


def test_failed_client_setup_does_not_install_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/codex")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "failed"),
    )
    assert cli.main(["setup", "codex"]) == 1
    assert not skills.skill_directory("codex").exists()


# A multi-file bundle exercises the installer that bundled skills share.
SKILL_NAME = "test-skill"
SKILL_FILES = ("SKILL.md", "references/access.md")
BUNDLE = {}


def install(client: str) -> str:
    return skills._install_skill(client, SKILL_NAME, BUNDLE["source"], SKILL_FILES)


@pytest.fixture
def installed_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    source = tmp_path / "bundled"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text("entry point")
    (source / "references/access.md").write_text("access")
    monkeypatch.setitem(BUNDLE, "source", source)
    install("cursor")
    return source, skills.skill_directory("cursor", SKILL_NAME)


def test_repeated_install_does_not_rewrite_files(installed_skill):
    _, destination = installed_skill
    before = {p: p.stat().st_mtime_ns for p in destination.rglob("*") if p.is_file()}
    assert "Already installed" in install("cursor")
    assert {p: p.stat().st_mtime_ns for p in before} == before


def test_update_refreshes_owned_files_and_preserves_extra_files(installed_skill):
    source, destination = installed_skill
    (source / "SKILL.md").write_text("updated guidance")
    (source / "references/access.md").write_text("updated access")
    notes = destination / "notes.txt"
    notes.write_text("my notes")

    install("cursor")

    assert (destination / "SKILL.md").read_text() == "updated guidance"
    assert (destination / "references/access.md").read_text() == "updated access"
    assert notes.read_text() == "my notes"
    assert "Already installed" in install("cursor")


def test_local_edit_prevents_partial_bundle_update(installed_skill):
    source, destination = installed_skill
    original = (destination / "SKILL.md").read_bytes()
    (source / "SKILL.md").write_text("new entry point")
    (destination / "references/access.md").write_text("my login workflow")

    assert "Skipped" in install("cursor")

    assert (destination / "SKILL.md").read_bytes() == original
    assert (destination / "references/access.md").read_text() == "my login workflow"


def test_unowned_skill_is_not_overwritten(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    destination = skills.skill_directory("claude")
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("a manually maintained skill")

    assert "Skipped" in skills.install_notification_skill("claude")
    assert list(destination.iterdir()) == [destination / "SKILL.md"]
    assert (destination / "SKILL.md").read_text() == "a manually maintained skill"


@pytest.mark.parametrize("receipt", ["invalid json", "[]", '"not a mapping"'])
def test_invalid_receipt_preserves_installation(installed_skill, receipt):
    source, destination = installed_skill
    original = (destination / "SKILL.md").read_bytes()
    (source / "SKILL.md").write_text("new entry point")
    (destination / ".co-pymol-installed.json").write_text(receipt)
    assert "Skipped" in install("cursor")
    assert (destination / "SKILL.md").read_bytes() == original


@pytest.mark.parametrize("relative", [".", "SKILL.md", "references"])
def test_does_not_follow_skill_symlinks(installed_skill, tmp_path, relative):
    _, destination = installed_skill
    target = destination / relative
    moved = tmp_path / "user-maintained"
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=moved.is_dir())

    assert "symlink" in install("cursor")
    assert target.is_symlink()


def test_interrupted_update_can_resume(installed_skill):
    source, destination = installed_skill
    (source / "SKILL.md").write_text("new entry point")
    (source / "references/access.md").write_text("new access")
    # A previous attempt wrote one file but not the reference or receipt.
    (destination / "SKILL.md").write_text("new entry point")
    install("cursor")
    assert (destination / "references/access.md").read_text() == "new access"
    receipt = json.loads((destination / ".co-pymol-installed.json").read_text())
    assert set(receipt) == set(SKILL_FILES)
    assert "Already installed" in install("cursor")
