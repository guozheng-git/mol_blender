import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_render import authoring, cli, core, developer, local, local_bridge
from blender_render.scene import edit as scene_edit
from blender_render.presets import preset_path


@pytest.fixture
def style():
    return core.load_and_validate_style_json(preset_path('mint_sky_assembly'))


def test_bindings_preserve_unedited_style_and_report_unmapped_changes(style):
    metadata = {'style': style, 'baseline': {'roughness': .4, 'color': [.1, .2, .3, 1], 'mesh': 'old'},
                'bindings': {'roughness': {'path': ['materials', 'blue', 'roughness'], 'kind': 'value'},
                             'color': {'path': ['materials', 'blue', 'base_color'], 'kind': 'hex'}}}
    unchanged, changes, unsupported = scene_edit.apply_bindings(metadata, dict(metadata['baseline'], roughness=.40000001))
    assert unchanged == style and not changes and not unsupported
    edited, changes, unsupported = scene_edit.apply_bindings(metadata, {'roughness': .7, 'color': [1, 0, .5, 1], 'mesh': 'new'})
    assert edited['materials']['blue']['base_color'] == '#FF0080'
    assert edited['materials']['blue']['roughness'] == .7
    assert style['materials']['blue']['roughness'] == .4
    assert len(changes) == 2 and unsupported == ['mesh']


@pytest.mark.parametrize('value,kind', [([2, 0, 0, 1], 'hex'), ([.2, .2, .2, .5], 'hex'),
                                      ([.2, .3, .2, 1], 'gray'), ([float('nan'), 0, 0], 'rgb')])
def test_colors_cannot_silently_drop_hdr_alpha_or_tint(value, kind):
    with pytest.raises(ValueError):
        scene_edit.convert(value, kind)


def test_removed_or_added_bound_values_are_not_silently_accepted(style):
    metadata = {'style': style, 'baseline': {'a': 1},
                'bindings': {'a': {'path': ['output', 'samples'], 'kind': 'value'}}}
    result, changes, unsupported = scene_edit.apply_bindings(metadata, {'b': 2})
    assert len(unsupported) == 2 and unsupported[-1] == 'b'
    assert 'removed' in unsupported[0] and not changes and result == style


def test_derived_material_controls_must_agree_and_emission_keeps_socket_color(style):
    base = {'path': ['materials', 'blue', 'roughness'], 'kind': 'value'}
    metadata = {'style': style, 'baseline': {'base': .4, 'chain': .4, 'emission': 0, 'color': [1, 1, 1, 1]},
                'bindings': {'base': base, 'chain': base,
                    'emission': {'path': ['materials', 'blue', 'emission_strength'], 'kind': 'value'},
                    'color': {'path': ['materials', 'blue', 'emission_color'], 'kind': 'hex'}}}
    _, edits, unsupported = scene_edit.apply_bindings(metadata, dict(metadata['baseline'], base=.8))
    assert not edits and 'consistently' in unsupported[0]
    result, edits, unsupported = scene_edit.apply_bindings(metadata, dict(metadata['baseline'], base=.8, chain=.8, emission=2))
    assert not unsupported and len(edits) == 2
    assert result['materials']['blue']['emission_color'] == '#FFFFFF'


def test_relative_camera_declares_its_capability(style):
    from blender_render.presets import required_capabilities
    from blender_render.engine import CAPABILITIES
    style['camera']['relative_pose'] = {}
    assert 'camera.relative_pose' in required_capabilities(style)
    assert 'camera.relative_pose' in CAPABILITIES


def inspection(style, unsupported=None):
    return {'blend_sha256': 'blend-digest', 'extractor_source_sha256': 'source-digest',
            'preserve_input': {'style': style, 'changes': [{'setting': 'world.color', 'value': '#445566'}],
                               'unsupported': unsupported or [], 'ignored': [], 'source_engine': 'engine-digest'}}


def test_preset_import_refuses_partial_but_writes_reviewable_report(tmp_path, monkeypatch, style):
    monkeypatch.setattr(scene_edit, 'inspect_scene', lambda *a, **k: inspection(style, ['object/o1/geometry']))
    report = tmp_path / 'report.json'
    with pytest.raises(ValueError, match='No preset saved'):
        scene_edit.save_from_blend('edited.blend', 'user/look', catalog_dir=tmp_path, report=report)
    assert json.loads(report.read_text())['unsupported'] == ['object/o1/geometry']
    assert not (tmp_path / 'user/look.json').exists()
    result = scene_edit.save_from_blend('edited.blend', 'user/look', catalog_dir=tmp_path, allow_partial=True)
    assert result['partial'] and result['revision'] == 1
    saved = authoring.inspect_preset('user/look', tmp_path)
    assert saved['provenance']['blend_sha256'] == 'blend-digest'
    assert saved['provenance']['unsupported'] == ['object/o1/geometry']
    with pytest.raises(ValueError, match='already exists'):
        scene_edit.save_from_blend('edited.blend', 'user/look', catalog_dir=tmp_path)
    second = scene_edit.save_from_blend('edited.blend', 'user/look', catalog_dir=tmp_path, replace=True, allow_partial=True)
    assert second['revision'] == 2


