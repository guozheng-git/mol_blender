# Make a preset by editing in Blender

Start with a preset you like, open the resulting scene in Blender, and adjust its
look with Blender's normal controls. Save the edited `.blend`, then give that look
a name you can use on another structure.

You can also make these changes, request previews, save scenes/presets and trigger
renders through the optional [local chat bridge](chat-editing.md). Start with
`edit_bridge=1` instead of `open_blender=1` and enable `mcp --editor` in the local
chat connection. Ordinary manual editing needs no chat bridge.

## From PyMOL

Reload the updated bridge in a fresh PyMOL session, then run:

```pml
blender_render selection=(all), preset=mint_sky_assembly, open_blender=1, width=640, height=480, samples=32, output_dir=~/Desktop/blender-edit, output_name=start.png
```

This renders a preview, saves `start.render/render.blend`, and opens it in a
separate desktop Blender process. `open_blender=1` implies `save_blend=1`. The
scene contains the molecular geometry, materials, lights, camera, and preset
metadata. You do not need to install a Blender add-on to edit it.

In Blender, use **File → Save As** to save your edited scene, for example as
`~/Desktop/blender-edit/my-look.blend`. Then return to PyMOL:

```pml
blender_preset_from_blend ~/Desktop/blender-edit/my-look.blend, user/my-look
blender_render selection=(all), preset=user/my-look, output_dir=~/Desktop/blender-edit, output_name=with-my-look.png
```

The new preset is available through `mol-blender presets` and through the
existing local CLI, PyMOL and stdio MCP renderer. It is saved under the local
configuration directory, not in the repository or Blender's installation.
The importer reads the saved file, so remember to save again after further edits.

## From a terminal

```sh
mol-blender render structure.cif --preset mint_sky_assembly --open-blender \
  --width 640 --height 480 --samples 32 -o renders/start.png

# After editing and saving as renders/my-look.blend in Blender:
mol-blender dev from-blend renders/my-look.blend --preset user/my-look \
  --description 'My Blender material and lighting' --report renders/my-look-report.json

mol-blender render another-structure.cif --preset user/my-look -o renders/reused.png
```

To open an existing scene again, use `mol-blender open renders/my-look.blend`.
`--open-blender` is optional; ordinary rendering continues to work without a
desktop. It can be combined with `--warm` / `warm=1`: rendering uses the private
worker, and editing opens a separate desktop process. Closing the editor does
not stop the worker, and the worker does not incorporate unsaved editor changes.

## Which Blender controls can become a preset?

| What you edit | Supported controls |
| --- | --- |
| Standard materials | Unlinked Principled BSDF roughness, specular, transmission, coat, alpha and emission inputs |
| Clay colors and shading | **Clay shadow** Color1 (base) and Color2 (shadow), **Clay highlight** Color2; named AO and rim controls |
| Background | **Background color**, **Ambient illumination**, and the two **Background gradient** color stops in the World shader |
| Lights already in the scene | Power, color, size, position and rotation |
| Render settings | Resolution and percentage, Cycles samples, denoising, adaptive sampling, seed, transparency, view transform, look, exposure and gamma |
| Paint | Existing paint texture scale/detail/distortion, relief strengths/distances, loop relief, edge-normal radius, roughness range, specular, coat, pigment minimum and dark tint |
| Other existing material effects | Existing reference color-gradient stops and the named frosted opacity, pigment and noise-scale controls |
| Camera, when explicitly selected | Orthographic or perspective pose, lens, shifts, sensor settings, clipping and numeric depth of field |

For the default mint style, select the protein material **blue** in Blender's
Shader Editor. Its visible pigment comes from **Clay shadow → Color1**, because
the Principled Base Color input is connected to the clay shading nodes. Adjust
the shadow/highlight colors as well if you want a consistent new palette.
Principled **Roughness** controls how matte it looks.

