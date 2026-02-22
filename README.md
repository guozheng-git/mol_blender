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
pixi run install-bpy-kernel
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
- applies camera-distance correction if needed
- for `bpy>=5` kernels, applies a fallback opaque material so the model is visible (avoids gray/transparent Molecular Nodes shading incompatibility)
- saves the scene
- renders and displays a preview image

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
- `+X` left
- `+Y` down

Blender camera axes use:

- forward `-Z`
- `+X` right
- `+Y` up

Conversion uses `M = diag([-1, -1, 1])`.

- `cam_pos_world = o_model - R.T @ o_cam_p`
- `R_world_from_cam_blender = R.T @ M`

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
