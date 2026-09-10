"""Build the installed engine identity from exactly the packaged assets."""
import runpy
from pathlib import Path
import json
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildEngine(build_py):
    def run(self):
        source = Path(__file__).parent / 'blender_render'
        inventory_api = runpy.run_path(str(source / 'engine.py'))
        generate = inventory_api['generate_manifest']
        inventory = generate(source)['files']
        root = Path(self.build_lib) / 'blender_render'
        # This is setuptools-owned build output, never the source checkout.
        if root.resolve() == source.resolve():
            raise ValueError('Build output must be separate from package source')
        if root.exists():
            shutil.rmtree(root)
        super().run()
        inventory_api['stage_package'](source, root)
        (root / 'engine-manifest.json').write_text(json.dumps(generate(root), sort_keys=True, indent=2) + '\n')


setup(cmdclass={'build_py': BuildEngine})
