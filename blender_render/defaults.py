"""Shared render defaults captured with the renderer's immutable inputs."""
import json
from importlib.resources import files
from pathlib import Path

if Path(__file__).is_file():
    from .engine import resource_bytes
    DEFAULTS = json.loads(resource_bytes(Path(__file__).with_name('render_defaults.json')))
else:
    # Structure Studio imports the pure animation/export helpers straight from
    # its bundled wheel in PyMOL; no installed renderer or engine seal is needed.
    DEFAULTS = json.loads(files(__package__).joinpath('render_defaults.json').read_text())
IMAGE = DEFAULTS['image']
ANIMATION = DEFAULTS['animation']
ANIMATION_LIMITS = DEFAULTS['limits']['animation']
MIN_FRAMES = ANIMATION_LIMITS['min_frames']
MAX_FRAMES = ANIMATION_LIMITS['max_frames']
MAX_FPS = ANIMATION_LIMITS['max_fps']
MAX_DURATION = ANIMATION_LIMITS['max_duration']
MAX_ROCK_ANGLE = ANIMATION_LIMITS['max_rock_angle']
MAX_RGBA = ANIMATION_LIMITS['max_rgba_bytes']
MAX_ARCHIVE = ANIMATION_LIMITS['max_archive_bytes']
