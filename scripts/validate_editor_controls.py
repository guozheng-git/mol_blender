"""Real MCP acceptance against an explicitly selected, already-open desktop.

Never launches/closes/replaces a desktop or overwrites its file. Temporarily
changes and undoes loop/view controls, captures a background preview, then
leaves a fast colored view. Run while no one else is editing that scene.
"""
import argparse
import asyncio
import base64
import json
from pathlib import Path
import sys


async def validate(args):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    from blender_render import editor
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    selected = next(s for s in editor.sessions() if s['id'] == args.session)
    original_ids = {s['id'] for s in editor.sessions()}
    evidence = {'passed': False, 'session_id': args.session, 'desktop_pid': selected['pid']}
    async with Client(StdioTransport(sys.executable,
            ['-I', '-m', 'blender_render.cli', 'mcp', '--editor']), timeout=120) as client:
        async def tool(name, **fields):
            result = await client.call_tool(name, fields)
            return result.structured_content if result.structured_content is not None else json.loads(
                next(c.text for c in result.content if c.type == 'text'))
        async def rejected(name, **fields):
            result = await client.call_tool(name, fields, raise_on_error=False)
            assert result.is_error, name
        try:
            discovered = await tool('editor_sessions')
            if isinstance(discovered, dict): discovered = discovered['result']
            assert any(s['id'] == args.session for s in discovered)
            connected = await tool('editor_reconnect', session_id=args.session)
            assert connected['status'] == 'connected' and not connected['new_window_opened']
            state = await tool('editor_playback', session_id=args.session, state='pause')
            initial_file, initial = state['file'], state['revision']
            assert len(state['animation_loops']) == 1, 'Choose a scene with one supported loop for this acceptance'
            loop = state['animation_loops'][0]
            values = lambda s: {c['id']: c['value'] for c in s['controls']}
            before = values(state)
            await rejected('editor_open', scene_path=initial_file)
            await rejected('editor_animation_speed', session_id=args.session, revision=initial, multiplier=9)
            assert (await tool('editor_inspect', session_id=args.session))['revision'] == initial
            faster = await tool('editor_animation_speed', session_id=args.session, revision=initial,
                                multiplier=2, control=loop)
            assert values(faster)[loop] == before[loop] / 2 and faster['dirty']
            assert values(faster)['materials/blue/base_color'] == before['materials/blue/base_color']
            await rejected('editor_viewport', session_id=args.session, revision=initial, mode='wireframe')
            state = await tool('editor_undo', session_id=args.session, revision=faster['revision'])
            assert state['revision'] == initial
            wire = await tool('editor_viewport', session_id=args.session, revision=state['revision'], mode='wireframe')
            assert values(wire)['viewport/shading'] == 'WIREFRAME'
            state = await tool('editor_undo', session_id=args.session, revision=wire['revision'])
            assert state['revision'] == initial
            color = await tool('editor_viewport', session_id=args.session, revision=state['revision'],
                               mode='solid_color', overlays=False)
            assert values(color)['viewport/shading'] == 'SOLID'
            assert values(color)['viewport/color_source'] == 'MATERIAL'
            # Display-only changes must not change shader controls or render settings.
            after = values(color)
            assert all(after[k] == v for k, v in before.items() if not k.startswith('viewport/'))
            playing = await tool('editor_playback', session_id=args.session, state='play')
            assert playing['playing']
            again = await tool('editor_playback', session_id=args.session, state='play')
            assert again['playing']
            await asyncio.sleep(.35)
            paused = await tool('editor_playback', session_id=args.session, state='pause')
            assert not paused['playing']
            assert paused['frame'] != playing['frame'], (playing['frame'], paused['frame'])
            submitted = await tool('editor_render_scene', session_id=args.session, revision=paused['revision'],
                                   output_path=str(root / 'preview.png'), width=384, height=288, samples=8)
            (root / 'job.json').write_text(json.dumps(submitted, indent=2))
            for _ in range(30):
                result = await client.call_tool('editor_render_status', {'job_id': submitted['job_id'], 'wait_seconds': 5})
                status = json.loads(next(c.text for c in result.content if c.type == 'text'))
                if status['status'] != 'running': break
            assert status['status'] == 'completed', status
            image = next(c for c in result.content if c.type == 'image')
            assert base64.b64decode(image.data) == (root / 'preview.png').read_bytes()
            end = await tool('editor_inspect', session_id=args.session)
            assert end['file'] == initial_file and end['dirty']
            assert end['revision'] == paused['revision']
            assert {s['id'] for s in editor.sessions()} == original_ids
            assert next(s for s in editor.sessions() if s['id'] == args.session)['pid'] == selected['pid']
            evidence.update(passed=True, selected_session_reused=True, desktop_not_replaced=True,
                explicit_open_guard=True, invalid_speed_rejected=True, stale_revision_rejected=True,
                speed_and_viewport_undo=True, shader_and_render_settings_preserved=True,
                playback_idempotent=True, playback_frames=[playing['frame'], paused['frame']],
                unsaved_scene_preserved=True, preview=status)
            if args.leave_playing:
                assert (await tool('editor_playback', session_id=args.session, state='play'))['playing']
        finally:
            (root / 'evidence.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True, help='Existing, explicitly selected session ID')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--leave-playing', action='store_true')
    asyncio.run(validate(parser.parse_args()))