def test_invalid_extracted_style_is_never_saved(tmp_path, monkeypatch, style):
    style['materials']['blue']['unsupported'] = 1
    monkeypatch.setattr(scene_edit, 'inspect_scene', lambda *a, **k: inspection(style))
    with pytest.raises(core.StyleValidationError):
        scene_edit.save_from_blend('edited.blend', 'user/look', catalog_dir=tmp_path)
    assert not (tmp_path / 'user/look.json').exists()


def test_existing_report_and_unsafe_ids_fail_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(scene_edit, 'inspect_scene', lambda *a, **k: pytest.fail('Blender should not launch'))
    report = tmp_path / 'report.json'
    report.write_text('keep')
    with pytest.raises(ValueError, match='Report already exists'):
        scene_edit.save_from_blend('test.blend', 'user/look', report=report)
    with pytest.raises(ValueError, match='Local preset ID'):
        scene_edit.save_from_blend('test.blend', '../bad')
    assert report.read_text() == 'keep'


def test_relative_pose_validation(style):
    style['camera'] = {'mode': 'override', 'projection': 'ORTHO', 'relative_pose': {
        'position': [0, 0, 3], 'rotation_deg': [0, 0, 0], 'ortho_scale': 2}, 'sensor_fit': 'HORIZONTAL'}
    core.validate_style_document(style)
    style['camera']['fit'] = True
    with pytest.raises(core.StyleValidationError, match='cannot be combined'):
        core.validate_style_document(style)
    del style['camera']['fit']
    style['camera']['relative_pose']['position'][0] = float('inf')
    with pytest.raises(core.StyleValidationError, match='finite'):
        core.validate_style_document(style)


def test_open_scene_is_explicit_separate_and_disables_embedded_scripts(tmp_path, monkeypatch):
    scene = tmp_path / 'my edited scene.blend'
    scene.write_bytes(b'test')
    monkeypatch.setattr(scene_edit, 'blender_executable', lambda *a, **k: Path('/desktop/blender'))
    monkeypatch.setenv('PYTHONEXECUTABLE', '/foreign/python')
    calls = []
    monkeypatch.setattr(scene_edit.subprocess, 'Popen', lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(pid=42))
    result = scene_edit.open_scene(scene)
    assert result['status'] == 'launched'
    command, args = calls[0]
    assert command == ['/desktop/blender', '--factory-startup', '--disable-autoexec', str(scene)]
    assert args['start_new_session'] and 'PYTHONEXECUTABLE' not in args['env']


def test_headless_runtime_requires_explicit_desktop_executable(tmp_path, monkeypatch):
    shim = tmp_path / 'blender'
    shim.write_text('#!/usr/bin/env python\n')
    shim.chmod(0o700)
    with pytest.raises(ValueError, match='desktop Blender'):
        scene_edit.blender_executable(shim, gui=True)
    assert scene_edit.blender_executable(shim) == shim


def test_cli_and_embedded_adapters_forward_explicit_edit_options(monkeypatch, tmp_path):
    from blender_render import pymol_bridge_plugin as plugin
    calls = []
    monkeypatch.setattr(local, 'render_file', lambda *a, **kw: calls.append(kw))
    cli.execute(cli.parser().parse_args(['render', 'a.cif', '--open-blender', '--gui-blender', '/desktop/blender']))
    assert calls[-1]['open_blender'] and calls[-1]['gui_blender'] == '/desktop/blender'
    monkeypatch.setattr(plugin, 'build_render_payload', lambda *a, **kw: {})
    monkeypatch.setattr(local_bridge, 'render_payload', lambda *a, **kw: calls.append(kw))
    plugin.bridge_render(open_blender=1, auto_preview=0, _self=object())
    assert calls[-1]['open_blender']
    monkeypatch.setattr(plugin, 'build_render_payload', lambda *a, **kw: pytest.fail('must reject before export'))
    with pytest.raises(ValueError, match='local desktop'):
        plugin.bridge_render(cluster='lab', open_blender=1)


def test_embedded_export_stays_in_controller_environment(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(local_bridge, 'load_config', lambda: {'local_python': '/controller/python'})
    monkeypatch.setenv('PYTHONEXECUTABLE', '/pymol/python')
    monkeypatch.setattr(local_bridge.subprocess, 'run', lambda command, **kw:
                        calls.append((command, kw)) or SimpleNamespace(returncode=0, stdout='{"preset":"user/look"}'))
    result = local_bridge.preset_from_blend('a.blend', 'user/look', camera_policy='authored', report='edits.json')
    command, kwargs = calls[0]
    assert command[0] == '/controller/python' and command[3:5] == ['dev', 'from-blend']
    assert 'authored' in command and '--allow-partial' not in command and '--report' in command
    assert 'PYTHONEXECUTABLE' not in kwargs['env'] and result['preset'] == 'user/look'


def test_developer_command_uses_catalog_and_review_options(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(scene_edit, 'save_from_blend', lambda *a, **kw: calls.append((a, kw)))
    developer.execute(developer.parser().parse_args(['--catalog-dir', str(tmp_path), 'from-blend', 'a.blend',
                      '--preset', 'user/look', '--camera-policy', 'authored', '--allow-partial', '--replace']))
    args, kwargs = calls[0]
    assert args == (Path('a.blend'), 'user/look')
    assert kwargs['catalog_dir'] == tmp_path and kwargs['allow_partial'] and kwargs['replace']
    assert kwargs['camera_policy'] == 'authored'
