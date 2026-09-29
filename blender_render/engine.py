"""Deterministic installed identity and one source/runtime observation per process.

This module deliberately uses only the standard library: packaging and the
local source snapshotter must not import renderer dependencies.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import importlib
import importlib.abc
import importlib.util
import importlib.machinery
import json
import os
import stat
import re
import threading
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
    'animation.camera_orbit', 'encoding.ffmpeg',
})


def _exception_diagnostic(value):
    # This file also runs as a standalone stdlib verifier before a remote package
    # is trusted. Never import package code while reporting an integrity failure.
    module = sys.modules.get('blender_render.compatibility')
    if module is not None:
        return module.sanitize(value)
    return {'version':1, 'reason':'unknown_compatibility', 'phase':'unknown',
            'component':'unknown', 'before_execution':False, 'expected':{}, 'observed':{}, 'log_refs':[]}


class EngineMismatch(ValueError):
    """A known compatibility failure; never a reason to launch fallback work."""
    def __init__(self, *args, diagnostic=None):
        super().__init__(*args)
        self.diagnostic = _exception_diagnostic(diagnostic)


class RestartRequired(EngineMismatch):
    code = 'restart_required'


class RuntimeUnavailable(EngineMismatch, RuntimeError):
    """A worker explicitly refused execution before invoking the renderer."""
    before_execution = True


# Runtime inventory excludes generated outputs and is also used by the wheel build.
EXCLUDED_DIRS = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'render_outputs'}
EXCLUDED_SUFFIXES = {'.pyc', '.pyo', '.tmp', '.log', '.sif'}



def excluded(path):
    return path.name == '.DS_Store' or bool(set(path.parts) & EXCLUDED_DIRS) or path.suffix in EXCLUDED_SUFFIXES


def package_inventory(root):
    """Normalized recursive file inventory shared by build and runtime checks."""
    root = Path(root).resolve()
    result = {}
    def walk(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if excluded(relative):
                continue
            if path.is_symlink():
                raise ValueError('Included package symlink is forbidden: ' + relative.as_posix())
            if path.is_dir():
                walk(path)
            elif path.is_file() and relative.as_posix() != 'engine-manifest.json':
                name = relative.as_posix()
                if '\\' in name or name in result or not path.resolve().is_relative_to(root):
                    raise ValueError('Invalid or colliding package-relative path')
                result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif not path.is_file():
                raise ValueError('Package input must be a regular file')
    walk(root)
    return dict(sorted(result.items()))


def stage_package(source, destination):
    """Copy only verified inventory bytes, preserving relative directories."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source):
        raise ValueError('Package staging must be outside the source package')
    inventory = package_inventory(source)
    for name, digest in inventory.items():
        target = destination/name
        if not target.resolve().is_relative_to(destination) or target.is_symlink():
            raise ValueError('Staging path escapes or aliases its package root')
        data = (source/name).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Package changed during build staging')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod((source/name).stat().st_mode & 0o777)
    return inventory


def generate_manifest(root: Path) -> dict:
    files = package_inventory(root)
    return {'manifest_version': 1, 'protocol_version': 1,
            'bundle_versions': ['1.0'], 'schema_versions': ['1.0'],
            'engine_build': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            'capabilities': CAPABILITIES,
            'runtime_requirements': json.loads((root / 'runtime.lock.json').read_text()),
            'files': files}


