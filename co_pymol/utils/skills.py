"""Install bundled agent guidance without replacing locally edited skills."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

NOTIFICATION_SKILL = "co-pymol-notifications"
SKILLS = Path(__file__).resolve().parents[1] / "skills"
_RECEIPT = ".co-pymol-installed.json"


def skill_directory(client: str, name: str = NOTIFICATION_SKILL) -> Path:
    """The selected client's documented user-level skill directory."""
    root = {"codex": ".agents", "cursor": ".cursor", "claude": ".claude"}[client]
    return Path.home() / root / "skills" / name


def _write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def install_notification_skill(client: str) -> str:
    source = SKILLS / NOTIFICATION_SKILL
    return _install_skill(client, NOTIFICATION_SKILL, source, ("SKILL.md",))


def _install_skill(
    client: str, skill_name: str, source: Path, files: tuple[str, ...]
) -> str:
    """Copy the bundled skill; update only files matching our last receipt.

    Check the whole bundle before writing so a local edit cannot produce mixed
    versions of the entry point and references. Unknown files are left alone.
    An interrupted update can resume because already-current files also match.
    """
    destination = skill_directory(client, skill_name)
    desired = {name: (source / name).read_bytes() for name in files}
    receipt = destination / _RECEIPT

    def skipped(reason: str) -> str:
        return (
            f"Skipped {skill_name} skill at {destination}: {reason}. "
            f"Existing files were preserved; bundled guidance: {source}"
        )

    # Do not follow a skill or reference symlink into user-maintained content.
    paths = [destination, receipt, *(destination / name for name in files)]
    for path in paths:
        current = path
        while current != destination.parent:
            if current.is_symlink():
                return skipped(f"symlink at {current}")
            current = current.parent
    if destination.exists() and not destination.is_dir():
        return skipped("the destination is not a directory")

    previous = {}
    if receipt.exists():
        try:
            previous = json.loads(receipt.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return skipped("unreadable installation receipt")
        if not isinstance(previous, dict):
            return skipped("invalid installation receipt")

    pending = {}
    for name, content in desired.items():
        path = destination / name
        if path.parent.exists() and not path.parent.is_dir():
            return skipped(f"{path.parent} is not a directory")
        if path.exists():
            if not path.is_file():
                return skipped(f"{name} is not a regular file")
            existing = path.read_bytes()
            if existing == content:
                continue
            if hashlib.sha256(existing).hexdigest() != previous.get(name):
                return skipped(
                    f"{name} contains local changes or belongs to another skill"
                )
        pending[name] = content

    hashes = {
        name: hashlib.sha256(content).hexdigest() for name, content in desired.items()
    }
    for name, content in pending.items():
        _write_file(destination / name, content)
    if previous != hashes:
        _write_file(receipt, (json.dumps(hashes, indent=2) + "\n").encode("utf-8"))
    action = "Installed/updated" if pending else "Already installed"
    return f"{action} skill: {destination}"
