"""Compatibility imports for the shared validation helpers."""
from blender_render import core as _core
globals().update({name: getattr(_core, name) for name in dir(_core) if not name.startswith('__')})
