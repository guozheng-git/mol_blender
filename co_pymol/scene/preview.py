"""Native PyMOL stills and animation previews, independent of MCP and Blender.

Animation previews run in a private headless session: cutaways add temporary
objects and representations there without saving them back to the source PSE.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from co_pymol.constants import PLDDT_PALETTE, RENDER_POLL_ATTEMPTS, RENDER_POLL_INTERVAL_S
from co_pymol.defaults import ANIMATION
from .animation import Frame, View
from .camera import _rotation

SHELL_FLAG = 8  # a user flag PyMOL leaves free; marks the lid of the cutaway
SHELL_OBJECT = "cutaway_lid"


def apply_plddt_palette(cmd, selection: str = "all") -> None:
    """Color selection by pLDDT (b-factor 0–100) with discrete AlphaFold bins."""
    sel = f"({selection})"
    # PyMOL selections don't support >=; use strict > with epsilon cutoffs.
    cmd.color(PLDDT_PALETTE["very_high"], f"{sel} and b > 89.999")
    cmd.color(PLDDT_PALETTE["confident"], f"{sel} and b > 69.999 and b < 90")
    cmd.color(PLDDT_PALETTE["low"], f"{sel} and b > 49.999 and b < 70")
    cmd.color(PLDDT_PALETTE["very_low"], f"{sel} and b < 50")


def render_image(cmd, width: int, height: int, ray: bool = False) -> bytes:
    """Render PNG bytes. Caller holds the pymol_session lock."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if ray:
            cmd.ray(width, height)
        else:
            cmd.draw(width, height, antialias=2)
        cmd.png(tmp_path, dpi=150)

        # In GUI mode the PNG is written on PyMOL's main thread at the next
        # redraw, not by this worker thread's cmd.png call — poll for the file.
        for _ in range(RENDER_POLL_ATTEMPTS):
            if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                break
            time.sleep(RENDER_POLL_INTERVAL_S)
        else:
            timeout_s = RENDER_POLL_ATTEMPTS * RENDER_POLL_INTERVAL_S
            raise RuntimeError(
                f"Render timed out after {timeout_s:.1f}s (PNG file never appeared)"
            )

        data = Path(tmp_path).read_bytes()
        if not data:
            raise RuntimeError("Render produced an empty PNG file")
        return data
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def build_cutaway(cmd, focus: str, view: View) -> str:
    """Surface the polymer, splitting off the lid between camera and focus.

    Both surfaces are computed over every polymer atom, so the lid fits the
    hole it leaves exactly. Returns the lid object, whose transparency fades.
    """
    rotation, origin = _rotation(view), np.array(view[12:15])
    site = (cmd.get_coords(focus) - origin) @ rotation.T
    centre = site.mean(axis=0)
    reach = np.linalg.norm(site[:, :2] - centre[:2], axis=1).max() + 6
    rows: list[tuple] = []
    cmd.iterate_state(
        -1,
        "enabled and polymer",
        "rows.append((model, index, x, y, z))",
        space={"rows": rows},
    )
    lid: dict[str, list[int]] = {}
    for model, index, *xyz in rows:
        x, y, z = (np.array(xyz) - origin) @ rotation.T
        if z > centre[2] and np.hypot(x - centre[0], y - centre[1]) < reach:
            lid.setdefault(model, []).append(index)
    for model, indices in lid.items():
        for start in range(0, len(indices), 500):
            chunk = "+".join(map(str, indices[start : start + 500]))
            cmd.flag(SHELL_FLAG, f"byres ({model} and index {chunk})", "set")
    cmd.create(SHELL_OBJECT, "enabled and polymer")
    cmd.hide("everything", SHELL_OBJECT)
    cmd.show("surface", f"{SHELL_OBJECT} and flag {SHELL_FLAG}")
    cmd.show(
        "surface",
        f"enabled and polymer and not {SHELL_OBJECT} and not flag {SHELL_FLAG}",
    )
    cmd.show(
        "sticks",
        f"({focus}) or (byres (polymer and not {SHELL_OBJECT} within 5 of "
        f"({focus})) and not name N+C+O)",
    )
    cmd.set("transparency", 0)
    return SHELL_OBJECT


def render_preview(
    cmd,
    frames: list[Frame],
    output: Path,
    *,
    fps: int,
    width: int,
    height: int,
    shell: str | None = None,
) -> Path:
    """Ray-trace every frame at preview quality and encode an H.264 MP4."""
    from co_pymol.scene.capture import set_animation_frame

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise OSError("ffmpeg is required for animation previews (brew install ffmpeg)")
    width, height = width // 2 * 2, height // 2 * 2  # H.264 needs even sizes
    cmd.viewport(width, height)
    cmd.set("ray_shadows", 0)
    with tempfile.TemporaryDirectory(prefix="co-pymol-frames-") as directory:
        for number, frame in enumerate(frames):
            set_animation_frame(cmd, frame)
            if shell:
                # PyMOL previews retain a ghost of the shell. The shared
                # timeline records full lid opacity for Blender's cutaway.
                transparency = (1 - frame.lid_opacity) * ANIMATION["recipes"]["cutaway"]["pymol_shell_transparency"]
                cmd.set("transparency", transparency, shell)
            cmd.png(
                f"{directory}/frame_{number:04d}.png",
                width=width,
                height=height,
                ray=1,
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                str(fps),
                "-i",
                f"{directory}/frame_%04d.png",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "20",
                "-movflags",
                "+faststart",
                str(output),
            ],
            check=True,
        )
    return output
