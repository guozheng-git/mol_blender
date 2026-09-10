"""Correction contracts. Every external runtime boundary is injected."""
import hashlib
import json
from pathlib import Path
import pytest
from blender_render import engine


def package(tmp_path):
    root = tmp_path / 'package'
    root.mkdir()
    (root / 'runtime.lock.json').write_text(json.dumps({'ccd_sha256': hashlib.sha256(b'ccd').hexdigest()}))
    (root / 'engine.py').write_text('# sealed source\n')
    return root


def test_recursive_inventory_and_latched_mutation(tmp_path):
    root = package(tmp_path)
    (root / 'nested').mkdir()
    (root / 'nested' / 'engine.py').write_text('# different file\n')
    (root / 'nested' / 'native.so').write_bytes(b'native')
    assert {'engine.py', 'nested/engine.py', 'nested/native.so'} <= engine.generate_manifest(root)['files'].keys()
    copied = tmp_path/'staged'
    engine.stage_package(root, copied)
    assert engine.package_inventory(root) == engine.package_inventory(copied)
    seal = engine.EngineSeal(root)
    (root / 'nested' / 'engine.py').write_text('# changed\n')
    with pytest.raises(engine.RestartRequired): seal.check()
    (root / 'nested' / 'engine.py').write_text('# different file\n')
    with pytest.raises(engine.RestartRequired): seal.check()
    engine.EngineSeal(root).check()


def test_stale_packaged_manifest_rejected(tmp_path):
    root = package(tmp_path)
    (root / 'engine-manifest.json').write_text(json.dumps(engine.generate_manifest(root)))
    (root / 'engine.py').write_text('# stale manifest\n')
    with pytest.raises(engine.RestartRequired): engine.EngineSeal(root)


def test_inventory_rejects_symlinks_and_includes_new_resources(tmp_path):
    root = package(tmp_path)
    seal = engine.EngineSeal(root)
    (root / 'shader.bin').write_bytes(b'shader')
    with pytest.raises(engine.RestartRequired): seal.check()
    (root / 'alias.py').symlink_to(root / 'engine.py')
    with pytest.raises(ValueError, match='symlink'): engine.generate_manifest(root)


@pytest.mark.parametrize('data,verified', [(None, False), (b'bad', False), (b'ccd', True)])
def test_ccd_readiness_is_read_only(tmp_path, data, verified):
    from blender_render.engine import ccd_observation
    path = tmp_path / 'components.bcif'
    if data is not None: path.write_bytes(data)
    expected = hashlib.sha256(b'ccd').hexdigest()
    observed = ccd_observation(path=path, expected=expected)
    assert observed['verified'] is verified
    assert observed['path'] == str(path)
    assert (path.read_bytes() if path.exists() else None) == data


def test_controller_closures_do_not_import_runtime(tmp_path, monkeypatch):
    forbidden = {'bpy', 'molecularnodes', 'modal', 'biotite'}
    calls = []
    original = engine.importlib.import_module
    def load(name, *args):
        calls.append(name)
        assert name.split('.')[0] not in forbidden
        return original(name, *args)
    monkeypatch.setattr(engine.importlib, 'import_module', load)
    engine.protect('admission')
    assert calls


