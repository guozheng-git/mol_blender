# Local animation

Create camera orbits and molecular animations through the local CLI, PyMOL
bridge or stdio MCP. The shared renderer writes a still, an MP4 and a lossless
frame archive; GIF output is optional.

FPS, orbit defaults, recipe timings and enforced limits come from
[`render_defaults.yaml`](../render_defaults.yaml), shared with PyMOL and
Structure Studio. Run `python scripts/sync_render_defaults.py` after editing it;
the generated JSON and animation schema bounds are checked during wheel builds.

## Controls

| CLI | PyMOL / MCP | Meaning |
| --- | --- | --- |
| `--animation rock` | `animation=rock` | Sinusoidal orbit, starting at the input pose |
| `--animation 360` | `animation=360` | Full orbit, with no duplicate endpoint frame |
| `--duration` | `duration` | Seconds: default 3 for rock, 6 for 360; maximum 15 |
| `--fps` | `fps` | Integer 1–60, default 24 |
| `--rock-angle` | `rock_angle` | One-sided amplitude in degrees, default 12, maximum 45; rock only |
| `--gif` | `gif=1` / `gif=true` | Also encode an infinitely looping GIF |
| `--animation none` | `animation=none` | Disable animation embedded in a preset |

Duration × FPS must be an integer from 4 to 360. Decoded frames are limited to
10 GiB and their lossless archive to 512 MiB. Lower the dimensions, duration or FPS
if the limit is exceeded. Raw-view rendering is incompatible with animation.
`--rough` retains motion and style while lowering render quality. GIF timing is
quantized to 10 ms; use MP4 when precise frame timing matters.

Any valid preset can include the same controls under `output.animation`:

```json
{"mode": "rock", "duration": 3, "fps": 24, "rock_angle": 12, "gif": true}
```

This is the value of `output.animation`, not a complete style. The rest of the
style remains unchanged. Explicit command options override these values.

## Outputs and runtime

For `-o renders/rock.png`, the outputs are `rock.png`, `rock.mp4`,
`rock.frames.zip` and, with `--gif`, `rock.gif`. The sibling `rock.render/` contains
copies plus resolved style, manifests, source hashes and logs. Existing outputs
are rejected. Failed runs retain diagnostics in that directory without publishing
a success PNG. The manifest records frame angles, orbit center and encoding
background. The camera is restored before saving an optional Blender scene.

FFmpeg must support libx264 and GIF encoding. `brew install ffmpeg` supplies this
on macOS. On Linux or a cluster, install/load an FFmpeg build with these encoders
alongside the configured native Blender runtime. The
[Apptainer build](runtime-environments.md#linux-and-apptainer) includes FFmpeg.
Encoder availability is checked before launching Blender.
Rendering and encoding share the request deadline (600 seconds by default).
MP4 uses standard YUV 4:2:0 H.264 for native player compatibility. Keep the PNG
archive when exact rendered pixels are needed; the MP4 is a lossy presentation.
MP4 pads odd dimensions to even values without cropping. Both movie formats use
white behind transparent pixels; lossless PNGs retain the rendered alpha.

`blender-render movie-export INPUT.frames.zip -o OUTPUT.gif` (or `.mp4`) verifies
archive paths, sizes, frame dimensions and hashes, then runs FFmpeg only. It does
not require Blender or the original structure. Export filenames must be new.

## PyMOL and agent use

Use the README's plugin setup, then for example:

```pymol
blender_render animation=360, duration=2, fps=12, gif=1, width=320, height=240, samples=8, output_name=spin.png
blender_movie_export filename=spin-again.gif
```

Install Pillow in PyMOL's environment for validated frame loading and export.
The GUI preview uses cached PNG frames and replaces the existing movie timeline;
use PyMOL's play control after loading. `auto_preview=0` saves files without changing
the timeline. Preview falls back to file paths in headless or unsupported GUI
contexts. Movie export accepts an explicit `source` archive or the most recent
verified animation. FFmpeg must be on the invoking process's PATH. The CLI works
without a PyMOL GUI.

Claude/Codex can use the same CLI or call stdio MCP `render_structure` with
`animation="rock"`, `gif=true`, and optional duration, FPS, dimensions and samples.
The response includes movie file paths. `read_render` is still the PNG inspection
tool; movie playback depends on the client's support for local media files.

## Shared PyMOL and Structure Studio recipes

Both clients use `co_pymol/scene/animation.py` for `turntable`, `rock`, `reveal`,
`cutaway`, `states` and `scenes`. Its sibling `camera.py` measures camera keys and
`capture.py` captures the anchor scene while preserving the caller's camera,
colors, enabled objects, secondary structure and pinned molecular states. See Studio's
[animation reference](../plugins/structure-studio/skills/structure-studio/references/animations.md)
for commands, prerequisites and quality controls. Existing `--animation rock|360`
commands retain their defaults.

`python -m co_pymol animate scene.pse --blender --recipe rock --duration 2
--fps 8 --rock-angle 17` uses the same recipe compiler as Studio. The PyMOL CLI
also accepts `--gif`/`--no-gif` for Blender movies. All named recipes use sampled
camera paths, including rock and turntable. Duration overrides scale the whole
timeline; segment rounding preserves the exact requested total frame count.
State movies must include every molecular state. PyMOL's native preview retains
its ghost-shell cutaway display, mapping the shared lid opacity to transparency.

The shared renderer also accepts `output.animation.mode = "path"` in a style or
output override. `path` is a list of 4–360 PyMOL views (18 finite numbers each),
played at `fps`. Optional `anchor` identifies the still's view; its default is 0.
`states` supplies one molecular-state index per view and requires the exported
payload's validated `state_coordinates`. `lid` supplies one opacity per view and
requires a preset with a ligand-cutout surface. The existing duration, frame,
archive and memory limits apply. Unknown fields are rejected.

Structure Studio compiles these paths and coordinates from a trusted local
session. It renders one local movie through the ordinary isolated preview runner,
records the resolved style and manifest, and stores the movies, frames and recipe
with the workspace's session bundle. A saved Blender file contains the anchor
scene; it is not an editable animation timeline.