The importer changes only settings you edited; it retains the original preset's
representations and selections. Colors follow the renderer's existing numeric
`#RRGGBB` convention and are rounded to 8-bit channels. HDR color values and
color-socket alpha are reported instead of silently clipped. Linked socket
defaults do not replace the nodes driving those sockets.

Mesh sculpting, atom/object movement, additional or removed objects/lights,
material reassignment, node rewiring, arbitrary new shaders, modifiers,
keyframes and unsupported effect controls stay in the `.blend`. They cannot be
represented by this preset format. Changes to the captured render state that
have no supported mapping cause export to stop **without saving a preset**.
The report identifies the unmapped settings. Node labels and layout can change
without affecting the look. Duplicated objects/materials/nodes require a separate
scene workflow and are rejected.

Use `--allow-partial` (PyMOL: `allow_partial=1`) only if you deliberately want a
preset containing the supported part of the edit. The result and saved provenance
record the omitted changes. Keep the `.blend` for the complete composition; a
partial preset does not promise to reproduce it. PyMOL color-follow and generated
per-chain materials also remain input-dependent: edit their source JSON controls
when you need to change that behavior. For a non-paint preset with generated
per-chain materials, shared settings such as roughness must be changed
consistently in the base and generated materials. Inconsistent edits are
reported, not applied to all chains behind your back. Paint uses one shared
shader, with its per-atom palette retained, so changing its texture controls is
a single operation.

## Camera and animation choices

By default, `preserve_input` keeps the pose of each new structure. Camera changes
made in Blender are listed as ignored. To make the Blender composition part of
the preset, choose:

```sh
mol-blender dev from-blend renders/my-look.blend --preset user/my-camera-look \
  --camera-policy authored --report renders/my-camera-report.json
```

Or in PyMOL:

```pml
blender_preset_from_blend ~/Desktop/blender-edit/my-look.blend, user/my-camera-look, camera_policy=authored
```

Camera position, orthographic scale and clipping are normalized to the molecular
bounds. Light positions and sizes are likewise reusable. Different shapes and
aspect ratios can still need framing adjustments. Depth-of-field focus distance
uses the existing fixed angstrom setting; use a numeric distance instead of a
Blender focus object. A scene with depth of field requires `authored`; the default
policy refuses to discard focus silently. Camera scaling is unsupported; use lens/orthographic scale.
Light scaling is unsupported; use the light's Size control.

Imported looks omit the original render's animation recipe and automatic scene
saving. These omissions are explicit in the workflow: use the existing
`--animation` / PyMOL animation arguments when rendering again. The original
`.blend`, movie and frame archive are untouched. Blender-authored keyframes are
not converted to the renderer's animation recipes.

To revise a previously saved preset, add `--replace` (PyMOL: `replace=1`). That
creates the next local preset revision. Built-in presets cannot be overwritten.
Reports require a new filename. `--catalog-dir` remains available before the
`from-blend` subcommand for a project catalog.

## Runtimes and shared rendering

Opening a window requires a desktop Blender executable. If the rendering runtime
is the headless Linux Studio `bpy` launcher, configure an existing desktop Blender
separately:

```sh
mol-blender setup --gui-blender /path/to/desktop/blender
```

Use the pinned Blender 5.0.1 for editing as well as rendering. The native Blender
runtime can normally serve both purposes. No installer runs
as part of opening or importing a scene. Preset extraction uses the configured,
pinned Blender runtime in a fresh background process; temporary copies of the
source and scene are removed afterward. Opening and extraction disable embedded
Python auto-execution. Work with trusted local `.blend` files; this is not an
upload or execution endpoint. The metadata text is data and must remain intact.
Older saved scenes without it need to be rendered again with this version and
`--save-blend`.

Slurm keeps one independent Blender process per submitted request. Do not combine
`cluster=...` with `open_blender=1`: submit with `save_blend=1`, fetch the result,
and open that downloaded file locally. Editing never runs on the shared gateway.
To render a new preset remotely, the existing client/server engine compatibility
checks still apply. An older server will not acquire new capabilities from a
local preset or a `.blend` upload.
