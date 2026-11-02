"""Shared local render API for the CLI, PyMOL and stdio MCP."""
from __future__ import annotations
import json
import shutil
import tempfile
from pathlib import Path
from .config import config_root, load_config


def runtime_settings(*, blender='', runtime_paths=None, ccd='', runtime_wheels=''):
    from .core import _detect_host_blender_executable
    config = load_config()
    paths = runtime_paths or config.get('runtime_paths') or [config.get('runtime_site', str(config_root() / 'runtime/site'))]
    paths = [Path(p).expanduser().resolve() for p in paths]
    database = ccd or config.get('ccd')
    if not database:
        database = next((p / 'biotite/structure/info/components.bcif' for p in paths
                         if (p / 'biotite/structure/info/components.bcif').is_file()), None)
    if not database:
        raise ValueError('CCD database not configured. Run setup --ccd /path/to/components.bcif.')
    wheels = runtime_wheels or config.get('runtime_wheels')
    return dict(blender=Path(blender or config.get('blender') or _detect_host_blender_executable()).expanduser().resolve(),
                runtime_paths=paths, ccd=Path(database).expanduser().resolve(),
                runtime_wheels=Path(wheels).expanduser().resolve() if wheels else None)


def render_payload(payload, *, output, options=None, blender='', runtime_paths=None,
                   ccd='', runtime_wheels='', timeout=600, keep_temp=False, catalog_dir=None, warm=False,
                   open_blender=False, gui_blender=''):
    from .core import validate_payload_document
    from .authoring import resolve_authored
    from .developer import preview
    payload = validate_payload_document(payload)
    options = dict(options or {})
    if open_blender:
        from .scene_edit import blender_executable
        editor = blender_executable(gui_blender or load_config().get('gui_blender') or blender, gui=True)
        options['save_blend'] = True
    catalog_dir = Path(catalog_dir).expanduser() if catalog_dir else config_root() / 'presets'
    # Reject invalid presets before allocating any Blender work.
    resolved = resolve_authored(options, payload, catalog_dir)
    animation = resolved['style_doc']['output'].get('animation') or {}
    if animation.get('states'):
        coordinates = payload.get('state_coordinates')
        if not coordinates or max(animation['states']) > len(coordinates['coordinates']):
            raise ValueError('Animation references molecular states missing from the exported payload')
        if animation['states'][animation.get('anchor', 0)] != coordinates['base']:
            raise ValueError('The anchor animation state must be the exported state')
    from .animation import encoder, required_names
    if resolved['style_doc']['output'].get('animation'):
        encoder()  # Fail before allocating a Blender process.
    names = required_names(resolved['style_doc'])
    settings = runtime_settings(blender=blender, runtime_paths=runtime_paths, ccd=ccd, runtime_wheels=runtime_wheels)
    output = Path(output).expanduser().resolve().with_suffix('.png')
    artifacts = output.with_suffix('.render')
    destinations = {'render.png': output, 'render.mp4': output.with_suffix('.mp4'),
                    'render.gif': output.with_suffix('.gif'), 'frames.zip': output.with_suffix('.frames.zip')}
    if artifacts.exists() or any(destinations[name].exists() for name in names if name in destinations):
        raise ValueError(f'Output already exists; choose a new filename: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    runner_options = {}
    if warm:
        from .warm_protocol import seconds
        seconds(timeout, minimum=30)
        from .warm_worker import start
        runner_options['warm_worker'] = start(settings=settings)
    with tempfile.TemporaryDirectory(prefix='blender-local-input-') as temporary:
        bundle = Path(temporary) / 'bundle.json'
        bundle.write_text(json.dumps(payload, allow_nan=False))
        result = preview(checkout=Path(__file__).resolve().parent.parent,
                         bundle=bundle, options=options, catalog_dir=catalog_dir,
                         output=artifacts, timeout=timeout, keep_source=keep_temp, **settings, **runner_options)
    for name in names:
        if not (artifacts / name).is_file():
            raise RuntimeError('Render artifact missing: ' + name)
    for name in names:
        if name in destinations:
            shutil.copyfile(artifacts / name, destinations[name])
    outputs = {'render.png': str(output), 'manifest.json': str(artifacts / 'manifest.json'),
               'style.json': str(artifacts / 'resolved-style.json')}
    for name in names:
        if name in destinations:
            outputs[name] = str(destinations[name])
    if (artifacts / 'render.blend').is_file():
        outputs['render.blend'] = str(artifacts / 'render.blend')
    result = {'status': 'completed', 'outputs': outputs, 'artifacts': str(artifacts),
            'source_sha256': result['sha256'], 'png_sha256': result['png_sha256'],
            **({'worker': result['worker']} if 'worker' in result else {})}
    if open_blender:
        from .scene_edit import open_scene
        try:
            result['editor'] = open_scene(outputs['render.blend'], blender=editor)
        except OSError as exc:
            result['editor'] = {'status': 'launch_failed', 'error': str(exc), 'scene': outputs['render.blend']}
    return result


def render_file(path, *, pymol_python='', selection='(all)', **kwargs):
    from .runtime import export_file
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() == '.json':
        payload = json.loads(path.read_text())
    else:
        payload = export_file(path, python=pymol_python, selection=selection)
    return render_payload(payload, **kwargs)
