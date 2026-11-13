"""Install and diagnose the optional local macOS notification helper."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from co_pymol.core.blender_notifications import load_preferences, save_preferences

BUNDLE_ID = "local.co-pymol.render-notifications"


def _native_architecture() -> str:
    """PyMOL's Intel Python may run under Rosetta on an Apple Silicon host."""
    machine = platform.machine()
    if sys.platform == "darwin" and machine == "x86_64":
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip() == "1":
            return "arm64"
    return machine


def build_notifier(app: Path) -> None:
    """Build from packaged source and replace only our own application bundle."""
    app = app.expanduser().absolute()
    source = Path(__file__).resolve().parents[1] / "core/render_notifier.m"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    receipt = app / "Contents/Resources/co-pymol-build.json"
    if app.is_symlink():
        raise ValueError(f"Notification app must not be a symlink: {app}")
    machine = _native_architecture()
    native = ["/usr/bin/arch", "-arm64"] if machine == "arm64" else []
    if app.exists():
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        if info.get("CFBundleIdentifier") != BUNDLE_ID:
            raise ValueError(f"Refusing to replace another application: {app}")
        binary = app / "Contents/MacOS/RenderNotifier"
        try:
            saved = json.loads(receipt.read_text())
            if (
                saved.get("source") == digest
                and saved.get("machine") == machine
                and os.access(binary, os.X_OK)
            ):
                subprocess.run(
                    [*native, "/usr/bin/codesign", "--verify", "--strict", str(app)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                return
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            pass  # A legacy or damaged build receipt needs a fresh helper.

    app.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".co-pymol-notifier-", dir=app.parent) as temporary:
        staging = Path(temporary) / app.name
        contents = staging / "Contents"
        binary = contents / "MacOS/RenderNotifier"
        binary.parent.mkdir(parents=True)
        with (contents / "Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleIdentifier": BUNDLE_ID,
                    "CFBundleExecutable": "RenderNotifier",
                    "CFBundleName": "Blender Renders",
                    "CFBundleDisplayName": "Blender Renders",
                    "CFBundlePackageType": "APPL",
                    "CFBundleVersion": "2",
                    "CFBundleShortVersionString": "2.0",
                    "LSMinimumSystemVersion": "11.0",
                    "LSUIElement": True,
                    "NSHighResolutionCapable": True,
                },
                stream,
            )
        subprocess.run(
            [
                *native,
                "/usr/bin/xcrun",
                "clang",
                "-fobjc-arc",
                "-fblocks",
                "-Wall",
                "-Wextra",
                "-Wno-unused-parameter",
                "-mmacosx-version-min=11.0",
                "-framework",
                "Cocoa",
                "-framework",
                "UserNotifications",
                str(source),
                "-o",
                str(binary),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        resources = contents / "Resources"
        resources.mkdir()
        (resources / receipt.name).write_text(
            json.dumps(
                {
                    "source": digest,
                    "machine": machine,
                }
            )
        )
        subprocess.run(
            [*native, "/usr/bin/codesign", "--force", "--sign", "-", str(staging)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        backup = Path(temporary) / "previous.app"
        if app.exists():
            app.rename(backup)
        try:
            staging.rename(app)
        except OSError:
            if backup.exists():
                backup.rename(app)
            raise


def _helper(app: str, action: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(Path(app) / "Contents/MacOS/RenderNotifier"), action],
        capture_output=True,
        text=True,
        timeout=15,
    )


def notification_status() -> dict:
    if sys.platform != "darwin":
        return {
            "status": "unsupported",
            "message": "Desktop alerts support macOS only.",
        }
    preferences = load_preferences()
    if preferences.get("enabled") is False:
        return {**preferences, "status": "disabled"}
    app = preferences.get("app")
    if not app or not (Path(app) / "Contents/MacOS/RenderNotifier").is_file():
        return {**preferences, "status": "not_installed"}
    try:
        result = _helper(app, "--status")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Helper status failed")
        settings = json.loads(result.stdout)
        if not isinstance(settings, dict) or "authorization" not in settings:
            raise ValueError("Helper returned invalid notification settings")
        return {**preferences, **settings, "status": "installed"}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {**preferences, "status": "unavailable", "error": str(exc)}


def _existing_app() -> Path:
    """Migrate the legacy per-render app path without changing the render config."""
    config = Path(
        os.environ.get(
            "CO_PYMOL_BLENDER_CONFIG",
            str(Path.home() / ".config/co-pymol/blender.json"),
        )
    ).expanduser()
    if config.is_file():
        data = json.loads(config.read_text())
        if not isinstance(data, dict):
            raise ValueError(f"{config} must contain a JSON object")
        app = data.get("notification_app")
        if isinstance(app, str) and app:
            candidate = Path(app).expanduser()
            if not candidate.is_absolute():
                raise ValueError("notification_app must be an absolute path")
            return candidate
    return Path.home() / "Applications/Blender Renders.app"


def setup_notifications(*, enable: bool = False) -> dict:
    """Prepare alerts during setup; preserve an opt-out on repeated client setup."""
    if sys.platform != "darwin":
        return notification_status()
    preferences = load_preferences()
    if preferences.get("enabled") is False and not enable:
        return {**preferences, "status": "disabled"}
    app = Path(preferences["app"]) if preferences.get("app") else _existing_app()
    try:
        build_notifier(app)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Cannot build/sign Blender Renders. Check Apple Command Line Tools: "
            + (exc.stderr or str(exc)).strip()
        ) from exc
    preferences.update(enabled=True, app=str(app))
    save_preferences(preferences)
    # Authorization belongs to the bundle identity, but click relaunches also
    # need the current app location registered (including when already allowed).
    registered = subprocess.run(
        ["/usr/bin/open", "-g", "-n", str(app), "--args", "--register"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if registered.returncode:
        raise RuntimeError(
            registered.stderr.strip() or "Cannot register notification app"
        )
    status = notification_status()
    authorization = status.get("authorization")
    if authorization == "denied":
        return {
            **status,
            "message": "Allow Blender Renders in System Settings > Notifications.",
        }
    if authorization in {"authorized", "provisional"}:
        message = "Blender render notifications are enabled."
        if not status.get("alerts_enabled"):
            message += (
                " Banners are disabled; check the app's macOS notification settings."
            )
        return {**status, "message": message}
    message = (
        "Permission is not confirmed. If no dialog is open, run "
        "`co-pymol notifications setup` from your desktop terminal."
    )
    if enable or not preferences.get("permission_requested"):
        # LaunchServices registers the stable application path and supplies the
        # desktop context that a detached rendering process may lack.
        result = subprocess.run(
            ["/usr/bin/open", "-n", str(app), "--args", "--setup"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode:
            raise RuntimeError(
                result.stderr.strip() or "Cannot open notification setup"
            )
        preferences["permission_requested"] = True
        save_preferences(preferences)
        message = (
            "Allow Blender Renders when macOS asks. "
            "Run `co-pymol notifications status` to check."
        )
    return {
        **status,
        **preferences,
        "status": "permission_pending",
        "message": message,
    }


def disable_notifications() -> dict:
    preferences = load_preferences()
    preferences["enabled"] = False
    save_preferences(preferences)
    return {"status": "disabled", "message": "Future completion alerts are disabled."}


def test_notification() -> dict:
    status = notification_status()
    if status.get("status") != "installed":
        return {**status, "sent": False}
    result = _helper(status["app"], "--test")
    return {
        "sent": result.returncode == 0,
        "status": "submitted" if result.returncode == 0 else "error",
        "message": "Test accepted by macOS; check Notification Center."
        if result.returncode == 0
        else result.stderr.strip(),
    }
