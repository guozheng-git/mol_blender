"""Notification onboarding and completion alerts without touching the desktop."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from co_pymol import cli
from co_pymol.core import blender_notifications as delivery
from co_pymol.utils import notifications


@pytest.fixture(autouse=True)
def isolated_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CO_PYMOL_BLENDER_CONFIG", raising=False)
    monkeypatch.setattr(notifications.sys, "platform", "darwin")


@pytest.fixture
def desktop(monkeypatch):
    calls = []
    settings = {"authorization": "not_determined", "alerts_enabled": False}

    def build(app):
        binary = app / "Contents/MacOS/RenderNotifier"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.touch()

    def run(command, **kwargs):
        calls.append(command)
        output = json.dumps(settings) if command[-1] == "--status" else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(notifications, "build_notifier", build)
    monkeypatch.setattr(notifications.subprocess, "run", run)
    return calls, settings


def test_setup_requests_permission_once_and_reuses_stable_app(desktop):
    calls, settings = desktop
    result = notifications.setup_notifications()
    assert result["status"] == "permission_pending"
    app = Path.home() / "Applications/Blender Renders.app"
    assert delivery.load_preferences()["app"] == str(app)
    assert calls[-1] == ["/usr/bin/open", "-n", str(app), "--args", "--setup"]

    repeated = notifications.setup_notifications()
    assert "If no dialog is open" in repeated["message"]
    assert sum(c[-1] == "--setup" for c in calls) == 1
    settings.update(authorization="authorized", alerts_enabled=True)
    assert notifications.setup_notifications()["authorization"] == "authorized"
    assert sum(c[-1] == "--setup" for c in calls) == 1
    assert calls[-2][-1] == "--register"


def test_setup_preserves_opt_out_until_explicit_enable(desktop):
    calls, _ = desktop
    notifications.disable_notifications()
    assert notifications.setup_notifications()["status"] == "disabled"
    assert calls == []
    assert (
        notifications.setup_notifications(enable=True)["status"] == "permission_pending"
    )
    assert delivery.load_preferences()["enabled"] is True


def test_denied_permission_does_not_reopen_prompt(desktop):
    calls, settings = desktop
    settings["authorization"] = "denied"
    result = notifications.setup_notifications(enable=True)
    assert result["authorization"] == "denied"
    assert "System Settings" in result["message"]
    assert all(c[-1] != "--setup" for c in calls)


def test_migrates_existing_app_without_changing_render_config(
    desktop, tmp_path, monkeypatch
):
    path = tmp_path / "backend.json"
    app = tmp_path / "existing/Blender Renders.app"
    config = {"notification_app": str(app), "profile": "my-lab"}
    path.write_text(json.dumps(config))
    monkeypatch.setenv("CO_PYMOL_BLENDER_CONFIG", str(path))
    assert notifications.setup_notifications()["app"] == str(app)
    assert json.loads(path.read_text()) == config


def test_test_notification_uses_no_render_or_image(desktop):
    calls, settings = desktop
    settings["authorization"] = "authorized"
    notifications.setup_notifications()
    calls.clear()
    assert notifications.test_notification()["sent"]
    assert [c[-1] for c in calls] == ["--status", "--test"]


def test_non_macos_setup_is_a_noop(monkeypatch, desktop):
    calls, _ = desktop
    monkeypatch.setattr(notifications.sys, "platform", "linux")
    assert notifications.setup_notifications()["status"] == "unsupported"
    assert not delivery.preferences_path().exists()
    assert not calls


def test_cli_setup_survives_optional_helper_failure(monkeypatch):
    def broken():
        raise RuntimeError("compiler missing")

    monkeypatch.setattr(notifications, "setup_notifications", broken)
    message = cli.prepare_notifications()
    assert "compiler missing" in message
    assert "notifications setup" in message
    assert "connection is configured" in message


def test_skip_notifications_still_installs_the_skill(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "prepare_notifications", lambda: pytest.fail("Notification setup skipped")
    )
    assert cli.main(["setup", "cursor", "--no-notifications"]) == 0
    assert not delivery.preferences_path().exists()
    root = tmp_path / ".cursor/skills"
    assert (root / "co-pymol-notifications/SKILL.md").is_file()


def test_completion_uses_current_preferences_and_global_disable(monkeypatch):
    calls = []
    monkeypatch.setattr(
        delivery,
        "notify_render",
        lambda app, **kwargs: calls.append(app) or {"sent": True},
    )
    delivery.save_preferences({"enabled": True, "app": "/new/Blender Renders.app"})
    assert delivery.completion_notification("/old/Legacy.app")["sent"]
    assert calls == ["/new/Blender Renders.app"]
    notifications.disable_notifications()
    assert delivery.completion_notification("/old/Legacy.app")["status"] == "disabled"
    assert len(calls) == 1


def test_legacy_app_works_without_global_preferences(monkeypatch):
    monkeypatch.setattr(delivery, "notify_render", lambda app, **kw: {"app": app})
    assert delivery.completion_notification("/legacy.app")["app"] == "/legacy.app"


def test_bad_preferences_or_helper_are_nonfatal(monkeypatch):
    delivery.preferences_path().parent.mkdir(parents=True)
    delivery.preferences_path().write_text("broken JSON")
    assert delivery.completion_notification("/test.app")["sent"] is False
    delivery.save_preferences({"enabled": True, "app": "/test.app"})

    def broken(*args, **kwargs):
        raise RuntimeError("Unexpected helper bug")

    monkeypatch.setattr(delivery, "notify_render", broken)
    result = delivery.completion_notification(None)
    assert result["sent"] is False
    assert "Unexpected helper bug" in result["error"]


def test_build_failure_preserves_existing_app(monkeypatch, tmp_path):
    import plistlib

    app = tmp_path / "Blender Renders.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": notifications.BUNDLE_ID}, stream)
    old = app / "old-binary"
    old.write_text("working old helper")

    def failed(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="build failed")

    monkeypatch.setattr(notifications, "_native_architecture", lambda: "x86_64")
    monkeypatch.setattr(notifications.subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        notifications.build_notifier(app)
    assert old.read_text() == "working old helper"


def test_build_never_overwrites_another_app(tmp_path):
    import plistlib

    app = tmp_path / "Other.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": "someone.else"}, stream)
    with pytest.raises(ValueError, match="another application"):
        notifications.build_notifier(app)


@pytest.mark.parametrize(
    ("arm64_available", "expected"), [("1", "arm64"), ("0", "x86_64")]
)
def test_native_architecture_detects_rosetta(monkeypatch, arm64_available, expected):
    monkeypatch.setattr(notifications.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        notifications.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, arm64_available, ""),
    )
    assert notifications._native_architecture() == expected


def test_build_launches_tools_as_native_arm64(monkeypatch, tmp_path):
    monkeypatch.setattr(notifications, "_native_architecture", lambda: "arm64")
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        if "/usr/bin/xcrun" in argv:
            Path(argv[-1]).write_bytes(b"native binary")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(notifications.subprocess, "run", run)
    app = tmp_path / "Blender Renders.app"
    notifications.build_notifier(app)
    assert all(argv[:2] == ["/usr/bin/arch", "-arm64"] for argv in commands)
    receipt = json.loads((app / "Contents/Resources/co-pymol-build.json").read_text())
    assert receipt["machine"] == "arm64"
