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
