"""Local PyMOL-to-Blender CLI. No remote profiles or background services."""
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
    setup = commands.add_parser('setup', help='Configure the local Blender and PyMOL runtimes')
    setup.add_argument('--blender')
    setup.add_argument('--pymol-python')
    setup.add_argument('--runtime-path', action='append')
    setup.add_argument('--runtime-wheels')
    setup.add_argument('--ccd')
    setup.add_argument('--install-runtime', action='store_true')
    commands.add_parser('doctor', help='Inspect local runtime configuration')
    commands.add_parser('presets', help='List built-in and saved local presets')
    commands.add_parser('mcp', help='Serve local tools over stdio for Claude/Codex')
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
    if args.command == 'setup':
        config = load_config()
        config['local_python'] = sys.executable
        for name in ('blender', 'pymol_python', 'runtime_wheels', 'ccd'):
            value = getattr(args, name)
            if value:
                config[name] = str(Path(value).expanduser().resolve())
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
        create_stdio().run(transport='stdio', show_banner=False)
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
            keep_temp=args.keep_temp, catalog_dir=args.catalog_dir)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == 'dev':
        from .developer import main as developer_main
        return developer_main(argv[1:])
    try:
        result = execute(parser().parse_args(argv))
        if result is not None:
            print(json.dumps(result, indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print('blender-render: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
