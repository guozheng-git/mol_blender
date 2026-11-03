"""Real stdio MCP -> desktop editing -> PNG/preset acceptance, with public inputs.

Requires a configured desktop Blender; installs nothing. Creates its own editor
process and stops only that process. Run outside the checkout for wheel acceptance.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time


async def validate(args):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from PIL import Image, ImageChops, ImageStat
    from blender_render.local import render_file
    from blender_render import engine, editor, warm_worker
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    home = root / 'config'; home.mkdir(mode=0o700)
    config = json.loads(args.config.read_text()); config['local_python'] = sys.executable
    (home / 'config.json').write_text(json.dumps(config))
    os.environ['BLENDER_RENDER_HOME'] = str(home)
    evidence = {'passed': False, 'engine_build': engine.engine_identity()['engine_build'], 'module': engine.__file__}
    evidence_path = root / 'evidence.json'
    starting = render_file(args.fixture, output=root / 'start.png',
        options={'preset': 'mint_sky_assembly', 'width': 256, 'height': 192, 'samples': 8, 'save_blend': True})
    print(json.dumps({'stage': 'starting_render', 'status': starting['status']}), flush=True)
    transport = StdioTransport(sys.executable, ['-I', '-m', 'blender_render.cli', 'mcp', '--editor'],
                               env={'BLENDER_RENDER_HOME': str(home)}, cwd=str(root))
    session_id = pid = None
    async with Client(transport, timeout=180) as client:
        async def tool(name, **fields):
            result = await client.call_tool(name, fields)
            if result.structured_content is not None:
                return result.structured_content
            return json.loads(next(c.text for c in result.content if c.type == 'text'))

        async def render_image(revision, filename, **settings):
            submitted = await tool('editor_render_scene', session_id=session_id, revision=revision,
                                   output_path=str(root / filename), **settings)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                result = await client.call_tool('editor_render_status', {'job_id': submitted['job_id'], 'wait_seconds': 5})
                status = json.loads(next(c.text for c in result.content if c.type == 'text'))
                if status['status'] != 'running': break
            assert status['status'] == 'completed', status
            image = next(c for c in result.content if c.type == 'image')
            assert base64.b64decode(image.data) == (root / filename).read_bytes()
            assert not (editor.job_root(submitted['job_id']) / 'source').exists()
            return status

        try:
            opened = await tool('editor_open', scene_path=starting['outputs']['render.blend'])
            session_id, pid = opened['session_id'], opened['pid']
            print(json.dumps({'stage': 'editor_open', 'session_id': session_id}), flush=True)
            state = await tool('editor_pause', session_id=session_id)
            initial_revision = state['revision']
            initial_file = state['file']
            assert not state['unsupported'] and len(state['controls']) > 20
            changes = [
                {'control': 'materials/blue/base_color', 'value': '#E57E40'},
                {'control': 'materials/blue/shadow_color', 'value': '#B45C30'},
                {'control': 'materials/blue/highlight_color', 'value': '#F5BC8A'},
                {'control': 'materials/blue/roughness', 'value': .75},
                {'control': 'world/color', 'value': '#DAE6EF'},
                {'control': 'light/o1/power', 'value': 8.0},
            ]
            changed = await tool('editor_edit', session_id=session_id, revision=initial_revision, changes=changes)
            assert not changed['unsupported']
            failed = await client.call_tool('editor_edit', {'session_id': session_id, 'revision': initial_revision,
                                            'changes': changes}, raise_on_error=False)
            assert failed.is_error
            invalid = await client.call_tool('editor_edit', {'session_id': session_id, 'revision': changed['revision'],
                'changes': [{'control': 'world/color', 'value': '#FFFFFF'}, {'control': 'execute_python', 'value': 'print(1)'}]}, raise_on_error=False)
            assert invalid.is_error
            assert (await tool('editor_inspect', session_id=session_id))['revision'] == changed['revision']
            undone = await tool('editor_undo', session_id=session_id, revision=changed['revision'])
            assert undone['revision'] == initial_revision
            changed = await tool('editor_edit', session_id=session_id, revision=undone['revision'], changes=changes)
            preset = await tool('editor_save_preset', session_id=session_id, revision=changed['revision'],
                                 preset='user/chat-look', report_path=str(root / 'preset-report.json'))
            assert not preset['partial'] and len(preset['changes']) == len(changes)
            state = await tool('editor_inspect', session_id=session_id)
            assert state['revision'] == changed['revision'] and state['file'] == initial_file
            preview = await render_image(state['revision'], 'preview.png', width=256, height=192, samples=8)
            print(json.dumps({'stage': 'preview', 'status': preview['status']}), flush=True)
            saved = await tool('editor_save_scene', session_id=session_id, revision=state['revision'],
                              output_path=str(root / 'edited.blend'))
            assert Path(saved['scene']).is_file()
            overwrite = await client.call_tool('editor_save_scene', {'session_id': session_id, 'revision': saved['revision'],
                         'output_path': str(root / 'edited.blend')}, raise_on_error=False)
            assert overwrite.is_error
            final = await render_image(saved['revision'], 'final.png', preview=False, device='GPU', width=256, height=192, samples=8)
            assert final['cycles_device']['device'] == 'GPU'
            print(json.dumps({'stage': 'final_gpu', 'status': final['status']}), flush=True)
            after = await tool('editor_inspect', session_id=session_id)
            assert after['revision'] == saved['revision']
            # Apply the captured preset through the existing shared renderer, via MCP.
            replay = await tool('render_structure', input_path=str(args.fixture.resolve()),
                                output_path=str(root / 'replayed.png'), preset='user/chat-look',
                                width=256, height=192, samples=8, warm=True)
            assert replay['status'] == 'completed'
            with Image.open(root / 'final.png') as a, Image.open(root / 'replayed.png') as b:
                rms = ImageStat.Stat(ImageChops.difference(a.convert('RGBA'), b.convert('RGBA'))).rms
            assert max(rms) < 2, rms
            from blender_render.scene_edit import inspect_scene
            reopened = inspect_scene(root / 'edited.blend')
            assert not reopened['preserve_input']['unsupported']
            evidence.update(native_stdio_png=True, revision_conflict_rejected=True,
                atomic_invalid_edit_rejected=True, undo_verified=True, unsaved_preset_capture=True,
                overwrite_rejected=True, scene_reopened=True, preset_replay_rms=rms,
                preview=preview, final=final, warm_replay_pid=replay['worker']['pid'],
                session_id=session_id, desktop_pid=pid)
            if args.leave_open:
                evidence['left_open'] = True
                evidence_path.write_text(json.dumps(evidence, indent=2))
                print(json.dumps({'session_id': session_id, 'pid': pid, 'config': str(home)}), flush=True)
            else:
                await tool('editor_disconnect', session_id=session_id)
                # Disconnect must not terminate the desktop or lose its saved edits.
                os.kill(pid, 0)
                evidence['disconnect_leaves_desktop'] = True
            evidence['passed'] = True
        finally:
            warm_worker.stop(force=True)
            if session_id and not args.leave_open:
                try:
                    editor.call(session_id, 'disconnect')
                except (OSError, ValueError, RuntimeError):
                    pass
            if pid and not args.leave_open:
                # This process was launched by this acceptance run only.
                os.kill(pid, signal.SIGTERM)
            evidence_path.write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True, help='Public CIF/PDB or explicitly trusted PSE')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--leave-open', action='store_true', help='Retain the test editor for a follow-up GUI check')
    asyncio.run(validate(parser.parse_args()))


if __name__ == '__main__':
    main()
