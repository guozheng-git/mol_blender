"""The files a render produces and where each one lands next to the chosen PNG path.

``render.png`` becomes ``<name>.png``, a movie becomes ``<name>.mp4`` and so on; anything
else (the .blend, manifest and style) goes in the ``<name>.render/`` folder.
"""
from __future__ import annotations

from pathlib import Path

_BESIDE_PNG = {'render.png': '.png', 'render.mp4': '.mp4', 'render.gif': '.gif',
               'frames.zip': '.frames.zip'}
_IN_FOLDER = ('render.blend', 'manifest.json', 'style.json')

#: Every artifact name a render may return; cluster transfers accept nothing else.
NAMES = frozenset(_BESIDE_PNG) | frozenset(_IN_FOLDER)


def beside_png(name):
    """True when ``name`` is published next to the PNG rather than inside the folder."""
    return name in _BESIDE_PNG


def folder(output):
    return Path(output).with_suffix('.render')


def destination(output, name):
    """Where artifact ``name`` is published for a render whose PNG is ``output``."""
    output = Path(output)
    return output.with_suffix(_BESIDE_PNG[name]) if name in _BESIDE_PNG else folder(output) / name
