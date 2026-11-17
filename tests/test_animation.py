import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

import pytest

from blender_render import animation
from blender_render.spec import resolve_options


def test_defaults_hash_capabilities_and_still_compatibility():
    still = resolve_options({}, {})
    assert 'animation' not in still['style_doc']['output']
    assert still['preset'] == 'mint_sky_assembly'
    rock = resolve_options({'animation': 'rock'}, {})
    assert rock['style_doc']['output']['animation'] == dict(mode='rock', duration=3, fps=24, rock_angle=12, gif=False)
    spin = resolve_options({'animation': '360', 'width': 640, 'height': 480}, {})
    assert len(animation.angles(spin['style_doc']['output']['animation'])) == 144
    assert rock['render_spec']['sha256'] != still['render_spec']['sha256']
    assert {'animation.camera_orbit', 'encoding.ffmpeg'} <= set(rock['render_spec']['required_capabilities'])
    rough = resolve_options({'animation': 'rock', 'rough': True}, {})
    assert rough['style_doc']['output']['animation'] == rock['style_doc']['output']['animation']


@pytest.mark.parametrize('options', [
    {'animation':'bad'}, {'animation':'none','gif':False}, {'animation':'rock','fps':True},
    {'animation':'rock','fps':61}, {'animation':'rock','duration':float('nan')},
    {'animation':'rock','duration':16}, {'animation':'rock','duration':.1},
    {'animation':'360','rock_angle':12}, {'animation':'rock','rock_angle':0},
    {'animation':'rock','raw_view':True}, {'animation':'rock','width':8192,'height':8192},
    {'gif':True}, {'animation':'360','duration':15,'fps':60},
])
def test_invalid_before_dispatch(options):
    with pytest.raises(ValueError):
        resolve_options(options, {})


@pytest.mark.parametrize('mode', ['rock', 'path'])
def test_ten_gib_decoded_frame_budget(mode):
    view = [1., 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -50, 0, 0, 0, 40, 60, -20]
    config = ({'mode': 'rock', 'fps': 16, 'duration': 8} if mode == 'rock'
              else {'mode': 'path', 'fps': 16, 'path': [view] * 128})
    # 128 frames at 5120 x 4096 are exactly 10 GiB decoded, in both modes.
    assert animation.normalize(config, {'width': 5120, 'height': 4096})
    with pytest.raises(ValueError, match='10 GiB'):
        animation.normalize(config, {'width': 5120, 'height': 4097})


def test_full_size_cutaway_passes_before_dispatch():
    view = [1., 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -50, 0, 0, 0, 40, 60, -20]
    result = resolve_options({'preset': 'cyan_mint_cutaway', 'overrides': {'output': {
        'width': 1280, 'height': 1115, 'animation': {
            'mode': 'path', 'fps': 24, 'path': [view] * 276,
            'anchor': 168, 'lid': [1.] * 168 + [0.] * 108,
        },
    }}}, {})
    output = result['style_doc']['output']
    assert (output['width'], output['height']) == (1280, 1115)
    assert output['animation']['duration'] == 11.5


@pytest.mark.parametrize('preset', [p['id'] for p in __import__('blender_render.presets', fromlist=['catalog']).catalog()])
def test_every_preset_resolves_animation(preset):
    result = resolve_options({'preset':preset,'animation':'360','duration':1,'fps':4,'width':320,'height':240}, {})
    assert result['style_doc']['output']['animation']['mode'] == '360'


@pytest.fixture
def archive(tmp_path):
    from PIL import Image
    frames = tmp_path/'frames'; frames.mkdir()
    for i, color in enumerate(('red','green','blue','yellow')):
        Image.new('RGBA',(32,24),color).save(frames/f'{i:04d}.png')
    path=tmp_path/'movie.frames.zip'
    animation.package_frames(frames,path,dict(mode='360',fps=4,duration=1,gif=True),32,24)
    return path


@pytest.mark.parametrize('attack', ['traversal','duplicate','dimensions','checksum','oversize','symlink'])
def test_archive_rejected_before_movie_mutation(archive, attack):
    with zipfile.ZipFile(archive) as z:
        contents={i.filename:z.read(i) for i in z.infolist()}
    meta=json.loads(contents['frames.json'])
    if attack=='dimensions': meta['width']=2**30
    if attack=='checksum': meta['files'][0]['sha256']='0'*64
    if attack=='oversize': meta['frames']=361
    contents['frames.json']=json.dumps(meta).encode()
    with zipfile.ZipFile(archive,'w') as z:
        for name,raw in contents.items(): z.writestr(name,raw)
        if attack=='traversal': z.writestr('../escape.png',b'x')
        if attack=='duplicate': z.writestr('0000.png',contents['0000.png'])
        if attack=='symlink':
            info=zipfile.ZipInfo('link');info.external_attr=0o120777<<16;z.writestr(info,'/etc/passwd')
    with pytest.raises(ValueError):
        with animation.unpack(archive): pytest.fail('unsafe archive accepted')


