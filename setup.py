"""Build the installed engine identity from exactly the packaged assets."""
import runpy
from pathlib import Path
import json
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildEngine(build_py):
    def run(self):
        super().run()
        root = Path(self.build_lib) / 'blender_render'
        generate = runpy.run_path(str(root / 'engine.py'))['generate_manifest']
        (root / 'engine-manifest.json').write_text(json.dumps(generate(root), sort_keys=True, indent=2) + '\n')


setup(cmdclass={'build_py': BuildEngine})
