# Edit an open Blender scene through chat

Ask your local chat to adjust a material, soften a light, change the background,
show a preview, and save the result when you like it. You can keep editing in the
same Blender window between requests. The bridge reads the current scene,
including unsaved changes.

Chat first calls `editor_sessions`, selects the existing window, and retains its
`session_id` for every subsequent edit, preview, save and playback action. It must
not create a replacement scene for an unsupported edit or a connection failure.
`editor_open` always launches another desktop and requires `new_window=True`; use
it only when you explicitly request another window. Background previews still
run separately without opening desktop windows.

This is an optional **local** connection for macOS or Linux with a desktop Blender.
It uses the configured, pinned Blender runtime and installs nothing. Ordinary
local rendering, the warm render worker, and Slurm remain available independently.

## Enable it for a scene

After updating, restart PyMOL and load the updated `pymol_bridge_plugin.py`
using the [PyMOL bridge setup](../README.md#pymol-bridge). Then run:

```pml
blender_render selection=(all), preset=mint_sky_assembly, edit_bridge=1, width=640, height=480, samples=32, output_name=edit.png
```

Or from the terminal:

```sh
blender-render render structure.cif --preset mint_sky_assembly --edit-bridge \
  --width 640 --height 480 --samples 32 -o renders/start.png
```

`edit_bridge=1` / `--edit-bridge` saves a Blender scene, opens its desktop window,
and enables chat access for that session. The result includes a `session_id`.
To open an already-saved Blender Render scene with this connection:

```sh
blender-render open renders/start.render/render.blend --edit-bridge
blender-render editor sessions
```

`--open-blender` alone continues to open an ordinary editor without chat access.
If the configured render executable is the headless Linux runtime, first configure
an existing desktop executable with `blender-render setup --gui-blender /path/to/blender`.

## Connect your local chat

Enable the optional editor tools by adding `--editor` to the local MCP server's
arguments. For example, register a dedicated connection:

```sh
codex mcp add blender-editor -- /absolute/path/to/environment/bin/blender-render mcp --editor
claude mcp add --transport stdio blender-editor -- /absolute/path/to/environment/bin/blender-render mcp --editor
```

Use the same user account, local configuration and package revision as the editor
bridge. Reconnect the MCP server or start a new chat so its tools are refreshed.
The ordinary four render tools remain available; the editor tools are added only
with `mcp --editor`. A hosted Blender Render connection does not automatically gain
access to a desktop. This feature requires the local MCP connection above.

Example conversation:

> Connect to my Blender Render editor session. Make the protein matte orange,
> keep the ligand color, and show me a preview.

> Soften the key light a little and show another preview.

> I like that. Save the complete scene as `renders/orange.blend`, save the look as
> `user/matte-orange`, and render a 1600 × 1200 final image at 128 samples using
> the local GPU.

> Use `user/matte-orange` to render this other structure too.

Scene saving and preset saving are separate actions. A `.blend` keeps the entire
composition. A preset keeps the supported reusable appearance and can be used by
the existing `render_structure` chat tool, CLI, PyMOL, or compatible Slurm service.
Saving a preset captures the current unsaved scene without changing the editor's
filename. Built-in presets cannot be overwritten; explicitly replacing a user
preset creates its next revision.

## What chat can change

The chat first calls `editor_inspect`, which returns the available control IDs,
current values, types and bounds. It can change existing material controls,
background and illumination colors, light power/size/location/rotation, camera
pose and projection, depth of field, and supported render settings. Material
controls come from the existing scene's preset bindings; arbitrary shader code,
mesh edits and new objects are not exposed.

Each edit uses the inspected scene revision. If you changed the scene manually
in the meantime, the bridge refuses the stale request and the chat must inspect
again. A group of changes is validated before applying it and rolled back if a
control fails. Successful edits enter Blender's undo history and mark the file as
unsaved, so closing the window offers its normal save warning. `editor_undo` can
undo the last chat edit while its result is still
current. It refuses to undo over intervening manual edits. Pause timeline playback
before editing or capturing; `editor_pause` is available for an explicit pause.
Leave Edit Mode and finish any desktop render before saving or changing controls.

Colors follow the renderer's numeric `#RRGGBB` convention. Light location, size,
camera location, orthographic scale and focus distance use **Blender scene units**;
rotation uses degrees and lens length uses millimeters. Inspect before assigning
values. The preset exporter normalizes the supported camera/light geometry for
reuse on another structure.

Preset saving defaults to `camera_policy=preserve_input`, preserving the pose of
each future input. Choose `authored` explicitly to reuse the edited Blender camera.
Unsupported manual edits are reported and prevent a complete preset export. Only
use `allow_partial=True` when you deliberately accept the listed omissions. The
complete scene can still be saved. See [Blender editing](blender-editing.md) for
the detailed mappings and limitations.

## Previews and final renders

`editor_render_scene` captures the selected scene once and returns a `job_id`.
It renders that snapshot in a separate background process, so you can continue
working in the editor. Later edits do not change an already-submitted render.
This renders the actual saved scene, including manual edits that a preset cannot
represent. The current implementation produces a Cycles still at the selected
frame. Use the existing molecular animation tools for rock/360 movies.

- A preview defaults to at most 640 pixels on its longest side and 16 samples.
- A final still (`preview=False`) uses the scene's resolution and sample count.
- Explicit width, height and samples override those settings for that render only.
- CPU is the default for editor previews/finals; request `device=GPU` for the local
  GPU. An explicit GPU request fails if no supported device is available.
- `editor_render_status` reads the same job and returns the original PNG as native
  image content when complete. Poll that job instead of submitting another render.

Outputs use a new PNG filename and a neighboring `.editor/` folder containing the
captured `.blend`, resolved style, source identity, capture report, manifest and
logs. Nothing is overwritten by rendering. Saving a scene also refuses an existing
filename unless overwrite was explicitly requested. A timed-out scene operation
has an operation ID for `editor_operation`; recover its outcome before retrying.

The existing `--warm` / `warm=1` worker can generate the starting molecular scene.
Editor previews use independent snapshot processes and do not alter that worker.
Slurm continues to allocate one independent Blender process per submitted render.
To reuse a captured look on Slurm, save it as a preset and submit the original
molecular structure through the usual cluster path. The shared API does not accept
`.blend` uploads or desktop commands. Fetch a Slurm scene and open it locally when
you want to edit it; do not combine `cluster=...` with `edit_bridge=1`.

## Attach to a window that is already open

A window needs an explicit opt-in before chat can access it. To attach without
reopening and losing unsaved work, prepare a trusted external startup script:

```sh
blender-render editor attach-script --output /absolute/path/to/enable-blender-chat.py
```

In that Blender window, switch one area to the **Python Console** and run this
one-time command using the generated script's actual path:

```python
exec(compile(open('/absolute/path/to/enable-blender-chat.py').read(), 'enable-blender-chat.py', 'exec'))
```

This runs the trusted bridge you generated locally. It is not code from a structure
or downloaded scene. No script needs to be embedded in the `.blend`, no add-on is
installed, and automatic script execution should stay disabled. The current scene
must have Blender Render editing metadata. The script captures the package revision;
use the in-place reconnect procedure below after updating that package.

For a dropped connection, call `editor_reconnect` with the selected `session_id`
(CLI: `blender-render editor reconnect SESSION_ID`). This only reconnects to the
existing window and reports any failure; it never starts Blender. After a bridge
timeout or a package update, replace the bridge in place:

```sh
blender-render editor attach-script --reconnect SESSION_ID --output /absolute/path/to/reconnect-blender-chat.py
```

Run that generated trusted external script in the **same** window's Python
Console. It checks the selected session, retains its ID, replaces only the bridge
code, and preserves the entire unsaved scene. Preparing the script does not
disconnect the active bridge. Do not open another window to recover a connection.

## Disconnect and command-line access

Ask chat to disconnect, or run:

```sh
blender-render editor disconnect SESSION_ID
```

This removes chat access and its temporary source capture, leaving the desktop and
unsaved work open. Closing Blender, opening another file, or one hour without
bridge activity also stops the connection. The idle interval can be set from 30
seconds to 24 hours with `editor open --idle-timeout SECONDS` or `attach-script`.
There are at most eight queued operations and 512 retained operations per session;
reattach the bridge in the same window after reaching the operation limit. Preview job records
and requested output artifacts are retained for recovery.

For a crash or an unused prepared attachment, `editor forget SESSION_ID` removes
its abandoned private capture; it refuses to remove a running desktop's session.
The bridge uses a per-user Unix socket in a private directory, with bounded JSON
and a session token. It opens no network port and accepts no Python/RNA execution
requests. All Blender changes run on Blender's main thread. Source identity checks
require reopening the bridge after package changes. Trust requirements for local
PSE and `.blend` inputs still apply; this is not a sandbox for untrusted scenes.

All chat actions also have CLI equivalents. For example, inspect the returned
revision and control IDs, then put a list of changes in `changes.json`:

```json
[{"control": "materials/blue/roughness", "value": 0.75}]
```

```sh
blender-render editor inspect SESSION_ID
blender-render editor edit SESSION_ID --revision REVISION --changes-json changes.json
# Use the new returned revision after each change or Save As.
blender-render editor save SESSION_ID renders/my-look.blend --revision REVISION
blender-render editor save-preset SESSION_ID user/my-look --revision REVISION
blender-render editor render SESSION_ID --revision REVISION -o renders/preview.png
blender-render editor job JOB_ID
blender-render editor render SESSION_ID --revision REVISION --final --device GPU \
  --width 1600 --height 1200 --samples 128 -o renders/final.png
```
