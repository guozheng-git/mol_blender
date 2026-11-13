# Animation recipes and templates

These recipes adapt co-pymol's keyframe timelines and camera measurement to the
shared local Blender renderer. No co-pymol installation or remote backend is needed.

## Choose a recipe

- `turntable`: one full turn in 10 seconds, constant speed and no duplicate endpoint.
- `rock`: a looping ±25° sway over 6 seconds; `--rock-angle` changes its amplitude.
- `reveal`: hold the overview (1.5 s), ease toward the pocket (3 s), hold (1 s),
  then rock ±12° (4 s). Requires a focus region or a non-additive ligand.
- `cutaway`: hold the overview (1.5 s), push in (3 s), hold (0.5 s), fade away the
  surface covering the pocket (2 s), hold (0.5 s), rock ±12° (4 s). Uses
  `cyan_mint_cutaway` by default. The remaining surface keeps its preset material;
  the removed lid fades completely. The still is the first fully opened close-up.
- `states`: play each supplied molecular state in order while rocking ±10°.
  Duration is 0.25 s per state, bounded to 4–15 s. Requires one enabled molecular
  object with 2–360 states, identical atoms and atom ordering in every state.
  These are discrete supplied conformations, not a simulated trajectory or morph.
- `scenes`: start on the first saved PyMOL scene, hold 1.5 s, then ease to each
  subsequent scene in 2.5 s and hold 1.5 s. Only cameras move; current enabled
  objects, colors, representations and molecular state are preserved. Needs at
  least two saved scenes. Tours longer than 15 s require an explicit shorter duration.

A reveal changes the view to look into the focus pocket; cutaway pushes in along
the current orientation. Focus controls the camera destination; the preset still
determines which ligand cutouts and surface chains are drawn. For several ligands,
check the preview and hide unwanted ligands through the workspace scene if needed.
All measurements take place in a separate headless
PyMOL session. The input file and live PyMOL session are not modified. DSS and CIF
export use the same exporter as ordinary local renders.

## Commands

```bash
python3 "$S" recipes
python3 "$S" render model.cif --recipe turntable --gif
python3 "$S" render model.cif --recipe reveal --focus 'chain A and resn BTN' --gif
python3 "$S" render model.cif --recipe cutaway --focus 'chain A and resn BTN' --gif
python3 "$S" render ensemble.pse --recipe states --gif
python3 "$S" render tour.pse --recipe scenes --duration 10 --gif
python3 "$S" render model.cif --animation-template "$SKILL_DIR/templates/animations/rock.json"
```

For workspace renders add `--workspace SNAPSHOT_DIR --url URL --job ID` as in
SKILL.md. For states/scenes from a `.pse`, use the restored session rather than
the workspace's single-state CIF. Restore all source-session asset parts with
`studio.py restore PARTS... --out-dir DIR`, or use `restore --record` for a render.

## Reusable JSON

Copy a bundled file from `templates/animations/`, then edit that copy. The files
are portable: they contain no structure paths, camera coordinates or output paths.

```json
{"version": 1, "recipe": "rock", "duration": 6, "fps": 24, "rock_angle": 25, "gif": true}
```

Accepted fields are exactly `version`, `recipe`, `duration`, `fps`, `rock_angle`,
`gif` and `focus`. Version is 1. `focus` is a PyMOL selection string and applies
only to reveal/cutaway. `rock_angle` applies only to rock, from greater than 0 to
45 degrees. Unknown fields are rejected. CLI duration/FPS/angle/focus/GIF options
override template values. `--no-gif` disables a template's GIF.

`--recipe`, `--animation-template` and the legacy `--animation rock|360` are
mutually exclusive. Legacy orbits keep their existing 3 s/±12° rock and 6 s spin
defaults. Recipes use the saved input camera and a named preset; they reject
`--no-preserve-view` and `--style-json`. Presets control materials/representations,
and the workspace controls visible atoms and the starting camera.

## Quality and limits

Recipe movies default to 640 px width, the input viewport's aspect ratio, 24 fps
and 32 samples. Set `--width`, `--height` and `--samples` explicitly for finals.
`--full-quality` retains preset samples; `--rough` changes performance settings
only. Duration rescales the full timeline while preserving its order. FPS must
be 1–60; duration × FPS must be an integer from 4–360 and duration at most 15 s.
Every state must appear at least once. Decoded frames are capped at 10 GiB and
the frame archive at 512 MiB. Reduce dimensions, duration or FPS if needed.

Start with a short low-resolution preview and inspect the motion, focus and
clipping. Frame 0000 is the initial view; for cutaway inspect the recorded anchor
and frames on either side of the fade. Missing states/scenes, different atom
counts, unsupported cutaway presets and state-incompatible geometry fail rather
than substituting another motion. State movies cannot use cutouts or wire overlays.

FFmpeg with libx264 and GIF encoders must be on PATH. Setup can reuse an existing
FFmpeg installation; it does not install it. One invocation builds the scene,
renders its frames, and writes PNG, MP4, optional GIF, a lossless `.frames.zip`,
`.animation.json` (reusable template), and the renderer's manifest/resolved style.
Use `blender-render movie-export MOVIE.frames.zip -o NEW.mp4` (or `.gif`) to encode
again without Blender. Output files are never overwritten.

## Workspace storage and playback

Follow `render --workspace`'s printed upload/record steps. Movie files and the
recipe are included in the existing checksum-verified session bundle, split into
uploadable JSON parts when necessary. The render record contains recipe, FPS,
duration, frame count and anchor metadata. The gallery's **Play animation** button
decodes the MP4 locally; **Save MP4**, **Save GIF**, **PNG frames** and **Animation
recipe** retrieve the respective files. Republish an older workspace page once
using the existing workspace-update recipe to enable these new controls.

The `.pse` preserves the prepared molecular session; `.blend` is the rendered
anchor scene. Neither is an editable Blender movie timeline. Keep the recipe,
frame archive and source session to repeat or re-encode a movie.
