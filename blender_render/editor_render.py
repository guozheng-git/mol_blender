"""Render a captured editor scene in an isolated background Blender process."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def blender_main(request):
    import bpy
    from blender_render import scene_capture as capture
    from blender_render.editor_controls import metadata
    from blender_render.developer import source_files
    from blender_render.cycles_device import _configure_cycles_gpu_for_custom
    if source_files(Path(__file__).resolve().parent) != request['source_files']:
        raise ValueError('Scene render source changed')
    if hashlib.sha256(Path(request['scene']).read_bytes()).hexdigest() != request['blend_sha256']:
        raise ValueError('Captured scene bytes changed')
    lock = json.loads(Path(__file__).with_name('runtime.lock.json').read_text())
    if bpy.app.version_string != lock['blender']:
        raise ValueError('Scene rendering requires Blender ' + lock['blender'])
    bpy.ops.wm.open_mainfile(filepath=request['scene'], load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    data = metadata()
    extracted = capture.extract(scene, data, capture.snapshot(scene), 'authored')
    # Record both replayable style and any edits retained only by this .blend.
    artifacts = Path(request['artifacts'])
    (artifacts / 'resolved-style.json').write_text(json.dumps(extracted['style'], indent=2))
    (artifacts / 'capture-report.json').write_text(json.dumps(extracted, indent=2))
    if request['preview']:
        scale = min(1.0, 640 / max(scene.render.resolution_x, scene.render.resolution_y))
        scene.render.resolution_x = max(1, int(scene.render.resolution_x * scale))
        scene.render.resolution_y = max(1, int(scene.render.resolution_y * scale))
        scene.cycles.samples = min(scene.cycles.samples, 16)
    for field, attr in [('width', 'resolution_x'), ('height', 'resolution_y')]:
        if request[field] is not None:
            setattr(scene.render, attr, request[field])
    if request['samples'] is not None:
        scene.cycles.samples = request['samples']
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = request['output']
    scene.cycles.device = request['device']
    _configure_cycles_gpu_for_custom(scene, {'compute_device_type': 'AUTO', 'require_gpu': request['device'] == 'GPU'})
    bpy.ops.render.render(write_still=True)
    output = Path(request['output'])
    manifest = {'job_id': request['job_id'], 'status': 'completed', 'scene_revision': request['revision'],
                'source_sha256': request['source_sha256'], 'blend_sha256': request['blend_sha256'],
                'png_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'width': scene.render.resolution_x, 'height': scene.render.resolution_y,
                'samples': scene.cycles.samples, 'preview': request['preview'],
                'cycles_device': json.loads(scene['bridge_device_info']),
                'output': str(output), 'unsupported_preset_edits': extracted['unsupported']}
    (artifacts / 'manifest.json').write_text(json.dumps(manifest, indent=2))


def main(path):
    from blender_render.developer import isolated_environment
    from blender_render.editor_protocol import write_json
    request = json.loads(Path(path).read_text())
    root, artifacts = Path(request['root']), Path(request['artifacts'])
    started = time.monotonic()
    try:
        with (artifacts / 'render.log').open('w') as log:
            process = subprocess.Popen([request['blender'], '--background', '--factory-startup', '--disable-autoexec',
                '--python-exit-code', '1', '--python', __file__, '--', str(path)],
                cwd=root, env=isolated_environment(root), stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                code = process.wait(timeout=request['timeout'])
            except BaseException:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        if code:
            raise RuntimeError('Scene render failed; inspect ' + str(artifacts / 'render.log'))
        result = json.loads((artifacts / 'manifest.json').read_text())
    except BaseException as exc:
        result = {'status': 'failed', 'error': type(exc).__name__ + ': ' + str(exc)}
    finally:
        shutil.rmtree(root / 'source', ignore_errors=True)
    result['elapsed_seconds'] = time.monotonic() - started
    write_json(root / 'result.json', result)


if __name__ == '__main__':
    if '--' in sys.argv:
        blender_main(json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text()))
    else:
        main(sys.argv[1])
