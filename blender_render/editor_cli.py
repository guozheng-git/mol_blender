"""Local desktop editing commands; use the local MCP adapter for chat."""
import argparse
import json
from pathlib import Path


def parser():
    p = argparse.ArgumentParser(prog='blender-render editor', description=__doc__)
    commands = p.add_subparsers(dest='action', required=True)
    commands.add_parser('sessions', help='List explicitly enabled local editor sessions')
    opening = commands.add_parser('open', help='Open a saved scene with the chat bridge enabled')
    opening.add_argument('scene'); opening.add_argument('--blender', default='')
    opening.add_argument('--idle-timeout', type=float, default=3600)
    attach = commands.add_parser('attach-script', help='Prepare a trusted script for an already-open Blender')
    attach.add_argument('--output', required=True); attach.add_argument('--idle-timeout', type=float, default=3600)
    attach.add_argument('--reconnect', help='Replace the bridge in this selected session without reopening Blender')
    for action in ('inspect', 'reconnect', 'pause', 'disconnect', 'forget'):
        command = commands.add_parser(action)
        command.add_argument('session')
    for action in ('edit', 'undo', 'save', 'save-preset', 'render'):
        command = commands.add_parser(action)
        command.add_argument('session'); command.add_argument('--revision', required=True)
        if action == 'edit': command.add_argument('--changes-json', required=True)
        elif action == 'save':
            command.add_argument('path'); command.add_argument('--overwrite', action='store_true')
        elif action == 'save-preset':
            command.add_argument('preset'); command.add_argument('--description', default='')
            command.add_argument('--camera-policy', choices=('preserve_input', 'authored'), default='preserve_input')
            command.add_argument('--replace', action='store_true'); command.add_argument('--allow-partial', action='store_true')
            command.add_argument('--catalog-dir'); command.add_argument('--report')
        elif action == 'render':
            command.add_argument('-o', '--output', required=True)
            command.add_argument('--final', action='store_true', help='Use saved resolution and samples unless explicitly overridden')
            command.add_argument('--device', choices=('CPU', 'GPU'), default='CPU')
            for field in ('width', 'height', 'samples'): command.add_argument('--' + field, type=int)
            command.add_argument('--timeout', type=float, default=600)
    job = commands.add_parser('job', help='Recover an existing render without resubmitting it')
    job.add_argument('job_id')
    operation = commands.add_parser('operation', help='Recover a timed-out scene operation without repeating it')
    operation.add_argument('session'); operation.add_argument('operation_id')
    return p


def execute(args):
    from . import editor
    action = args.action
    if action == 'sessions': return editor.sessions()
    if action == 'open': return editor.open_editor(args.scene, blender=args.blender, idle_timeout=args.idle_timeout)
    if action == 'attach-script': return editor.attach_script(args.output, idle_timeout=args.idle_timeout, reconnect_session=args.reconnect)
    if action == 'inspect': return editor.inspect(args.session)
    if action == 'reconnect': return editor.reconnect(args.session)
    if action == 'forget': return editor.forget(args.session)
    if action in ('pause', 'disconnect'): return editor.call(args.session, action)
    if action == 'edit': return editor.edit(args.session, args.revision, json.loads(Path(args.changes_json).expanduser().read_text()))
    if action == 'undo': return editor.call(args.session, 'undo', revision=args.revision)
    if action == 'save': return editor.save_scene(args.session, args.revision, args.path, overwrite=args.overwrite)
    if action == 'save-preset':
        return editor.save_preset(args.session, args.revision, args.preset, description=args.description,
            camera_policy=args.camera_policy, replace=args.replace, allow_partial=args.allow_partial,
            catalog_dir=args.catalog_dir, report=args.report)
    if action == 'render':
        return editor.render_scene(args.session, args.revision, args.output, preview=not args.final,
            width=args.width, height=args.height, samples=args.samples, device=args.device, timeout=args.timeout)
    if action == 'job': return editor.job(args.job_id)
    if action == 'operation': return editor.operation(args.session, args.operation_id)


def main(argv=None):
    print(json.dumps(execute(parser().parse_args(argv)), indent=2))
    return 0
