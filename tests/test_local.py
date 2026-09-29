import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from blender_render import cli, local, local_bridge, developer, pymol_bridge_plugin
from blender_render.config import save_config
from blender_render.export import from_file


def bundle():
    return {'cif_text': 'data_test\nloop_\n_atom_site.id\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n1 0 0 0\n',
            'view': {'pymol_view': [1.,0.,0.,0.,1.,0.,0.,0.,1.,0.,0.,-30.,0.,0.,0.,10.,100.,0.]}}


@pytest.fixture
def local_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('BLENDER_RENDER_HOME', str(tmp_path / 'config'))
    blender = tmp_path / 'blender'; blender.touch()
    ccd = tmp_path / 'components.bcif'; ccd.touch()
    deps = tmp_path / 'deps'; deps.mkdir()
    save_config({'blender':str(blender), 'ccd':str(ccd), 'runtime_paths':[str(deps)]})
    return tmp_path


def test_local_render_keeps_outputs_and_resolves_saved_preset(local_runtime, monkeypatch):
    from blender_render import authoring, core
    from blender_render.config import config_root
    from blender_render.presets import preset_path
    authoring.save_preset('user/test', core.load_and_validate_style_json(preset_path('pastel')),
                         config_root()/'presets', description='test')
    calls = []
    def preview(**kwargs):
        calls.append(kwargs)
        assert json.loads(kwargs['bundle'].read_text()) == bundle()
        kwargs['output'].mkdir()
        for name in ('render.png','render.blend','resolved-style.json','manifest.json'):
            (kwargs['output']/name).write_bytes(b'render')
        return {'sha256':'source', 'png_sha256':'image'}
    monkeypatch.setattr(developer, 'preview', preview)
    out = local_runtime / 'result.png'
    result = local.render_payload(bundle(), output=out, options={'preset':'user/test','save_blend':True})
    assert out.read_bytes() == b'render'
    assert Path(result['outputs']['render.blend']).is_file()
    assert not calls[0]['bundle'].exists()
    assert calls[0]['options']['preset'] == 'user/test'
    with pytest.raises(ValueError, match='already exists'):
        local.render_payload(bundle(), output=out)
    assert len(calls) == 1


def test_invalid_style_never_launches_blender(local_runtime, monkeypatch):
    monkeypatch.setattr(developer, 'preview', lambda **kw: pytest.fail('invalid style launched'))
    with pytest.raises(ValueError, match='Unknown preset'):
        local.render_payload(bundle(), output=local_runtime/'bad.png', options={'preset':'absent'})


def test_failed_preview_has_no_success_png(local_runtime, monkeypatch):
    def fail(**kw):
        kw['output'].mkdir()
        (kw['output']/'preview.log').write_text('failure')
        raise RuntimeError('failed')
    monkeypatch.setattr(developer,'preview',fail)
    out=local_runtime/'failed.png'
    with pytest.raises(RuntimeError): local.render_payload(bundle(),output=out)
    assert not out.exists()
    assert (out.with_suffix('.render')/'preview.log').read_text() == 'failure'


def test_cli_style_and_camera_options_reach_local_renderer(monkeypatch, tmp_path):
    style = tmp_path/'style.json'; style.write_text('{}')
    calls=[]
    monkeypatch.setattr(local,'render_file',lambda *a,**kw: calls.append((a,kw)))
    args=cli.parser().parse_args(['render','x.pse','--style-json',str(style),'--no-preserve-view','--samples','16','--no-transparent'])
    cli.execute(args)
    assert calls[0][1]['options'] == {'style_doc':{},'preserve_view':False,'samples':16,'transparent_background':False}


