"""Build the Mol* page: a live workspace artifact, or a self-contained offline viewer.

Standard library only, so it runs before any runtime is installed. Pillow is used
opportunistically to shrink large renders embedded in offline pages.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import re
import zipfile
from pathlib import Path

import scene as scenes
from structure_io import read_structure_bytes, summarize

ASSETS = Path(__file__).resolve().parent.parent / "assets"
MOLSTAR_VERSION = "5.12.0"
PAGE_LIMIT = 16 * 1024 * 1024
DOWNLOAD_BUDGET = 8 * 1024 * 1024
RENDER_BUDGET = int(1.5 * 1024 * 1024)
PALETTE = ["#4bb5a6", "#5e8fea", "#e48db2", "#9a80dd", "#eda953", "#68b95a", "#df7462", "#46a2d6", "#c4a23a", "#8c98ad"]
STYLES = ("cartoon", "surface", "atoms", "site")
COLORS = ("chain", "ss", "rainbow", "bfactor", "plddt")
SKELETON = ('<!doctype html><html><head><meta charset=utf8><meta name=viewport '
            'content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>')
# Rules for workspace artifacts: anyone admitted reads; Contributors (and editors) change the shared
# scene; only editors and the owner add renders or change the workspace record.
WORKSPACE_CAPABILITIES = {
    "db": {"rules": [{"path": "scene", "read": "view", "write": "interact"},
                     {"path": "renders", "read": "view", "write": "admin"},
                     {"path": "workspace", "read": "view", "write": "admin"}]},
    "assets": {}, "downloads": True, "user": {},
}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _image_payload(path: Path, budget: int) -> tuple[str, int, int, list[str]]:
    """Return a data URI that fits the per-image budget, plus its size and notes."""
    data = path.read_bytes()
    mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    width = height = 0
    notes = []
    try:
        from PIL import Image  # optional
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if len(data) > budget:
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, (255, 255, 255))
                flat.paste(image, mask=image.getchannel("A"))
                flat.thumbnail((1600, 1600))
                buffer = io.BytesIO()
                flat.save(buffer, "JPEG", quality=88, optimize=True)
                data, mime = buffer.getvalue(), "image/jpeg"
                width, height = flat.size
                notes.append(f"{path.name}: embedded as a {width}x{height} JPEG preview")
    except ImportError:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if len(data) > budget:
        raise ValueError(f"{path.name} is {len(data) / 1e6:.1f} MB; install Pillow or pass a smaller image")
    return f"data:{mime};base64,{_b64(data)}", width, height, notes


def _bundle(paths: list[Path], name: str) -> dict:
    buffer = io.BytesIO()
    contents = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, path.name)
            contents.append({"name": path.name, "size": path.stat().st_size})
    data = buffer.getvalue()
    return {"filename": name, "size": len(data), "contents": contents, "data": _b64(data)}


def _render_entries(renders: list[str], notes: list[str]) -> list[dict]:
    entries = []
    for spec in renders:
        # PATH or PATH=Label|description
        path_text, _, label_text = spec.partition("=")
        label, _, description = label_text.partition("|")
        path = Path(path_text).expanduser()
        manifest = path.with_suffix(".render") / "resolved-style.json"
        src, width, height, image_notes = _image_payload(path, RENDER_BUDGET)
        notes.extend(image_notes)
        detail = ""
        if manifest.is_file():
            output = json.loads(manifest.read_text()).get("output", {})
            detail = f"{output.get('width')}×{output.get('height')} · {output.get('samples')} samples"
        filename = path.name if src.startswith("data:image/png") else path.stem + ".jpg"
        entries.append({"src": src, "label": label or path.stem, "description": description,
                        "spec": detail or (f"{width}×{height}" if width else ""), "filename": filename})
    return entries


def initial_scene(summary: dict, *, style: str = "cartoon", color: str = "chain", ligand: str = "",
                  water: bool = False) -> dict:
    """A validated starting scene with explicit chain colors (file order), shared by page and renders."""
    if style not in STYLES or color not in COLORS:
        raise ValueError(f"style must be one of {STYLES} and color one of {COLORS}")
    chain_colors = {c["id"]: PALETTE[i % len(PALETTE)] for i, c in enumerate(summary.get("chains", []))}
    data = scenes.default_scene(chain_colors=chain_colors, color=color, waters=water,
                                representation="spacefill" if style == "atoms" else "surface" if style == "surface" else "cartoon")
    ligands = summary.get("ligands", [])
    key = ""
    if ligand:
        wanted = ligand.strip().upper()
        match = next((l for l in ligands if l["key"].upper() == wanted), None) or next((l for l in ligands if l["comp"].upper() == wanted), None)
        if not match:
            raise ValueError(f"Ligand {ligand!r} not found; available: {', '.join(l['key'] for l in ligands) or 'none'}")
        key = match["key"]
    if style == "site":
        key = key or (ligands[0]["key"] if ligands else "")
        if not key:
            raise ValueError("The binding-site style needs a ligand, but this structure has none")
        data.update(layers=[{"select": {"site": key}, "representation": "sticks", "color": "element"}],
                    focus={"ligand": key}, opacity=0.45)
    data.update(revision=1, updated_by="claude", note="initial scene")
    return scenes.validate(data, summary)


def build(structure: str, output: str, *, title: str = "", entry_id: str = "", source: str = "",
          renders: list[str] | None = None, files: list[str] | None = None, style: str = "cartoon",
          color: str = "chain", ligand: str = "", water: bool = False, camera: dict | None = None,
          predicted: bool | None = None, tab: str = "", standalone: bool = False, scene: dict | None = None,
          workspace: bool = False) -> dict:
    raw, name = read_structure_bytes(structure)
    summary = summarize(raw, name)
    start = scenes.validate(scene, summary) if scene else initial_scene(summary, style=style, color=color, ligand=ligand, water=water)
    notes: list[str] = []
    data = {
        "version": 2, "workspace": workspace, "id": entry_id or summary.get("id") or "",
        "title": title or summary.get("title") or "", "source": source, "predicted": predicted,
        "structure": {"filename": name, "gzip": True, "data": _b64(gzip.compress(raw, mtime=0))},
        "scene": start, "camera": camera, "initial_tab": tab,
        "renders": _render_entries(renders or [], notes), "downloads": None,
    }
    if files:
        paths = [Path(p).expanduser() for p in files]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise ValueError("Missing files: " + ", ".join(missing))
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", (data["id"] or Path(name).stem)).strip("-").lower() or "structure"
        bundle = _bundle(paths, f"{stem}-files.zip")
        if bundle["size"] > DOWNLOAD_BUDGET:
            notes.append(f"Session files ({bundle['size'] / 1e6:.1f} MB zipped) exceed the page budget; deliver them in the chat instead")
        else:
            data["downloads"] = bundle
    label = (data["id"] or Path(name).stem).upper()
    page_title = title if (workspace and title) else (summary.get("suggested_workspace_title") if workspace else f"{label} Structure Viewer")
    template = (ASSETS / "workspace.html").read_text()
    css = (ASSETS / f"molstar-{MOLSTAR_VERSION}.css").read_text()
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = (template.replace("__PAGE_TITLE__", page_title.replace("<", "&lt;"))
            .replace("/*__MOLSTAR_CSS__*/", css)
            .replace("__MOLSTAR_VERSION__", MOLSTAR_VERSION)
            .replace("__STUDIO_DATA__", payload))
    if standalone:
        html = SKELETON + html + "</body></html>"
    size = len(html.encode())
    if size > PAGE_LIMIT:
        raise ValueError(f"Viewer page would be {size / 1e6:.1f} MB (limit 16 MB); drop renders or files")
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return {"viewer": str(out.resolve()), "title": page_title, "bytes": size, "standalone": standalone,
            "workspace": workspace, "renders": len(data["renders"]), "downloads": bool(data["downloads"]),
            "needs_downloads_capability": bool(data["downloads"] or data["renders"]), "scene": start,
            "structure": {k: summary[k] for k in ("id", "title", "suggested_workspace_title", "chains", "ligands", "atoms") if k in summary},
            "notes": notes}
