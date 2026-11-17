"""Read shared data from the sibling package without importing the renderer."""
import json
from pathlib import Path

# Studio also imports these helpers directly from its bundled wheel. Use our
# loader for that zip path without importing the sibling renderer package.
_path = Path(__file__).parent.parent / 'blender_render/render_defaults.json'
DEFAULTS = json.loads(_path.read_bytes() if _path.is_file() else __loader__.get_data(str(_path)))
IMAGE = DEFAULTS['image']
ANIMATION = DEFAULTS['animation']
ANIMATION_LIMITS = DEFAULTS['limits']['animation']
