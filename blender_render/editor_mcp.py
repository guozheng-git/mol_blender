"""Optional local chat tools. All mutations address a selected desktop session."""
from pathlib import Path
from typing import Any


def register(mcp):
    from . import editor

    @mcp.tool()
    def editor_sessions() -> list[dict]:
        """Start here: find the existing Blender session, select it, and retain its session_id for all edits.

        Never choose an ambiguous session silently. Preserve unsaved edits. An unsupported operation or
        connection failure is not permission to call editor_open or create a replacement scene.
        """
        return editor.sessions()

    @mcp.tool()
    def editor_open(scene_path: str, idle_timeout: float = 3600, new_window: bool = False) -> dict:
        """Launch another Blender window ONLY when the user explicitly requests a new window.

        Requires new_window=True for that explicit request. For ordinary edits use editor_sessions,
        keep the selected session_id, and inspect/edit it. For connection failures use editor_reconnect.
        Never use this tool to recover a connection or work around an unsupported edit.
        Embedded scripts stay disabled. This always launches a desktop; it never reuses one.
        """
        if new_window is not True:
            raise ValueError('Use editor_sessions and reuse the selected session_id. '
                             'editor_open requires an explicit request for another window and new_window=True.')
        return editor.open_editor(scene_path, idle_timeout=idle_timeout)

    @mcp.tool()
    def editor_reconnect(session_id: str) -> dict:
        """Reconnect the selected existing session without launching Blender or loading any file.

        Retain the same session_id. Reports the connection problem and in-place recovery instructions
        if it cannot connect. Never fall back to editor_open; preserve the original unsaved scene.
        """
        return editor.reconnect(session_id)

    @mcp.tool()
    def editor_inspect(session_id: str) -> dict:
        """Inspect the live scene and its editable control IDs, values, limits and revision.

        Reads unsaved manual edits. Reinspect after a revision conflict; never silently overwrite them.
        Unsupported preset edits remain in the scene and are reported.
        """
        return editor.inspect(session_id)

    @mcp.tool()
    def editor_edit(session_id: str, revision: str, changes: list[dict[str, Any]]) -> dict:
        """Apply a bounded atomic edit using controls from editor_inspect.

        changes=[{"control":"materials/blue/roughness","value":0.75}]. Colors use #RRGGBB.
        Use the exact inspected revision. Unknown controls and stale revisions fail without applying edits.
        Existing molecular geometry is preserved. Do not send Python, commands or arbitrary RNA paths.
        """
        return editor.edit(session_id, revision, changes)

    @mcp.tool()
    def editor_undo(session_id: str, revision: str) -> dict:
        """Undo the last chat edit only while the scene still matches its result. Manual edits are preserved."""
        return editor.call(session_id, 'undo', revision=revision)

    @mcp.tool()
    def editor_pause(session_id: str) -> dict:
        """Explicitly pause timeline playback, retaining the current frame, then inspect it."""
        return editor.call(session_id, 'pause')

    @mcp.tool()
    def editor_save_scene(session_id: str, revision: str, output_path: str, overwrite: bool = False) -> dict:
        """Save the complete open scene, including unsaved manual edits, to a local .blend.

        Choose a new path unless the user explicitly requests overwriting an existing scene.
        This is separate from saving a reusable preset. Returns the new revision after Save As.
        """
        return editor.save_scene(session_id, revision, output_path, overwrite=overwrite)

    @mcp.tool()
    def editor_save_preset(session_id: str, revision: str, preset: str, description: str = '',
                           camera_policy: str = 'preserve_input', replace: bool = False,
                           allow_partial: bool = False, report_path: str | None = None) -> dict:
        """Capture the current unsaved look as user/NAME or project/NAME in the local preset catalog.

        Does not change the editor's file. Preserve_input keeps each future input's pose; use authored
        only when the user wants this Blender camera. Unsupported edits refuse export unless the user
        explicitly accepts a partial preset. replace=True explicitly creates the next preset revision.
        Use the ordinary render_structure tool to apply the saved preset to another local structure.
        """
        return editor.save_preset(session_id, revision, preset, description=description,
            camera_policy=camera_policy, replace=replace, allow_partial=allow_partial, report=report_path)

    @mcp.tool()
    def editor_render_scene(session_id: str, revision: str, output_path: str, preview: bool = True,
                            width: int | None = None, height: int | None = None,
                            samples: int | None = None, device: str = 'CPU', timeout: float = 600) -> dict:
        """Render the actual open scene, including unsaved edits, in a separate background process.

        preview=True caps the longest side at 640 and samples at 16 unless overridden. Set preview=False
        for the final still using the scene's settings. Device is CPU or explicit GPU. The editor stays
        available. Capture happens once: keep job_id and call editor_render_status for progress and the
        native PNG. Never resubmit merely to poll. Output and its .editor folder must be new.
        """
        return editor.render_scene(session_id, revision, output_path, preview=preview, width=width,
            height=height, samples=samples, device=device, timeout=timeout)

    @mcp.tool()
    def editor_render_status(job_id: str, wait_seconds: float = 0) -> list:
        """Read one existing render job; completed jobs return the original PNG as native image content.

        Wait at most 20 seconds per call. Pending is not completion. Never start another job to recover
        a preview. Files and provenance are retained at the paths in the result.
        """
        import json
        import time
        from fastmcp.utilities.types import Image
        from mcp.types import TextContent
        from .editor_protocol import number
        number(wait_seconds, 0, 20)
        deadline = time.monotonic() + wait_seconds
        result = editor.job(job_id)
        while result['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.2)
            result = editor.job(job_id)
        content = [TextContent(type='text', text=json.dumps(result))]
        if result['status'] == 'completed':
            # Verify the file belongs to this exact result before showing its pixels.
            import hashlib
            raw = Path(result['output']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != result['png_sha256']:
                raise ValueError('Rendered PNG changed on disk; refusing to show different pixels')
            content.append(Image(data=raw, format='png').to_image_content())
        return content

    @mcp.tool()
    def editor_operation(session_id: str, operation_id: str) -> dict:
        """Recover a scene operation after a timeout without repeating a mutation."""
        return editor.operation(session_id, operation_id)

    @mcp.tool()
    def editor_disconnect(session_id: str) -> dict:
        """Stop chat access and clean its temporary source capture; leave the desktop and edits open."""
        return editor.call(session_id, 'disconnect')
