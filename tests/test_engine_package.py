import hashlib
import json
import os
from pathlib import Path

from blender_render import engine, telemetry


def test_packaged_manifest_and_import_locations():
    root = Path(engine.__file__).resolve().parent
    expected = engine.generate_manifest(root)
    manifest = root/'engine-manifest.json'
    if os.environ.get('C3_INSTALLED_SITE'):
        site = Path(os.environ['C3_INSTALLED_SITE'])
        for module in (engine, telemetry):
            assert Path(module.__file__).is_relative_to(site)
        assert manifest.is_file()
    if manifest.exists():
        assert json.loads(manifest.read_text()) == expected
    assert engine.engine_identity() == expected


def test_identity_cache_and_source_change(tmp_path):
    from shutil import copy2
    root = Path(engine.__file__).resolve().parent
    (tmp_path/'package').mkdir()
    for name in engine.generate_manifest(root)['files']:
        (tmp_path/'package'/name).parent.mkdir(parents=True, exist_ok=True)
        copy2(root/name, tmp_path/'package'/name)
    first = engine.generate_manifest(tmp_path/'package')
    script = tmp_path/'package'/'geometry.py'
    script.write_text(script.read_text()+'\n# deliberate source variant\n')
    assert engine.generate_manifest(tmp_path/'package')['engine_build'] != first['engine_build']
    loaded = engine.engine_identity()
    loaded['engine_build'] = 'tampered return value'
    assert engine.engine_identity()['engine_build'] != loaded['engine_build']
