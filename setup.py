"""Build the installed engine identity from exactly the packaged assets."""
import runpy
from pathlib import Path
import json
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildEngine(build_py):
    def run(self):
        project = Path(__file__).parent
        defaults = runpy.run_path(str(project / 'scripts/sync_render_defaults.py'))
        defaults['synchronize'](project, check=True)
        source = Path(__file__).parent / 'mol_blender'
        inventory_api = runpy.run_path(str(source / 'engine.py'))
        generate = inventory_api['generate_manifest']
        inventory = generate(source)['files']
        root = Path(self.build_lib) / 'mol_blender'
        # Clear both packages so incremental builds cannot retain removed code.
        # This is setuptools-owned build output, never the source checkout.
        for package in ('mol_blender', 'co_pymol'):
            target = Path(self.build_lib) / package
            if target.resolve() == (project / package).resolve():
                raise ValueError('Build output must be separate from package source')
            if target.exists():
                shutil.rmtree(target)
        super().run()
        inventory_api['stage_package'](source, root)
        (root / 'engine-manifest.json').write_text(json.dumps(generate(root), sort_keys=True, indent=2) + '\n')


setup(cmdclass={'build_py': BuildEngine})
