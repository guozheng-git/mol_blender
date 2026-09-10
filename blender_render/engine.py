"""Deterministic installed identity and one source/runtime observation per process.

This module deliberately uses only the standard library: packaging and the
login-node compatibility helper must not import renderer dependencies.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
from functools import lru_cache
from pathlib import Path
import sys

CAPABILITIES = sorted({
    'representation.cartoon', 'representation.ribbon', 'representation.surface',
    'representation.sticks', 'representation.spheres', 'representation.wire',
    'representation.surface_smoothing', 'representation.wire_mesh',
    'representation.nucleic_base_thickness', 'material.clay', 'material.dried_paint',
    'material.frosted_grid', 'material.color_gradient', 'material.attribute_color',
    'material.fog_glow', 'material.edge_highlight', 'material.uniform_polymer_color',
    'geometry.edge_rounding', 'geometry.element_palette', 'world.directional_gradient',
    'world.separate_illumination', 'camera.depth_of_field', 'camera.reference_framing',
})


class EngineMismatch(ValueError):
    """A known compatibility failure; never a reason to launch fallback work."""


def generate_manifest(root: Path) -> dict:
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.iterdir()) if p.is_file()
             and p.suffix in {'.py', '.json', '.txt', '.def', '.sh', '.html'}
             and p.name != 'engine-manifest.json'}
    return {'manifest_version': 1, 'protocol_version': 1,
            'bundle_versions': ['1.0'], 'schema_versions': ['1.0'],
            'engine_build': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'capabilities': CAPABILITIES,
            'runtime_requirements': json.loads((root / 'runtime.lock.json').read_text()),
            'files': files}


@lru_cache(maxsize=1)
def _identity():
    root = Path(__file__).resolve().parent
    manifest = root / 'engine-manifest.json'
    return json.loads(manifest.read_text()) if manifest.is_file() else generate_manifest(root)


def engine_identity() -> dict:
    return copy.deepcopy(_identity())


def require_compatible(expected, observed, requirements):
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        raise EngineMismatch('Engine identity unavailable; explicit runtime setup required')
    for key in ('manifest_version', 'protocol_version', 'bundle_versions', 'schema_versions', 'engine_build', 'runtime_requirements'):
        if key not in expected or expected[key] != observed.get(key):
            raise EngineMismatch('Incompatible engine ' + key + '; install the exact controller build')
    missing = set(requirements) - set(observed.get('capabilities', []))
    if missing:
        raise EngineMismatch('Unsupported engine capabilities: ' + ', '.join(sorted(missing)))


@lru_cache(maxsize=1)
def runtime_observation():
    import bpy
    versions = {'blender': bpy.app.version_string, 'python': '.'.join(map(str, sys.version_info[:2]))}
    for name in ('molecularnodes', 'numpy', 'biotite'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
            if name == 'molecularnodes':
                try:
                    import molecularnodes
                    import tomllib
                    versions[name] = tomllib.loads(Path(molecularnodes.__file__).with_name('blender_manifest.toml').read_text())['version']
                except (ImportError, OSError, ValueError, KeyError):
                    pass
    required = _identity()['runtime_requirements']
    mismatches = [key for key, value in versions.items() if value != required[key]]
    return {'versions': versions, 'verified': not mismatches, 'mismatches': mismatches}
