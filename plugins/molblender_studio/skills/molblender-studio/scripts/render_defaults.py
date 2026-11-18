"""The renderer's shared defaults, read from this plugin's bundled wheel (or source checkout).

co-pymol reads the same file, so Studio, co-pymol and the renderer cannot drift.
"""
import studio_env

studio_env.use_bundled_helpers()
from co_pymol.defaults import ANIMATION, ANIMATION_LIMITS, DEFAULTS, IMAGE  # noqa: E402,F401