class EngineSeal:
    """One process seal. A failed verification cannot be cleared in place."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.aliases = {Path(os.path.abspath(root)), self.root}
        self.latched = False
        self.lock = threading.RLock()
        try:
            self.identity = generate_manifest(self.root)
            manifest = self.root / 'engine-manifest.json'
            if manifest.is_symlink():
                raise ValueError('Manifest symlink')
            self.manifest_bytes = manifest.read_bytes() if manifest.exists() else None
            if self.manifest_bytes is not None and json.loads(self.manifest_bytes) != self.identity:
                raise ValueError('Stale installed manifest')
            self.inputs = {name: (self.root/name).read_bytes() for name in self.identity['files']}
            self.resources = {name:data for name,data in self.inputs.items()
                              if not name.endswith(('.py', '.so', '.pyd', '.dylib'))}
            if any(hashlib.sha256(data).hexdigest() != self.identity['files'][name] for name, data in self.inputs.items()):
                raise ValueError('Input changed during capture')
            self.check()
        except (OSError, ValueError) as exc:
            self.latched = True
            raise RestartRequired('restart_required: package verification failed') from exc

    def capture(self, destination):
        """Private action-owned package, constructed only from startup bytes.

        The owner retains this directory until its child exits. Never overwrite
        a capture: a replacement process gets a new directory and seal.
        """
        self.check()
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError('Capture destination must be new')
        destination.mkdir(mode=0o700, parents=True)
        for name, data in self.inputs.items():
            target = destination/name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o400)
        (destination/'engine-manifest.json').write_text(json.dumps(self.identity, sort_keys=True))
        (destination/'engine-manifest.json').chmod(0o400)
        self.check()
        return destination

    def check(self):
        with self.lock:
            if self.latched:
                raise RestartRequired('restart_required: restart the affected process')
            try:
                if any(alias.resolve() != self.root for alias in self.aliases):
                    raise ValueError('Package root alias changed')
                manifest = self.root/'engine-manifest.json'
                if manifest.is_symlink() or generate_manifest(self.root) != self.identity:
                    raise ValueError('Package changed')
                if (manifest.read_bytes() if manifest.exists() else None) != self.manifest_bytes:
                    raise ValueError('Manifest changed')
            except (OSError, ValueError) as exc:
                self.latched = True
                raise RestartRequired('restart_required: package changed since process startup') from exc


@lru_cache(maxsize=1)
def process_seal():
    return EngineSeal(Path(__file__).parent)


def _identity():
    return process_seal().identity


def engine_identity() -> dict:
    return copy.deepcopy(_identity())


def resource_bytes(path):
    """Read a captured package input; external request files remain caller data."""
    seal = process_seal()
    # Classify lexical package identity before following mutable symlinks.
    path = Path(os.path.abspath(path))
    for root in sorted(seal.aliases, key=lambda root:len(root.parts), reverse=True):
        if path.is_relative_to(root):
            return seal.resources[path.relative_to(root).as_posix()]
    return path.read_bytes()








# Reviewed local module closures. Dependencies may be imported transitively here.
ACTION_MODULES = {
    'conversion': ('export',),
    'render': ('core', 'chain_palette', 'ligand_selection', 'cutout_planes',
               'geometry', 'paint', 'reference_effects', 'operation_progress', 'telemetry', 'worker_ccd', 'animation', 'animation_blender'),
}



class _SealedLoader(importlib.abc.Loader):
    def __init__(self, seal, name):
        self.seal, self.name = seal, name

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        self.seal.check()
        module.__file__ = str(self.seal.root/self.name)
        exec(compile(self.seal.inputs[self.name], module.__file__, 'exec'), module.__dict__)


class _SealedFinder(importlib.abc.MetaPathFinder):
    """Only action preloads use this finder; no provider imports are invented."""
    def __init__(self, seal):
        self.seal = seal

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith('blender_render.'):
            return None
        self.seal.check()
        relative = fullname.removeprefix('blender_render.').replace('.', '/')
        for name, package in ((relative+'.py', False), (relative+'/__init__.py', True)):
            if name in self.seal.inputs:
                return importlib.util.spec_from_loader(fullname, _SealedLoader(self.seal, name), is_package=package)
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            if relative+suffix in self.seal.inputs:
                # Native imports also use captured files, retained for the
                # process lifetime; never dlopen the mutable installation.
                if not hasattr(self.seal, '_native_capture'):
                    import tempfile
                    self.seal._native_capture = tempfile.TemporaryDirectory(prefix='blender-native-')
                    self.seal._native_package = self.seal.capture(Path(self.seal._native_capture.name)/'blender_render')
                return importlib.util.spec_from_file_location(fullname, self.seal._native_package/(relative+suffix))
        raise RestartRequired('restart_required: action dependency absent from inventory')


def protect(action):
    seal = process_seal()
    finder = _SealedFinder(seal)
    try:
        with seal.lock:
            seal.check()
            sys.meta_path.insert(0, finder)
            for name in ACTION_MODULES[action]:
                module = importlib.import_module('blender_render.' + name)
                path = Path(module.__file__).resolve()
                if not path.is_relative_to(seal.root) and not path.is_relative_to(getattr(seal, '_native_package', seal.root)):
                    seal.latched = True
                    raise RestartRequired('restart_required: action module outside sealed package')
            seal.check()
    except RestartRequired as exc:
        exc.before_execution = True  # This protected action has not begun.
        raise
    finally:
        if finder in sys.meta_path: sys.meta_path.remove(finder)
    return seal
