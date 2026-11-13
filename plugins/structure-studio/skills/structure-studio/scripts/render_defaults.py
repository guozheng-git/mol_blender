"""Read the shared defaults bundled by scripts/build_plugin.py."""
import json
from pathlib import Path

DEFAULTS = json.loads((Path(__file__).resolve().parent.parent / 'assets/render_defaults.json').read_text())
IMAGE = DEFAULTS['image']
ANIMATION = DEFAULTS['animation']
ANIMATION_LIMITS = DEFAULTS['limits']['animation']
