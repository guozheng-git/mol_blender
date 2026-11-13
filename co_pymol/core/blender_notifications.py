"""Optional local macOS notifications with a persistent image click target."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path


def preferences_path() -> Path:
    return Path.home() / ".config/co-pymol/notifications.json"


def load_preferences() -> dict:
    path = preferences_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if "enabled" in data and type(data["enabled"]) is not bool:
        raise ValueError("Notification enabled must be a boolean")
    if "app" in data and (
        not isinstance(data["app"], str) or not Path(data["app"]).is_absolute()
    ):
        raise ValueError("Notification app must be an absolute path")
    return data


def save_preferences(data: dict) -> None:
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def completion_notification(app: str | None, **kwargs) -> dict:
    """Resolve current preferences at delivery time, including for saved jobs.

    Keep this entire optional operation outside the render failure boundary.
    Reading an invalid preference file or an unexpected helper exception must
    never downgrade a verified image to a render failure.
    """
    try:
        preferences = load_preferences()
        if preferences.get("enabled") is False:
            return {"sent": False, "status": "disabled", "error": None}
        # A managed app takes precedence over paths saved in older requests.
        app = preferences.get("app") or app
        if not app:
            return {"sent": False, "status": "not_configured", "error": None}
        return notify_render(app, **kwargs)
    except Exception as exc:
        return {"sent": False, "status": "error", "error": str(exc)}


def notify_render(
    app: str,
    *,
    identifier: str,
    title: str,
    message: str,
    image: str | Path,
    directory: str | Path,
) -> dict:
    """Post through the signed helper; never open an image without a click.

    A notification problem must not turn a successful render into a failed job.
    No shell is involved: paths and user-supplied names remain plain arguments.
    """
    binary = Path(app) / "Contents/MacOS/RenderNotifier"
    image = Path(image).absolute()
    directory = Path(directory).absolute()
    target = image if image.is_file() else directory
    if not target.exists():
        return {"sent": False, "error": "Neither the render nor its folder exists"}
    try:
        result = subprocess.run(
            [
                str(binary),
                "--notify",
                identifier,
                title,
                message,
                str(target),
                str(directory),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "sent": result.returncode == 0,
            # Success means accepted by macOS, not proof a banner was seen.
            "status": "submitted" if result.returncode == 0 else "error",
            "target": str(target),
            "error": (
                result.stderr.strip()
                or f"Notification helper exited with status {result.returncode}"
            )
            if result.returncode
            else None,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"sent": False, "target": str(target), "error": str(exc)}
