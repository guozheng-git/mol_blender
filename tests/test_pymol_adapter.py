import json
import sys
from types import SimpleNamespace
from pathlib import Path
import pytest
from blender_render import pymol_bridge_plugin as plugin


def test_pymol_run_uses_script_path_in_shared_namespace(monkeypatch, tmp_path):
    source = Path(plugin.__file__)
    namespace = {'__file__':str(tmp_path/'pymol'/'__init__.py'), '__script__':str(source)}
    monkeypatch.setitem(sys.modules, 'pymol', SimpleNamespace(cmd=SimpleNamespace(extend=lambda *a:None)))
    exec(compile(source.read_text(), str(source), 'exec'), namespace)
    assert namespace['_PACKAGE_ROOT'] == str(source.parent.parent)


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    calls = []
    class Client:
        def upload(self, payload): calls.append(('upload',payload)); return {'input_id':'input'}
        def submit(self, identifier, options): calls.append(('submit',options)); return {'job_id':'job'}
        def wait(self, *a, **kw): calls.append(('wait',a))
        def download(self, job, path):
            Path(path).write_bytes(b'PNG'); return {'outputs':{'render.png':str(path)}}
    monkeypatch.setitem(sys.modules, 'pymol', SimpleNamespace(cmd=SimpleNamespace(load_png=lambda *a,**kw:calls.append(('preview',a)))))
    monkeypatch.setattr(plugin, 'build_render_payload', lambda cmd, selection, state, **kw: {'selection':selection,'state':state})
    import blender_render.client
    monkeypatch.setattr(blender_render.client, 'configured_client', lambda profile, **kw: (calls.append(('client',profile,kw)) or Client()))
    monkeypatch.chdir(tmp_path)
    return calls


def test_no_arguments_is_local_preset_one(adapter):
    output = plugin.blender_render()
    assert Path(output).is_file()
    assert adapter[0][1] == 'local'
    assert adapter[2][1]['preset'] == 'preset_1_interface'
    assert adapter[-1][0] == 'preview'


def test_pymol_injected_command_context_is_used(adapter, monkeypatch):
    context = SimpleNamespace(load_png=lambda *a, **kw: adapter.append(('context_preview', a)))
    monkeypatch.setattr(plugin, 'build_render_payload', lambda cmd, *a, **kw: {'correct_context':cmd is context})
    plugin.blender_render(_self=context)
    assert adapter[1][1]['correct_context']
    assert adapter[-1][0] == 'context_preview'


def test_historical_pymol_invocation_on_mac(adapter, monkeypatch, tmp_path):
    from blender_render.core import _default_preset_style
    monkeypatch.setattr(sys,'platform','darwin')
    output = plugin.blender_render(selection="'(all)'", style_json=str(_default_preset_style('preset_1_interface')),
        mode="'local'", use_apptainer=1, apptainer_image='/absent/image.sif', output_dir='.', output_name='exact.png', rough=0)
    assert Path(output).name == 'exact.png'
    assert adapter[0][2]['image'] == ''
    assert adapter[1][1]['selection'] == '(all)'
    assert 'style_doc' in adapter[2][1]


def test_interactive_attach_and_options_use_same_command(adapter):
    plugin.blender_render(mode='interactive', profile='lab', attach_only=1, color_follow=1, keep_temp=1, save_blend=1)
    assert adapter[0][1] == 'lab' and adapter[0][2]['attach_only']
    assert all(adapter[2][1][k] for k in ['color_follow','keep_temp','save_blend'])