def test_warm_worker_rechecks_ccd_before_render(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from blender_render.blender_worker_daemon import _render_job
    from blender_render import worker_ccd
    path = tmp_path/'components.bcif'
    path.write_bytes(b'ccd')
    ccd = SimpleNamespace(_CCD_FILE=path)
    ccd.set_ccd_path = lambda value:setattr(ccd, '_CCD_FILE', Path(value))
    capture = worker_ccd.initialize(parent=tmp_path, ccd_module=ccd, expected=hashlib.sha256(b'ccd').hexdigest())
    monkeypatch.setattr(engine, '_runtime_versions', lambda:{'versions':{}, 'verified':True, 'mismatches':[]})
    try:
        assert engine.runtime_observation()['verified']
        ccd._CCD_FILE.chmod(0o600)
        ccd._CCD_FILE.write_bytes(b'corrupt after readiness')
        ccd._CCD_FILE.chmod(0o400)
        with pytest.raises(RuntimeError, match='runtime mismatch'):
            _render_job(SimpleNamespace(_render=lambda *a:pytest.fail('corrupt CCD rendered')),
                        {'expected_engine':engine.engine_identity()})
        assert path.read_bytes() == b'ccd'
    finally:
        capture.close()


def test_worker_replacement_cannot_clear_controller_latch(tmp_path, monkeypatch):
    from blender_render.render_worker_manager import PersistentBlenderWorker
    seal = engine.EngineSeal(package(tmp_path))
    seal.latched = True
    monkeypatch.setattr(engine, 'process_seal', lambda:seal)
    worker = PersistentBlenderWorker(root_dir=tmp_path/'worker', blender_exe='unused',
        custom_script=tmp_path/'unused', use_apptainer=False, apptainer_image='')
    monkeypatch.setattr(worker, '_send_request_locked', lambda *a, **k:pytest.fail('latched controller contacted worker'))
    with pytest.raises(engine.RestartRequired): worker._negotiate_locked(seal.identity, [])


def test_action_preload_checks_actual_bytes_after_loading(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = package(tmp_path)
    seal = engine.EngineSeal(root)
    monkeypatch.setattr(engine, 'process_seal', lambda:seal)
    def load(name):
        (root/'engine.py').write_text('# mutation during preload')
        return SimpleNamespace(__file__=str(root/'engine.py'))
    monkeypatch.setattr(engine.importlib, 'import_module', load)
    with pytest.raises(engine.RestartRequired): engine.protect('admission')
    assert seal.latched


def test_warm_worker_rejects_source_mutation_before_callback(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from blender_render.blender_worker_daemon import _render_job
    root = package(tmp_path)
    seal = engine.EngineSeal(root)
    monkeypatch.setattr(engine, 'process_seal', lambda:seal)
    monkeypatch.setattr(engine, 'runtime_observation', lambda:{'verified':True})
    expected = engine.engine_identity()
    (root/'engine.py').write_text('# changed after worker cached identity')
    request = dict(expected_engine=expected, structure_path='fixture.cif', view_json='view.json',
                   style_json='style.json', output_png='result.png', run_manifest='manifest.json')
    with pytest.raises(engine.RestartRequired):
        _render_job(SimpleNamespace(_render=lambda *a:pytest.fail('changed code rendered')), request)
    assert seal.latched


@pytest.mark.parametrize('initial', [None, b'corrupt', b'ccd'])
def test_explicit_setup_provisions_and_verifies_locked_ccd(tmp_path, monkeypatch, initial):
    import runpy
    import sys
    from types import SimpleNamespace
    source = Path(engine.__file__).with_name('runtime_initialize.py')
    script = tmp_path/'runtime_initialize.py'
    script.write_bytes(source.read_bytes())
    (tmp_path/'runtime.lock.json').write_text(json.dumps({'ccd_sha256':hashlib.sha256(b'ccd').hexdigest()}))
    path = tmp_path/'components.bcif'
    if initial is not None: path.write_bytes(initial)
    calls = []
    def provision():
        calls.append(True)
        path.write_bytes(b'ccd')
    setup = SimpleNamespace(main=provision)
    biotite = SimpleNamespace(__version__='1.6.0', setup_ccd=setup)
    for name, value in {'bpy':SimpleNamespace(app=SimpleNamespace(version=(5,0,1))),
        'biotite':biotite, 'biotite.structure':SimpleNamespace(),
        'biotite.structure.info':SimpleNamespace(ccd=SimpleNamespace(_CCD_FILE=path)),
        'biotite.setup_ccd':setup, 'numpy':SimpleNamespace(__version__='1.26.4'),
        'molecularnodes':SimpleNamespace()}.items():
        monkeypatch.setitem(sys.modules, name, value)
    monkeypatch.delenv('PRESS_BIOTITE_CCD_PATH', raising=False)
    if initial == b'corrupt':
        with pytest.raises(RuntimeError, match='CCD snapshot'): runpy.run_path(str(script))
        assert path.read_bytes() == initial
    else:
        runpy.run_path(str(script))
        assert path.read_bytes() == b'ccd'
    assert len(calls) == int(initial is None)
