"""Local preset examples, composed using Pillow in Blender Render's interpreter."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from pathlib import Path


def preset_gallery(backend: Path, cache: Path, catalog: list[dict]) -> dict:
    """Return a compact catalog and a cached contact sheet of real example PNGs."""
    entries = [
        {
            "number": i,
            "id": item["id"],
            "description": item["description"],
            "aliases": item.get("aliases", []),
            "palette": item.get("palette", []),
        }
        for i, item in enumerate(catalog, 1)
    ]
    result = {
        "presets": entries,
        "preview_kind": "reference_examples",
        "message": (
            "These are saved example renders, not previews of the current scene. "
            "Keeping PyMOL colours can change the displayed palette. "
            "When the user asks for available presets, show these choices and "
            "wait for a selection before submitting a render."
        ),
    }
    try:
        examples = json.loads((backend / "examples/presets/gallery.json").read_text())
        by_id = {item["preset"]: item for item in examples}
    except (OSError, ValueError, KeyError, TypeError):
        result["preview_warning"] = "Example images are unavailable in this backend."
        return result

    images = []
    digest = hashlib.sha256(b"co-pymol-gallery-v1")
    for entry in entries:
        example = by_id.get(entry["id"], {})
        raw = None
        try:
            path = (backend / example["thumbnail"]).resolve()
            if not path.is_relative_to(backend.resolve()):
                raise ValueError("Example path is outside the backend")
            raw = path.read_bytes()
            expected = example.get("thumbnail_sha256")
            if expected and hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("Example checksum changed")
        except (OSError, KeyError, TypeError, ValueError):
            raw = None
        entry["example_structure"] = example.get("source_id")
        entry["preview_available"] = raw is not None
        images.append(raw)
        digest.update(json.dumps(entry, sort_keys=True).encode())
        digest.update(raw or b"missing")
    if not any(images):
        result["preview_warning"] = "No verified example PNGs are available."
        return result

    output = cache / (digest.hexdigest()[:20] + ".png")
    if not output.is_file():
        from io import BytesIO

        from PIL import Image, ImageDraw, ImageFont, ImageOps

        columns = min(4, len(entries))
        rows = math.ceil(len(entries) / columns)
        card_width, card_height, gutter, margin, header = 320, 264, 16, 24, 98
        width = margin * 2 + columns * card_width + (columns - 1) * gutter
        height = header + rows * (card_height + gutter) + margin
        sheet = Image.new("RGB", (width, height), "#eef1f5")
        draw = ImageDraw.Draw(sheet)
        title_font = ImageFont.load_default(size=28)
        font = ImageFont.load_default(size=16)
        small = ImageFont.load_default(size=14)
        draw.text(
            (margin, 18), "Blender preset examples", font=title_font, fill="#18202c"
        )
        draw.text(
            (margin, 57),
            "Reference structures · choose a number or preset name",
            font=font,
            fill="#465265",
        )
        for i, (entry, raw) in enumerate(zip(entries, images, strict=True)):
            x = margin + (i % columns) * (card_width + gutter)
            y = header + (i // columns) * (card_height + gutter)
            draw.rounded_rectangle(
                (x, y, x + card_width, y + card_height), radius=10, fill="white"
            )
            if raw:
                with Image.open(BytesIO(raw)) as source:
                    thumb = ImageOps.contain(source.convert("RGBA"), (296, 200))
                sheet.paste(
                    thumb,
                    (
                        x + (card_width - thumb.width) // 2,
                        y + 8 + (200 - thumb.height) // 2,
                    ),
                    thumb,
                )
            else:
                draw.text(
                    (x + 20, y + 90), "Example unavailable", font=font, fill="#465265"
                )
            label = re.sub(r"^preset_\d+_", "", entry["id"]).replace("_", " ")
            draw.text((x + 12, y + 214), f"{i + 1}. {label}", font=font, fill="#18202c")
            draw.text(
                (x + 12, y + 239),
                f"Example: {entry['example_structure'] or 'unknown'}",
                font=small,
                fill="#465265",
            )
        cache.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = cache / (uuid.uuid4().hex + ".png")
        try:
            sheet.save(temporary, format="PNG")
            temporary.chmod(0o600)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
    result["preview_path"] = str(output)
    return result
