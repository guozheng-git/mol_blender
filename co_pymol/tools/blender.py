"""Optional Blender Render tools; existing PyMOL rendering stays unchanged."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from co_pymol.render.client import BlenderBridge, render_name
from co_pymol.defaults import IMAGE
from co_pymol.tools.errors import error_wrapper
from co_pymol.scene.capture import capture_pse, capture_scene
from co_pymol.scene.session import pymol_session


def register_blender_tools(mcp: FastMCP) -> None:
    bridge = BlenderBridge()

    @mcp.tool()
    @error_wrapper
    def blender_configure(config_path: str) -> str:
        """Load a trusted local Blender settings JSON for this session.

        Optional: defaults render locally with this checkout. Fields include
        backend ("local"/"cluster"), cluster_profile, output_directory and
        project_directory. Does not connect, allocate, install, or render.
        """
        return json.dumps(bridge.configure(config_path))

    @mcp.tool(structured_output=False)
    @error_wrapper
    def blender_presets(images: bool = False) -> list[str | Image]:
        """List preset IDs and short descriptions. Text only by default.

        Local-only and fast; does not render the current scene or allocate a GPU.
        Call once when the user asks for options, show descriptions, and wait for
        their choice. An options question is not an instruction to pick a default
        and render immediately. Example structures and colours can differ from
        the user's scene. Set images=True only if they explicitly want pictures.
        """
        catalog = bridge.presets(images=images)
        content: list[str | Image] = [json.dumps(catalog)]
        if path := catalog.get("preview_path"):
            content.append(Image(data=Path(path).read_bytes(), format="png"))
        return content

    @mcp.tool()
    @error_wrapper
    def blender_render(
        preset: str = IMAGE["preset"],
        selection: str = "all",
        state: int = -1,
        width: int = IMAGE["preview"]["width"],
        height: int = IMAGE["preview"]["height"],
        samples: int = IMAGE["preview"]["samples"],
        keep_colors: bool = True,
        save_blend: bool = False,
        name: str | None = None,
        protein: str | None = None,
        view: str | None = None,
        backend: str | None = None,
    ) -> str:
        """Capture this PyMOL view and submit a background Blender render.

        backend: "local" renders on this computer; "lab" (or "cluster") renders on
        the lab GPU through the saved `blender-render cluster connect` profile.
        Omit it to use the configured default.

        Only call when the user asks for a Blender picture. Enabled molecular
        objects are exported. If they ask to see available presets, show
        blender_presets and wait for their selection before calling this tool.
        Arbitrary labels/CGO/measurements aren't captured.
        height=0 follows the PyMOL viewport aspect ratio. keep_colors=True keeps
        scientific colours; False uses the preset palette. Returns a request_id
        immediately after capture. Submission completes this chat request:
        acknowledge the receipt and END THE TURN. Do not call status/result,
        sleep, or poll afterward. The background helper handles rendering and
        downloads. The user can continue giving PyMOL commands in the next turn.
        Check status/results only when subsequently requested. Never resubmit
        just because a job is queued or disconnected.
        Always saves the full PyMOL session locally as scene.pse alongside the
        render, including hidden objects and all states, before submitting.
        name is optional: omit it for an automatic adjective-animal folder name.
        Custom names become lowercase with spaces replaced by hyphens. Existing
        names receive a numeric suffix; saved runs are never overwritten.
        With project_directory configured, saves to figures/<protein>/<view>/
        renders/<preset> with a shared scene.pse at the view root. protein
        defaults to the sole exported object; specify it for a complex with
        multiple objects. view defaults to overview (or name), with numbered
        versions on repetition. Choose a descriptive view from the user's task.
        """
        name = render_name(name)
        cfg = bridge.config
        if cfg.project_directory:
            preset = bridge.select_presets([preset], False)[0]
        with pymol_session() as cmd:
            pse = capture_pse(cmd)
            payload = capture_scene(cmd, selection, state)
        receipt = bridge.presentation(
            bridge.submit(
                payload,
                preset=preset,
                width=width,
                height=height,
                samples=samples,
                keep_colors=keep_colors,
                save_blend=save_blend,
                pse=pse,
                name=name,
                protein=protein,
                view=view,
                backend=backend,
            )
        )
        receipt["next_action"] = "return_to_user"
        return json.dumps(receipt)

    @mcp.tool()
    @error_wrapper
    def blender_render_batch(
        presets: list[str] | None = None,
        all_presets: bool = False,
        selection: str = "all",
        state: int = -1,
        width: int = IMAGE["preview"]["width"],
        height: int = IMAGE["preview"]["height"],
        samples: int = IMAGE["preview"]["samples"],
        keep_colors: bool = True,
        save_blend: bool = False,
        name: str | None = None,
        protein: str | None = None,
        view: str | None = None,
        backend: str | None = None,
    ) -> str:
        """Submit multiple presets from ONE frozen PyMOL scene and return promptly.

        backend: "local" or "lab"/"cluster", as for blender_render.

        Use when the user requests several presets or all presets. Set
        all_presets=True only for an explicit all-presets request; otherwise
        supply preset IDs. Capture happens once; submission, rendering and
        downloads continue in a separate background process. No network work
        holds the PyMOL lock. All runs share one saved PSE and scene bundle.

        After this returns, acknowledge the batch name/count and END THE TURN.
        Do not poll, wait, call status/result, or issue individual render calls.
        This returns a local batch receipt, not confirmation of GPU allocation.
        The user's next chat request can operate PyMOL while renders continue.
        Later, use blender_status with the batch name, or blender_result with
        an individual run name to display an image. Never resubmit a batch to
        recover its existing jobs.
        With project_directory configured, protein and view group the shared
        snapshot under figures/<protein>/<view>/, with variants in renders/.
        protein defaults to the sole exported object; specify it for complexes.
        view defaults to overview (or name); repeated views get numbered folders.
        """
        name = render_name(name)
        selected = bridge.select_presets(presets, all_presets)
        cfg = bridge.config
        with pymol_session() as cmd:
            pse = capture_pse(cmd)
            payload = capture_scene(cmd, selection, state)
        return json.dumps(
            bridge.submit_batch(
                payload,
                presets=selected,
                pse=pse,
                name=name,
                protein=protein,
                view=view,
                width=width,
                height=height,
                samples=samples,
                keep_colors=keep_colors,
                save_blend=save_blend,
                backend=backend,
            )
        )

    @mcp.tool()
    @error_wrapper
    def blender_renders(limit: int = 20) -> str:
        """List recent saved renders, newest first, even from previous chats.

        Reads the configured local output directory only; statuses are cached.
        Does not connect or allocate compute. Use blender_result with a returned
        name or request_id to retrieve or reconnect to an existing render.
        """
        return json.dumps(bridge.recent(limit))

    @mcp.tool()
    @error_wrapper
    def blender_status(request_id: str) -> str:
        """Read cached stage, elapsed time and errors by render name or request ID.

        Returns immediately; no remote work. Do not poll repeatedly within one
        chat turn unless the user explicitly asks to wait for completion.
        """
        return json.dumps(bridge.presentation(bridge.status(request_id)))

    @mcp.tool(structured_output=False)
    @error_wrapper
    def blender_result(request_id: str) -> list[str | Image] | str:
        """Display the persistent PNG plus saved paths once the job is complete.

        Also resumes retrieval for an existing job after interruption; never
        submits a replacement render. If still pending, returns status only.
        request_id accepts either the saved request ID or the render's name.
        For a batch, returns its summary and child run names; fetch individual
        run names to display their images. Pending work returns promptly.
        """
        status, png = bridge.result(request_id)
        status = bridge.presentation(status)
        if png is None:
            return json.dumps(status)
        return [json.dumps(status), Image(data=png, format="png")]

    @mcp.tool()
    @error_wrapper
    def blender_cancel(request_id: str) -> str:
        """Request cancellation of a local or lab render; preserves saved files.

        Cancellation is asynchronous. Check blender_status for confirmation.
        request_id accepts either the saved request ID or the render's name.
        """
        return json.dumps(bridge.presentation(bridge.cancel(request_id)))
