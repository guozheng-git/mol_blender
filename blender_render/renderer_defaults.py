"""Shared execution defaults for style resolution and Blender configuration.

Appearance controls remain explicit in presets. In particular, omitting a view
transform or look in a custom style retains Blender's existing scene setting.
"""
from types import MappingProxyType


RENDERER_DEFAULTS = MappingProxyType({
    "device": "GPU",
    "compute_device_type": "AUTO",
    "transparent_background": False,
    "adaptive_sampling": True,
    "adaptive_threshold": 0.01,
    "denoise": False,
    "seed": 0,
})


def resolve_renderer(renderer: dict) -> dict:
    """Expand validated settings without mutating the caller or shared defaults.

    Keep require_gpu absent unless explicitly set: its existing environment
    fallback is evaluated by the device selector at execution time.
    """
    return {**RENDERER_DEFAULTS, **renderer}
