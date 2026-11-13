"""Read shared data from the sibling package without importing the renderer."""
import json
from pathlib import Path

DEFAULTS = json.loads((Path(__file__).resolve().parent.parent / 'blender_render/render_defaults.json').read_text())
IMAGE = DEFAULTS['image']
ANIMATION = DEFAULTS['animation']
ANIMATION_LIMITS = DEFAULTS['limits']['animation']