def test_unpack_cleanup_and_metadata(archive):
    with animation.unpack(archive) as (directory,meta):
        assert meta['frames']==4
        assert len(list(directory.glob('*.png')))==4
    assert not directory.exists()


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='Needs FFmpeg')
def test_real_export_transparency_and_later_gif(archive,tmp_path):
    from PIL import Image
    animation.export(archive,tmp_path/'out.mp4')
    animation.export(archive,tmp_path/'out.gif')
    for name in ('out.mp4','out.gif'):
        stream=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-of','json',str(tmp_path/name)]))['streams'][0]
        assert int(stream['nb_read_frames'])==4
        assert float(stream['duration'])==pytest.approx(1)
    with Image.open(tmp_path/'out.gif') as im: assert im.info['loop']==0
    frames=tmp_path/'alpha';frames.mkdir()
    for i in range(4): Image.new('RGBA',(31,23),(0,0,0,0)).save(frames/f'{i:04d}.png')
    animation.encode(frames,dict(fps=4,frames=4),tmp_path/'white.gif')
    with Image.open(tmp_path/'white.gif') as im: assert im.convert('RGB').getpixel((0,0))==(255,255,255)
    animation.encode(frames,dict(fps=4,frames=4),tmp_path/'odd.mp4')


@pytest.mark.skipif(sys.platform != 'darwin' or not shutil.which('swift') or not shutil.which('ffmpeg'),
                    reason='Needs the native macOS decoder and FFmpeg')
def test_mp4_colours_in_native_macos_decoder(tmp_path):
    from PIL import Image
    colours = [(0, 0, 0), (50, 137, 185), (44, 184, 128), (250, 18, 166)]
    frames = tmp_path / 'colour-patches'
    frames.mkdir()
    for index, colour in enumerate(colours):
        Image.new('RGB', (64, 48), colour).save(frames / f'{index:04d}.png')
    output = tmp_path / 'colours.mp4'
    animation.encode(frames, dict(fps=4, frames=4), output)
    result = subprocess.check_output(['swift', '-suppress-warnings',
                                      str(Path(__file__).with_name('native_video_colours.swift')),
                                      str(output)], text=True, timeout=60)
    decoded = json.loads(result)
    assert len(decoded) == len(colours)
    # Allow normal YUV quantization, but catch wrong colour matrices/channels.
    assert max(abs(a - b) for actual, expected in zip(decoded, colours)
               for a, b in zip(actual, expected)) <= 8


@pytest.mark.parametrize('cancel', [True, False])
def test_encoder_interrupts_and_reaps_child(cancel):
    with pytest.raises((TimeoutError,RuntimeError)):
        animation._run([sys.executable,'-c','import time;time.sleep(20)'],
                       deadline=time.monotonic()+.05, cancelled=lambda:cancel)


def test_pymol_import_and_export_without_submission(archive,tmp_path):
    from blender_render.pymol_movie import load_movie,remember,movie_export
    class Cmd:
        def __init__(self): self.calls=[]
        def _call_in_gui_thread(self,fn): return fn()
        def _call_with_opengl_context(self,fn): return fn()
        def get_view(self): return [1]*18
        def get_state(self): return 2
        def __getattr__(self,name):
            return lambda *a,**k:self.calls.append((name,a,k))
    cmd=Cmd();remember(cmd,archive)
    assert load_movie(cmd,archive)=='inline_preview'
    assert ('mset',('2 x4',),{}) in cmd.calls
    assert len([x for x in cmd.calls if x[0]=='load_png'])==4
    assert ('set_view',([1]*18,),{}) in cmd.calls
    if shutil.which('ffmpeg'):
        assert Path(movie_export(tmp_path/'later.gif',_self=cmd)).exists()
    archive.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'): movie_export(tmp_path/'fail.gif',_self=cmd)


def test_animation_capabilities_are_in_local_engine():
    from blender_render.engine import CAPABILITIES
    resolved = resolve_options({'animation': 'rock'}, {})
    assert set(resolved['render_spec']['required_capabilities']) <= set(CAPABILITIES)


def test_animation_capability_diagnostics_are_preserved():
    from blender_render.compatibility import sanitize
    from blender_render.engine import CAPABILITIES
    evidence = dict(version=1, reason='capability_missing', phase='negotiation',
                    component='engine', before_execution=True,
                    expected={'capabilities': list(CAPABILITIES)}, observed={}, log_refs=[])
    assert sanitize(evidence) == evidence


def test_orbit_angles_loop_without_duplicate_endpoint():
    rock = animation.angles(dict(mode='rock', duration=1, fps=4, rock_angle=10))
    assert rock == pytest.approx([0, 10, 0, -10])
    assert animation.angles(dict(mode='360', duration=1, fps=4)) == [0, 90, 180, 270]


def test_none_disables_animation_in_authored_style():
    style = resolve_options({'animation': 'rock', 'gif': True}, {})['style_doc']
    result = resolve_options({'style_doc': style, 'animation': 'none'}, {})
    assert 'animation' not in result['style_doc']['output']


def test_reexport_never_overwrites_existing_file(archive, tmp_path):
    output = tmp_path/'existing.gif'
    output.write_bytes(b'existing')
    with pytest.raises(ValueError, match='already exists'):
        animation.export(archive, output)
    assert output.read_bytes() == b'existing'


def test_source_inventory_ignores_mutable_finder_metadata(tmp_path):
    from blender_render import engine, developer
    (tmp_path/'animation.py').write_text('source')
    (tmp_path/'.DS_Store').write_bytes(b'finder')
    assert set(engine.package_inventory(tmp_path)) == {'animation.py'}
    assert set(developer.source_files(tmp_path)) == {'animation.py'}
