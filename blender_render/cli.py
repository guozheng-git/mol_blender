"""Local molecular rendering, with explicit optional cluster and MCP adapters."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path
from .presets import DEFAULT_PRESET
from .config import VERSION, load_config, save_config


def parser():
    p = argparse.ArgumentParser(prog='blender-render', description=__doc__)
    p.add_argument('--version', action='version', version=VERSION)
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('dev', help='Preset authoring and isolated source previews (dev --help)')
    commands.add_parser('editor', help='Optional desktop chat bridge (editor --help)')
    commands.add_parser('cluster', help='Optional shared Slurm service and client (cluster --help)')
    worker = commands.add_parser('worker', help='Optional warm local Blender worker')
    actions = worker.add_subparsers(dest='action', required=True)
    start = actions.add_parser('start', help='Start or reuse a worker for the configured local runtime')
    start.add_argument('--idle-timeout', type=float, default=900, help='Idle seconds before shutdown; 0 keeps it running')
    start.add_argument('--blender', default='')
    start.add_argument('--runtime-path', action='append')
    start.add_argument('--runtime-wheels', default='')
    start.add_argument('--ccd', default='')
    actions.add_parser('status', help='Show worker process identity and completed render count')
    stop = actions.add_parser('stop', help='Release Blender and remove its temporary source snapshot')
    stop.add_argument('--force', action='store_true', help='Also interrupt an active render')
    setup = commands.add_parser('setup', help='Configure the local Blender and PyMOL runtimes')
    setup.add_argument('--blender')
    setup.add_argument('--gui-blender', help='Optional desktop Blender executable when the render runtime is headless')
    setup.add_argument('--pymol-python')
    setup.add_argument('--runtime-path', action='append')
    setup.add_argument('--runtime-wheels')
    setup.add_argument('--ccd')
    setup.add_argument('--install-runtime', action='store_true')
    commands.add_parser('doctor', help='Inspect local runtime configuration')
    commands.add_parser('presets', help='List built-in and saved local presets')
    editor = commands.add_parser('open', help='Open a saved local .blend for editing (embedded scripts disabled)')
    editor.add_argument('scene')
    editor.add_argument('--blender', default='', help='Desktop Blender executable')
    editor.add_argument('--edit-bridge', action='store_true', help='Enable optional local chat editing for this desktop')
    mcp = commands.add_parser('mcp', help='Serve local tools over stdio for Claude/Codex')
    mcp.add_argument('--editor', action='store_true', help='Also expose opt-in desktop scene editing tools')
    export = commands.add_parser('export', help='Export a trusted PSE/CIF/PDB to a StructureBundle')
    export.add_argument('input')
    export.add_argument('-o', '--output', required=True)
    export.add_argument('--pymol-python', default='')
    export.add_argument('--selection', default='(all)')
    render = commands.add_parser('render', help='Render a trusted PSE/CIF/PDB or exported JSON locally')
    render.add_argument('input')
    render.add_argument('-o', '--output', default='render.png')
    choice = render.add_mutually_exclusive_group()
    choice.add_argument('--preset', default=DEFAULT_PRESET)
    choice.add_argument('--style-json')
    render.add_argument('--overrides-json')
    render.add_argument('--catalog-dir')
    render.add_argument('--preserve-view', action=argparse.BooleanOptionalAction, default=None)
    render.add_argument('--selection', default='(all)')
    render.add_argument('--pymol-python', default='')
    render.add_argument('--blender', default='')
    render.add_argument('--runtime-path', action='append')
    render.add_argument('--runtime-wheels', default='')
    render.add_argument('--ccd', default='')
    render.add_argument('--keep-temp', action='store_true')
    render.add_argument('--warm', action='store_true', help='Explicitly start/reuse the optional local Blender worker')
    render.add_argument('--open-blender', action='store_true', help='Save the scene and open desktop Blender after rendering')
    render.add_argument('--edit-bridge', action='store_true', help='Save and open desktop Blender with chat editing enabled')
    render.add_argument('--gui-blender', default='', help='Optional desktop Blender executable')
    for name in ('width', 'height', 'samples'):
        render.add_argument('--'+name, type=int)
    for name in ('rough', 'transparent', 'color-follow', 'save-blend'):
        render.add_argument('--'+name, action=argparse.BooleanOptionalAction, default=None)
    render.add_argument('--animation', choices=('none', 'rock', '360'))
    render.add_argument('--duration', type=float)
    render.add_argument('--fps', type=int)
    render.add_argument('--rock-angle', type=float)
    render.add_argument('--gif', action=argparse.BooleanOptionalAction, default=None)
    movie = commands.add_parser('movie-export', help='Encode saved frames as GIF or MP4 without rendering again')
    movie.add_argument('archive')
    movie.add_argument('-o', '--output', required=True)
    render.add_argument('--timeout', type=float, default=600)
    return p


def execute(args):
    from .config import config_root
    if args.command == 'open':
        from .scene.edit import open_scene
        return open_scene(args.scene, blender=args.blender, **({'edit_bridge': True} if args.edit_bridge else {}))
    if args.command == 'worker':
        from . import warm_worker
        if args.action == 'status':
            return warm_worker.status()
        if args.action == 'stop':
            return warm_worker.stop(force=args.force)
        from .local import runtime_settings
        settings = runtime_settings(blender=args.blender, runtime_paths=args.runtime_path,
                                    runtime_wheels=args.runtime_wheels, ccd=args.ccd)
        return warm_worker.start(idle_timeout=args.idle_timeout, settings=settings)
    if args.command == 'setup':
        config = load_config()
        config['local_python'] = sys.executable
        for name in ('blender', 'gui_blender', 'pymol_python', 'runtime_wheels', 'ccd'):
            value = getattr(args, name)
            if value:
                path = Path(value).expanduser()
                # Resolving a virtualenv interpreter's symlink would drop its site-packages.
                config[name] = str(path.absolute() if name == 'pymol_python' else path.resolve())
        if args.runtime_path:
            config['runtime_paths'] = [str(Path(p).expanduser().resolve()) for p in args.runtime_path]
        if args.install_runtime:
            from .runtime_install import install
            installed = install(config.get('blender', ''))
            config.update(installed)
            config['runtime_paths'] = [installed['runtime_site']]
        save_config(config)
        return {'status': 'configured', 'config': str(config_root() / 'config.json'),
                'runtime_verified': False, 'pymol_command': 'run ' + str(Path(__file__).with_name('pymol_bridge_plugin.py'))}
    if args.command == 'doctor':
        from .runtime import diagnose
        return {**diagnose(), 'config': load_config()}
    if args.command == 'presets':
        from .authoring import list_presets
        return list_presets(config_root() / 'presets')
    if args.command == 'mcp':
        from .mcp_server import create_stdio
        create_stdio(editor_enabled=args.editor).run(transport='stdio', show_banner=False)
        return
    if args.command == 'export':
        from .runtime import export_file
        output = Path(args.output).expanduser().resolve()
        if output.exists():
            raise ValueError('Export already exists: ' + str(output))
        payload = export_file(args.input, python=args.pymol_python, selection=args.selection)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, allow_nan=False))
        return {'bundle': str(output)}
    if args.command == 'movie-export':
        from .animation import export
        if Path(args.output).expanduser().exists():
            raise ValueError('Output already exists: ' + args.output)
        return {'movie': export(Path(args.archive).expanduser(), Path(args.output).expanduser())}
    if args.command == 'render':
        from .local import render_file
        options = {'style_doc': json.loads(Path(args.style_json).expanduser().read_text())} if args.style_json else {'preset': args.preset}
        if args.overrides_json:
            options['overrides'] = json.loads(Path(args.overrides_json).expanduser().read_text())
        for key in ('width', 'height', 'samples', 'rough', 'color_follow', 'save_blend', 'preserve_view', 'animation', 'duration', 'fps', 'rock_angle', 'gif'):
            if getattr(args, key) is not None:
                options[key] = getattr(args, key)
        if args.transparent is not None:
            options['transparent_background'] = args.transparent
        return render_file(args.input, output=args.output, options=options, selection=args.selection,
            pymol_python=args.pymol_python, blender=args.blender, runtime_paths=args.runtime_path,
            runtime_wheels=args.runtime_wheels, ccd=args.ccd, timeout=args.timeout,
            keep_temp=args.keep_temp, catalog_dir=args.catalog_dir, warm=args.warm,
            open_blender=args.open_blender, gui_blender=args.gui_blender, edit_bridge=args.edit_bridge)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'dev':
        from .developer import main as developer_main
        return developer_main(argv[1:])
    try:
        if argv and argv[0] == 'editor':
            from .editor_cli import main as editor_main
            return editor_main(argv[1:])
        if argv and argv[0] == 'cluster':
            from .cluster.cli import main as cluster_main
            return cluster_main(argv[1:])
        result = execute(parser().parse_args(argv))
        if result is not None:
            print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print('blender-render: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
