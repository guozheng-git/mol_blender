"""Store binary session files in an artifact and get them back in a later chat.

The artifact file store only accepts web types (images, PDF, fonts, CSV, Markdown, JSON, plain
text), with a 20 MiB cap per file. A render's session files (.pse, .blend) travel together in a
*session bundle*: one JSON file (more only past ~15 MiB) whose entries hold gzip-compressed,
base64-encoded data. The viewer page and ``restore`` rebuild the originals and check SHA-256.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
import time
from pathlib import Path, PureWindowsPath

PART_CHARS = 15 * 1024 * 1024
ENCODING = "gzip+base64"
BUNDLE_FORMAT = "structure-studio-session/1"
HOW_TO_DECODE = ("For each filename, join the `data` strings of its entries in `part` order across all bundle "
                 "files, base64-decode, gunzip, and compare with `sha256`. `studio.py restore` does this.")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack(path: str, out_dir: str) -> dict:
    """Write ``NAME.gz.b64[.partNN].txt`` files and describe them."""
    source = Path(path).expanduser()
    raw = source.read_bytes()
    text = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0))
    directory = Path(out_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    chunks = [text[i:i + PART_CHARS] for i in range(0, len(text), PART_CHARS)] or [b""]
    parts = []
    for index, chunk in enumerate(chunks, 1):
        suffix = ".gz.b64.txt" if len(chunks) == 1 else f".gz.b64.part{index:02d}.txt"
        target = directory / (source.name + suffix)
        target.write_bytes(chunk)
        parts.append(str(target))
    return {"filename": source.name, "bytes": len(raw), "sha256": sha256(raw), "encoding": ENCODING,
            "stored_bytes": len(text), "parts": parts}


def bundle(files: dict, out_stem: str) -> dict:
    """Write ``OUT_STEM.session.json`` (or ``.session.partNN.json``) holding ``{role: path}`` files."""
    entries, described = [], []
    for role, path in files.items():
        source = Path(path).expanduser()
        raw = source.read_bytes()
        text = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
        chunks = [text[i:i + PART_CHARS] for i in range(0, len(text), PART_CHARS)] or [""]
        info = {"role": role, "filename": source.name, "bytes": len(raw), "sha256": sha256(raw)}
        described.append(info)
        entries += [dict(info, encoding=ENCODING, part=i + 1, parts=len(chunks), data=chunk) for i, chunk in enumerate(chunks)]
    groups, current, size = [], [], 0
    for entry in entries:
        if current and size + len(entry["data"]) > PART_CHARS:
            groups.append(current)
            current, size = [], 0
        current.append(entry)
        size += len(entry["data"])
    groups.append(current)
    stem = Path(out_stem).expanduser()
    stem.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, group in enumerate(groups, 1):
        target = stem.with_name(stem.name + (".session.json" if len(groups) == 1 else f".session.part{index:02d}.json"))
        target.write_text(json.dumps({"format": BUNDLE_FORMAT, "how_to_decode": HOW_TO_DECODE, "index": index,
                                      "count": len(groups), "entries": group}))
        paths.append(str(target))
    return {"format": BUNDLE_FORMAT, "files": described, "parts": paths}


def _destination(directory: Path, filename: str) -> Path:
    """Bundle names are portable basenames, never paths or symlink destinations."""
    if (not isinstance(filename, str) or not filename or filename in {".", ".."}
            or any(c in filename for c in ("/", "\\", ":", "\0"))
            or PureWindowsPath(filename).is_absolute()):
        raise ValueError(f"Unsafe bundle filename: {filename!r}")
    target = directory / filename
    if target.is_symlink() or target.resolve().parent != directory:
        raise ValueError(f"Unsafe bundle destination: {filename!r}")
    return target


def _bundle_entries(bundle_files: list[str]) -> dict:
    pieces: dict[str, dict] = {}
    for path in bundle_files:
        data = json.loads(Path(path).expanduser().read_text())
        if data.get("format") != BUNDLE_FORMAT:
            raise ValueError(f"{path} is not a {BUNDLE_FORMAT} bundle")
        for entry in data["entries"]:
            item = pieces.setdefault(entry["filename"], {"info": entry, "parts": {}})
            fields = ("role", "filename", "parts", "bytes", "sha256", "encoding")
            if any(entry.get(k) != item["info"].get(k) for k in fields):
                raise ValueError(f"{entry['filename']}: inconsistent bundle metadata")
            if entry.get("encoding") != ENCODING:
                raise ValueError(f"Unsupported bundle encoding: {entry.get('encoding')!r}")
            if (type(entry["parts"]) is not int or type(entry["part"]) is not int
                    or not 1 <= entry["part"] <= entry["parts"]):
                raise ValueError(f"{entry['filename']}: invalid bundle part")
            if entry["part"] in item["parts"]:
                raise ValueError(f"{entry['filename']}: duplicate bundle part {entry['part']}")
            item["parts"][entry["part"]] = entry["data"]
    return pieces


def restore(bundle_files: list[str], out_dir: str) -> list[dict]:
    """Validate all files before restoring; never overwrite an unrelated file."""
    directory = Path(out_dir).expanduser().resolve()
    pieces = _bundle_entries(bundle_files)
    decoded = []
    for filename, item in pieces.items():
        target = _destination(directory, filename)
        info = item["info"]
        missing = [n for n in range(1, info["parts"] + 1) if n not in item["parts"]]
        if missing:
            raise ValueError(f"{filename}: missing bundle parts {missing}; download every asset id in the record")
        raw = gzip.decompress(base64.b64decode("".join(item["parts"][n] for n in range(1, info["parts"] + 1))))
        if sha256(raw) != info["sha256"] or len(raw) != info["bytes"]:
            raise ValueError(f"{filename}: checksum mismatch")
        if target.exists() and (not target.is_file() or target.read_bytes() != raw):
            raise FileExistsError(f"Refusing to overwrite {target}")
        decoded.append((target, raw, info))
    directory.mkdir(parents=True, exist_ok=True)
    restored = []
    for target, raw, info in decoded:
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(raw)
        restored.append({"role": info["role"], "path": str(target.resolve()), "bytes": len(raw), "sha256": info["sha256"], "verified": True})
    return restored


def unpack(parts: list[str], output: str, expected_sha256: str = "") -> dict:
    """Join downloaded parts (in order), decode, and verify against the recorded checksum."""
    text = b"".join(Path(p).expanduser().read_bytes().strip() for p in parts)
    raw = gzip.decompress(base64.b64decode(text))
    digest = sha256(raw)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(f"Checksum mismatch for {output}: got {digest}, expected {expected_sha256}")
    target = Path(output).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return {"path": str(target.resolve()), "bytes": len(raw), "sha256": digest, "verified": bool(expected_sha256)}


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return struct.unpack(">II", head[16:24])


def record(render: dict, assets: dict, *, label: str = "", note: str = "", scene: dict | None = None,
           camera: dict | None = None) -> dict:
    """The ``renders/<id>`` document for a finished render.

    ``render`` is the JSON printed by ``studio.py render --workspace``; ``assets`` maps ``image`` to the
    PNG's asset id and ``session`` to the asset ids of the uploaded session bundle file(s), in order.
    """
    image = Path(render["outputs"]["render.png"])
    width, height = png_size(image)
    doc = {"status": "done", "preset": render.get("preset"), "label": label or render.get("preset"),
           "note": note, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "seconds": render.get("seconds"), "cpu_adjustments": render.get("cpu_adjustments") or {},
           "image": {"asset_id": assets["image"], "filename": image.name, "width": width, "height": height,
                     "bytes": image.stat().st_size, "sha256": sha256(image.read_bytes())}}
    session = render.get("session")
    if session:
        ids = assets.get("session") or []
        if len(ids) != len(session["parts"]):
            raise ValueError(f"The session bundle has {len(session['parts'])} file(s) but {len(ids)} asset id(s) were given")
        doc["session"] = {"format": session["format"], "asset_ids": ids, "files": session["files"]}
    if scene is not None:
        doc["scene"] = scene
        doc["scene_revision"] = scene.get("revision")
    if camera is not None:
        doc["camera"] = camera
    if render.get("animation"):
        doc["animation"] = render["animation"]
    return doc


def write_json(data: dict, path: str) -> str:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2))
    return str(target.resolve())
