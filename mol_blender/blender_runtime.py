# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified Blender runtime shared by fresh renders and the optional warm worker."""
import importlib.metadata
import json
import os
import sys
import tomllib
from pathlib import Path

from .developer import digest_file, source_files


class SnapshotRuntime:
    def __init__(self, cfg):
        import bpy
        import molecularnodes
        import biotite
        import numpy
        from . import worker_ccd

        self.cfg = cfg
        self.package = Path(__file__).resolve().parent
        self.expected = cfg['identity']['files']
        self.verify_sources()
        if digest_file(Path(cfg['ccd'])) != cfg['ccd_sha256']:
            raise RuntimeError('Development source/CCD changed before Blender execution')
        try:
            mn_version = importlib.metadata.version('molecularnodes')
        except importlib.metadata.PackageNotFoundError:
            mn_version = tomllib.loads(Path(molecularnodes.__file__).with_name('blender_manifest.toml').read_text())['version']
        self.versions = {'blender': bpy.app.version_string, 'molecularnodes': mn_version,
                         'python': '.'.join(map(str, sys.version_info[:2])),
                         'numpy': numpy.__version__, 'biotite': biotite.__version__}
        lock = json.loads((self.package / 'runtime.lock.json').read_text())
        if cfg['ccd_sha256'] != lock['ccd_sha256']:
            raise RuntimeError('Development CCD does not match runtime.lock.json')
        if any(self.versions[key] != lock[key] for key in self.versions):
            raise RuntimeError('Development runtime does not match runtime.lock.json: ' + str(self.versions))
        self.binary = Path(bpy.app.binary_path or bpy.__file__)
        self.binary_sha256 = digest_file(self.binary)
        self.modules = (molecularnodes, biotite, numpy)
        os.environ['PRESS_BIOTITE_CCD_PATH'] = cfg['ccd']
        self.capture = worker_ccd.initialize(parent=Path(cfg['root']), expected=lock['ccd_sha256'])

    def verify_sources(self):
        if source_files(self.package) != self.expected:
            raise RuntimeError('Source snapshot changed during preview; restart the worker')

    def render(self, args, *, warm_worker=None):
        from . import blender_custom_render as render
        self.verify_sources()
        render._render(args)
        self.verify_sources()
        loaded = {}
        for name, module in list(sys.modules.items()):
            raw = getattr(module, '__file__', None)
            if not raw:
                continue
            path = Path(raw).resolve()
            owned = name.startswith('mol_blender') or (name.split('.')[0] + '.py') in self.expected
            if owned and path.suffix == '.py':
                if not path.is_relative_to(self.package):
                    raise RuntimeError('Stale/foreign renderer import: ' + str(path))
                relative = path.relative_to(self.package).as_posix()
                digest = digest_file(path)
                if digest != self.expected.get(relative):
                    raise RuntimeError('Loaded source digest mismatch: ' + str(path))
                loaded[name] = {'path': str(path), 'sha256': digest}
        manifest_path = Path(args.run_manifest)
        manifest = json.loads(manifest_path.read_text())
        manifest['development_source'] = {
            'sha256': self.cfg['identity']['sha256'], 'loaded_modules': loaded,
            'runtime_versions': self.versions, 'blender_binary': str(self.binary),
            'blender_binary_sha256': self.binary_sha256, 'pid': os.getpid(),
            'ccd_sha256': self.cfg['ccd_sha256'],
            'runtime_modules': {module.__name__: {'path': module.__file__, 'sha256': digest_file(Path(module.__file__))}
                                for module in self.modules},
        }
        if warm_worker is not None:
            manifest['warm_worker'] = warm_worker
        manifest_path.write_text(json.dumps(manifest, indent=2))

    def close(self):
        self.capture.close()