def test_bridge_uses_configured_python_and_current_package(local_runtime, monkeypatch):
    save_config({'local_python':'/test/python'})
    calls=[]
    def run(command, **kwargs):
        assert json.loads(Path(command[4]).read_text()) == bundle()
        calls.append((command,kwargs))
        return SimpleNamespace(returncode=0,stdout='{"status":"completed"}',stderr='')
    monkeypatch.setattr(local_bridge.subprocess,'run',run)
    result=local_bridge.render_payload(bundle(),output='new.png',options={'preset':'pastel','preserve_view':False,'save_blend':True})
    assert result['status']=='completed'
    command,kw=calls[0]
    assert command[0]=='/test/python'
    assert '--no-preserve-view' in command and '--save-blend' in command
    assert kw['env']['PYTHONPATH'] == str(Path(local_bridge.__file__).resolve().parent.parent)


def test_pymol_preserves_context_and_loads_generated_png(monkeypatch,tmp_path):
    calls=[]
    context=SimpleNamespace(load_png=lambda *a,**kw:calls.append(('load',a)))
    def export(cmd,*args,**kwargs):
        assert cmd is context
        return bundle()
    monkeypatch.setattr(pymol_bridge_plugin,'build_render_payload',export)
    def render(payload,**kw):
        calls.append(('render',kw)); Path(kw['output']).write_bytes(b'PNG')
    monkeypatch.setattr(local_bridge,'render_payload',render)
    result=pymol_bridge_plugin.bridge_render(_self=context,output_dir=str(tmp_path),preserve_view=0,color_follow=0)
    assert Path(result).is_file()
    assert calls[0][1]['options']['preserve_view'] is False
    assert calls[0][1]['options']['color_follow'] is False
    assert calls[-1][0]=='load'


def test_pymol_run_shared_namespace(monkeypatch,tmp_path):
    import sys
    source=Path(pymol_bridge_plugin.__file__)
    monkeypatch.setitem(sys.modules,'pymol',SimpleNamespace(cmd=SimpleNamespace(extend=lambda *a:None)))
    namespace={'__file__':str(tmp_path/'pymol/__init__.py'),'__script__':str(source)}
    exec(compile(source.read_text(),str(source),'exec'),namespace)
    assert namespace['_PACKAGE_ROOT']==str(source.parent.parent)


def test_reference_outputs_full_valid_style_and_refuses_overwrite(tmp_path):
    from PIL import Image
    from blender_render import core
    image=tmp_path/'reference.png'; Image.new('RGB',(16,16),'white').save(image)
    obs=tmp_path/'observations.json'
    obs.write_text(json.dumps({'representations':['cartoon','sticks'],'material_family':'clay',
                              'palette':['pink'],'required_capabilities':[],'base_preset':'pastel',
                              'overrides':{'world':{'strength':0.7}}}))
    target=tmp_path/'candidate.json'
    args=developer.parser().parse_args(['reference',str(image),'--observations',str(obs),'--style-output',str(target)])
    result=developer.execute(args)
    style=core.load_and_validate_style_json(target)
    assert style['world']['strength']==0.7
    assert result['visual_brief']['reference']['dimensions']==[16,16]
    with pytest.raises(FileExistsError): developer.execute(args)


def test_mcp_native_image_and_local_render_route(tmp_path,monkeypatch):
    pytest.importorskip('fastmcp')
    from fastmcp import Client
    from blender_render.mcp_server import create_stdio
    from PIL import Image
    path=tmp_path/'image.png'; Image.new('RGB',(8,8),'red').save(path)
    calls=[]
    monkeypatch.setattr(local,'render_file',lambda *a,**kw: calls.append((a,kw)) or {'status':'completed','outputs':{'render.png':str(path)}})
    async def check():
        async with Client(create_stdio()) as client:
            assert {t.name for t in await client.list_tools()} == {'presets','inspect_style','render_structure','read_render'}
            await client.call_tool('render_structure',{'input_path':'structure.cif','output_path':str(path)})
            image=await client.call_tool('read_render',{'path':str(path)})
            assert image.content[0].type=='image'
            import base64
            assert base64.b64decode(image.content[0].data)==path.read_bytes()
    asyncio.run(check())
    assert calls[0][0] == ('structure.cif',)
