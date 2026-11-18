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
import os
import re
import struct
import time
import zlib
from pathlib import Path, PureWindowsPath
from tempfile import mkstemp

PART_CHARS = 15 * 1024 * 1024
ENCODING = "gzip+base64"
BUNDLE_FORMAT = "molblender-studio-session/1"
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ENCODED_BYTES = 256 * 1024 * 1024
MAX_PART_BYTES = 20 * 1024 * 1024
MAX_PARTS = 64
MAX_FILES = 64
HOW_TO_DECODE = ("For each filename, join the `data` strings of its entries in `part` order across all bundle "
                 "files, base64-decode, gunzip, and compare with `sha256`. `studio.py restore` does this.")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bounded(path, limit):
    with Path(path).expanduser().open('rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Bundle input exceeds size limit')
    return data


def _decode(text, limit=MAX_FILE_BYTES):
    """Stop inflation at the declared size (or the hard limit for legacy parts)."""
    if not isinstance(text, (str, bytes)) or len(text) > MAX_ENCODED_BYTES:
        raise ValueError('Encoded bundle exceeds size limit')
    try:
        compressed = base64.b64decode(text, validate=True)
        inflater = zlib.decompressobj(wbits=31)
        raw = inflater.decompress(compressed, limit + 1)
        if len(raw) > limit or inflater.unconsumed_tail:
            raise ValueError('Decoded bundle exceeds size limit')
        if not inflater.eof or inflater.unused_data:
            raise ValueError('Truncated or concatenated gzip bundle')
        return raw
    except (zlib.error, UnicodeError) as exc:
        raise ValueError('Invalid gzip/base64 bundle') from exc


def pack(path: str, out_dir: str) -> dict:
    """Write ``NAME.gz.b64[.partNN].txt`` files and describe them."""
    source = Path(path).expanduser()
    raw = _read_bounded(source, MAX_FILE_BYTES)
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
    total = encoded = 0
    names = set()
    if not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Invalid bundle file count')
    for role, path in files.items():
        source = Path(path).expanduser()
        _destination(Path(out_stem).expanduser().parent.resolve(), source.name)
        if source.name in names:
            raise ValueError('Duplicate bundle filename')
        names.add(source.name)
        raw = _read_bounded(source, MAX_FILE_BYTES)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Bundle total exceeds size limit')
        text = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
        encoded += len(text)
        if encoded > MAX_ENCODED_BYTES:
            raise ValueError('Encoded bundle exceeds size limit')
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
    if not 1 <= len(bundle_files) <= MAX_PARTS:
        raise ValueError('Invalid bundle part count')
    input_size = total = encoded = 0
    documents = set()
    expected_count = None
    for path in bundle_files:
        raw = _read_bounded(path, min(MAX_PART_BYTES, MAX_ENCODED_BYTES - input_size))
        input_size += len(raw)
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("format") != BUNDLE_FORMAT:
            raise ValueError(f"{path} is not a {BUNDLE_FORMAT} bundle")
        if (type(data.get('count')) is not int or not 1 <= data['count'] <= MAX_PARTS
                or type(data.get('index')) is not int or not 1 <= data['index'] <= data['count']
                or data['index'] in documents
                or (expected_count is not None and data['count'] != expected_count)):
            raise ValueError('Invalid or duplicate bundle document')
        expected_count = data['count']
        documents.add(data['index'])
        if not isinstance(data.get('entries'), list) or len(data['entries']) > MAX_FILES * MAX_PARTS:
            raise ValueError('Invalid bundle entries')
        for entry in data["entries"]:
            if not isinstance(entry, dict):
                raise ValueError('Invalid bundle entry')
            _destination(Path.cwd().resolve(), entry.get('filename'))
            if (type(entry.get('bytes')) is not int or not 0 <= entry['bytes'] <= MAX_FILE_BYTES
                    or not isinstance(entry.get('sha256'), str)
                    or not re.fullmatch('[a-f0-9]{64}', entry['sha256'])
                    or not isinstance(entry.get('role'), str) or len(entry['role']) > 64
                    or not isinstance(entry.get('data'), str) or len(entry['data']) > MAX_PART_BYTES):
                raise ValueError('Invalid bundle metadata or size limit')
            if entry['filename'] not in pieces:
                total += entry['bytes']
                if len(pieces) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise ValueError('Bundle total exceeds size limit')
            encoded += len(entry['data'])
            if encoded > MAX_ENCODED_BYTES:
                raise ValueError('Encoded bundle exceeds size limit')
            item = pieces.setdefault(entry["filename"], {"info": entry, "parts": {}})
            fields = ("role", "filename", "parts", "bytes", "sha256", "encoding")
            if any(entry.get(k) != item["info"].get(k) for k in fields):
                raise ValueError(f"{entry['filename']}: inconsistent bundle metadata")
            if entry.get("encoding") != ENCODING:
                raise ValueError(f"Unsupported bundle encoding: {entry.get('encoding')!r}")
            if (type(entry.get("parts")) is not int or type(entry.get("part")) is not int
                    or not 1 <= entry["part"] <= entry["parts"] <= MAX_PARTS):
                raise ValueError(f"{entry['filename']}: invalid bundle part")
            if entry["part"] in item["parts"]:
                raise ValueError(f"{entry['filename']}: duplicate bundle part {entry['part']}")
            item["parts"][entry["part"]] = entry["data"]
    if len(documents) != expected_count:
        raise ValueError('missing bundle parts; download every document')
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
        raw = _decode("".join(item["parts"][n] for n in range(1, info["parts"] + 1)), info['bytes'])
        if sha256(raw) != info["sha256"] or len(raw) != info["bytes"]:
            raise ValueError(f"{filename}: checksum mismatch")
        if target.exists() and (not target.is_file() or target.stat().st_size != len(raw)
                                or _read_bounded(target, len(raw)) != raw):
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
    if not 1 <= len(parts) <= MAX_PARTS:
        raise ValueError('Invalid bundle part count')
    chunks, size = [], 0
    for path in parts:
        chunk = _read_bounded(path, min(MAX_PART_BYTES, MAX_ENCODED_BYTES - size)).strip()
        size += len(chunk)
        chunks.append(chunk)
    raw = _decode(b''.join(chunks))
    digest = sha256(raw)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(f"Checksum mismatch for {output}: got {digest}, expected {expected_sha256}")
    target = Path(output).expanduser()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f'Refusing to overwrite {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as handle:
        handle.write(raw)
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
    if render.get("device"):
        doc["device"] = render["device"]
    return doc


def write_json(data: dict, path: str | Path, *, exclusive: bool = False) -> str:
    """Publish durable JSON atomically, optionally reserving a new filename."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = mkstemp(prefix=".json-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(temporary, target)
        else:
            os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(target.resolve())
