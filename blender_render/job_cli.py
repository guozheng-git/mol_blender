"""`blender-render job`: one JSON command line for submitting and recovering renders.

Clients that must not import the renderer (co-pymol in PyMOL's Python, Structure
Studio's scripts, the PyMOL bridge) launch this instead. Every subcommand prints one
JSON object. Lab subcommands connect once with the saved profile and close only a
tunnel they opened, unless --keep-connected.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RENDER_FLAGS = ('rough', 'save-blend', 'transparent', 'preserve-view', 'color-follow', 'gif')
RENDER_VALUES = ('width', 'height', 'samples', 'animation', 'duration', 'fps', 'rock_angle')


def add_render_options(parser: argparse.ArgumentParser) -> None:
    """The render options shared by `job submit` and `cluster submit`."""
    parser.add_argument('--options-json', help='Full render options as a JSON file')
    parser.add_argument('--preset')
    parser.add_argument('--style-json')
    parser.add_argument('--overrides-json')
    for key in ('width', 'height', 'samples'):
        parser.add_argument('--' + key, type=int)
    for key in RENDER_FLAGS:
        parser.add_argument('--' + key, action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--animation', choices=('none', 'rock', '360'))
    parser.add_argument('--duration', type=float)
    parser.add_argument('--fps', type=int)
    parser.add_argument('--rock-angle', type=float)


def render_options(args) -> dict:
    from .cluster.jsonio import read_json
    options = read_json(args.options_json) if args.options_json else {}
    if args.preset:
        options['preset'] = args.preset
    if args.style_json:
        options['style_doc'] = read_json(args.style_json)
    if args.overrides_json:
        options['overrides'] = read_json(args.overrides_json)
    for key in (*RENDER_VALUES, 'rough', 'save_blend', 'preserve_view', 'color_follow', 'gif'):
        if getattr(args, key) is not None:
            options[key] = getattr(args, key)
    if args.transparent is not None:
        options['transparent_background'] = args.transparent
    return options


def load_payload(path, *, pymol_python: str = '', selection: str = '(all)') -> dict:
    """A scene bundle (JSON) as is; any other trusted file is exported with PyMOL first."""
    from .cluster.jsonio import read_json
    source = Path(path).expanduser()
    if source.suffix.lower() == '.json':
        return read_json(source)
    from .runtime import export_file
    return export_file(source, python=pymol_python, selection=selection)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog='blender-render job', description=__doc__)
    commands = root.add_subparsers(dest='command', required=True)
    submit = commands.add_parser('submit', help='Render locally, or submit to the lab and return once accepted')
    submit.add_argument('input', help='Scene bundle (.json), or a trusted .pse/.cif/.pdb to export first')
    submit.add_argument('-o', '--output', required=True, help='PNG destination; the lab receipt is written beside it')
    submit.add_argument('--backend', default='local', help="'local' or 'lab' (aliases: cluster, gpu)")
    submit.add_argument('--profile', default='lab', help='Lab connection profile')
    submit.add_argument('--timeout', type=float, default=600, help='Local render deadline in seconds')
    submit.add_argument('--wait', type=float, default=0,
                        help='Lab only: seconds to wait for the job, then fetch it if it completed')
    submit.add_argument('--pymol-python', default='')
    submit.add_argument('--selection', default='(all)')
    add_render_options(submit)
    for name, text in (('status', 'Observe a lab run by its receipt; never submits'),
                       ('fetch', 'Download a completed lab run; with --wait, poll first'),
                       ('cancel', 'Ask the gateway to cancel a lab run')):
        command = commands.add_parser(name, help=text)
        command.add_argument('receipt', help='The .cluster.json receipt written at submission')
        if name == 'fetch':
            command.add_argument('--wait', type=float, default=0, help='Seconds to wait for completion first')
            command.add_argument('--interval', type=float, default=15)
            command.add_argument('-o', '--output', help="Destination; defaults to the receipt's output")
    for command in commands.choices.values():
        command.add_argument('--keep-connected', action='store_true',
                             help='Leave a lab tunnel this command opened running')
    return root


def execute(args) -> dict:
    from . import jobs
    if args.command == 'submit':
        backend = jobs.backend(args.backend)
        payload = load_payload(args.input, pymol_python=args.pymol_python, selection=args.selection)
        options = render_options(args)
        if backend == jobs.LOCAL:
            return jobs.submit(payload, options, args.output, timeout=args.timeout)
        with jobs.lab_connection(args.profile, keep=args.keep_connected) as connection:
            result = jobs.submit(payload, options, args.output, backend_name=backend, profile=args.profile)
            if args.wait > 0:
                result = jobs.wait(result['receipt'], timeout=args.wait)
                if result.get('status') == 'completed':
                    result = jobs.fetch(result['receipt'])
        return {**result, 'connection': connection['connection']}
    record = json.loads(Path(args.receipt).expanduser().read_text())
    with jobs.lab_connection(record['profile'], keep=args.keep_connected) as connection:
        if args.command == 'status':
            result = jobs.observe(args.receipt)
        elif args.command == 'cancel':
            result = jobs.cancel(args.receipt)
        else:
            result = (jobs.wait(args.receipt, timeout=args.wait, interval=args.interval)
                      if args.wait > 0 else jobs.observe(args.receipt))
            if result.get('status') == 'completed':
                result = jobs.fetch(args.receipt, args.output)
    return {**result, 'connection': connection['connection']}


def main(argv=None) -> int:
    result = execute(parser().parse_args(sys.argv[1:] if argv is None else argv))
    print(json.dumps(result, indent=2, default=str))
    return 0
