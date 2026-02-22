# PyMOL -> Blender (Molecular Nodes) Bridge

`pymol_blender_bridge` exports a PyMOL `.pse` session (including camera view) into a bundle and builds a Blender `.blend` scene that imports structures through Molecular Nodes with a matching camera transform.

## Requirements

- Python 3.10+
- PyMOL installed
- Blender installed
- Molecular Nodes installed and enabled in Blender
- Optional: `numpy` (math fallback is pure Python if missing)

## Install (local development)

```bash
pip install -e .
```

## Pixi Environment

This repo includes `/Users/mb/Documents/blender_bridge/pixi.toml`.

Install the environment:

```bash
pixi install
```

Run commands through Pixi:

```bash
pixi run test
pixi run bridge --help
pixi run export-1a3n
pixi run build-1a3n
pixi run render-press-examples
pixi run install-bpy-kernel
pixi run install-blender-live-kernel
```

## CLI Usage

Export a PyMOL session to a bundle:

```bash
pymol_blender_bridge export session.pse --out bundle_dir
```

Build a Blender scene from the bundle:

```bash
pymol_blender_bridge build-blend bundle_dir --blender /path/to/blender --out scene.blend
```

Open with Blender-side automation (runs inside `bpy` and focuses the molecule):

```bash
open -a /Applications/Blender.app --args \
  --python /Users/mb/Documents/blender_bridge/scripts/remote_open_focus.py -- \
  /Users/mb/Documents/blender_bridge/1A3N_scene.blend 1A3N
```

Notebook style (same pattern as `interactive_example.ipynb`, requires Blender-backed kernel):

- `/Users/mb/Documents/blender_bridge/interactive_blender_control.ipynb`
- `/Users/mb/Documents/blender_bridge/open_blender_scene.ipynb` (Python kernel; opens Blender UI in parallel and renders saved scene snapshots in notebook)
- `/Users/mb/Documents/blender_bridge/blender_live_connection.ipynb` (`blender_live` kernel, live Blender session controlled directly from notebook)

If you see `ModuleNotFoundError: No module named 'bpy'`, install/select the provided kernel:

```bash
pixi install
pixi run install-bpy-kernel
jupyter kernelspec list
```

Then open the notebook and choose kernel `blender`.

This notebook does direct `import bpy` control:

- opens `/Users/mb/Documents/blender_bridge/1A3N_scene.blend`
- hides default non-target meshes (including `Cube`) and focuses `1A3N`
- for `bpy>=5` kernels, applies a fallback opaque material so the model is visible (avoids gray/transparent Molecular Nodes shading incompatibility)
- saves the scene
- renders and displays a preview image

Parallel edit + notebook render flow:

1. Run `open_blender_scene.ipynb`.
2. Blender opens with the generated scene.
3. Edit camera/object in Blender UI and save (`Cmd+S`).
4. Re-run the notebook render cell to render the updated saved scene in notebook.

Live notebook -> Blender control:

- `open_blender_scene.ipynb` now includes a live command bridge section.
- After launching Blender from the notebook, run:
  - `move_target(dx=0.02, dy=0.0, dz=0.0, save=True)` to move `1A3N` in the open Blender window.
  - `set_target_location(x, y, z, save=True)` for absolute positioning.

Press-release style renders:

- Script: `/Users/mb/Documents/blender_bridge/scripts/render_press_release_examples.py`
- Output directory: `/Users/mb/Documents/blender_bridge/examples/press_release_renders`
- Generate 5 curated stylistic variants:

```bash
pixi run render-press-examples
```

Notebook cells are included in:

- `/Users/mb/Documents/blender_bridge/open_blender_scene.ipynb` (`### Press-Release Style Renders`)
- `/Users/mb/Documents/blender_bridge/blender_live_connection.ipynb` (`### Press-Release Style Renders`)

NotebookConnector / blender_notebook style live connection:

- Inspired by [NotebookConnector](https://github.com/BradyAJohnston/NotebookConnector) and [blender_notebook](https://github.com/Octoframes/blender_notebook).
- Install the Blender-launching kernel:

```bash
pixi run install-blender-live-kernel
jupyter kernelspec list
```

- Open `/Users/mb/Documents/blender_bridge/blender_live_connection.ipynb`.
- Select kernel `blender_live`.
- Running cells controls the same open Blender UI session directly (no subprocess bridge file needed).

Useful export flags:

```bash
pymol_blender_bridge export session.pse \
  --out bundle_dir \
  --state all \
  --selection "polymer" \
  --format pdb
```

## Bundle Format

The `export` command writes:

- `bundle_dir/bundle.json`
- `bundle_dir/view.json`
- `bundle_dir/structures/*.pdb` (or `*.cif`)

`view.json` includes:

- `pymol_view` (18 floats from `cmd.get_view(quiet=1)`)
- `fov_deg` (when available)
- `orthoscopic` (when available)
- `viewport_px` (when available)
- `notes`

## Coordinate Notes

PyMOL camera axes use:

- forward `-Z`
- `+X` right
- `+Y` up

Blender camera axes use:

- forward `-Z`
- `+X` right
- `+Y` up

Conversion uses `M = I` (no axis flip).

- `cam_pos_world = o_model - R.T @ o_cam_p`
- `R_world_from_cam_blender = R.T @ M`

Molecular Nodes uses `world_scale=0.01` (Angstrom -> Blender units), so the bridge scales PyMOL camera translation/clip distances by `0.01` to keep pose/framing aligned.

For orthographic mode, this bridge approximates:

- `ortho_scale ≈ 2 * abs(o_cam_p[2]) * tan(fov/2)`

If FOV is missing, fallback default is `50°`.

## Troubleshooting

- `pymol2 is not available`:
  - The bridge will fallback to spawning `pymol -cq ... -d ...`.
  - If that also fails, install PyMOL or use an environment with `pymol2`.
- `Could not find Blender executable`:
  - Pass a valid path to `--blender` or add Blender to `PATH`.
- `Install/enable Molecular Nodes extension/addon`:
  - Open Blender preferences and enable Molecular Nodes.
- Camera mismatch:
  - Verify the session has the saved view you expect.
  - Orthographic scenes are approximate due to PyMOL/Blender projection differences.

## Tests

Run unit tests:

```bash
python -m pytest
```
