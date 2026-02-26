# PyMOL -> Blender Bridge (JSON Preset Pipeline)

This bridge now uses one render-definition format: JSON style documents.
Preset names (`preset_1_interface` ... `preset_5_black_interface`) are only shortcuts to JSON files in this folder.

## Load in PyMOL

```python
run /home/magnusb/blender_bridge/pymol_plugin_bridge/pymol_bridge_plugin.py
```

## Main PyMOL command

```python
blender_render selection='(all)', preset='preset_1_interface', mode='local'
```

Useful flags:
- `rough=1` for fast trial renders.
- `style_json='/abs/path/style.json'` to bypass preset shortcuts.
- `color_follow=1` to adapt Blender materials from current PyMOL colors.
- `save_blend=1, blend_output='scene.blend'` to export a Blender session file.
- `mode='local' | 'interactive' | 'slurm'`.

## JSON presets shipped

- `style_preset_1_opaque_pastel.json`
- `style_preset_2_frosted_blue.json`
- `style_preset_3_black_tubes.json`
- `style_preset_4_milky_green.json`
- `style_preset_5_black_interface.json`

All presets include broad selections for polymers, nucleic acids, ligands, ions, and waters.

`preset_1_interface` now mirrors the design constants from
`/home/magnusb/blender_bridge/render_cd3_single_frame_standalone.py`
in JSON form:
- same chain A/B inner materials, PTR ligand material, and cartoon dimensions
- same world/background and key/fill/rim light vectors
- same PyMOL camera world-scale (`0.014`)
- camera framing now uses override mode with `direction_source: pymol`,
  `focus_style: ligand_ptr_sticks`, and `distance_scale: 3.1` to match
  the standalone framing behavior (not raw exact PyMOL camera position)

How it is applied in Blender:
- `bridge_runner.py` resolves `preset_1_interface` to `style_preset_1_opaque_pastel.json`.
- `blender_custom_render.py` loads CIF + PyMOL view, builds styles from JSON, assigns materials to GN style nodes, computes scene bounds, and places lights (preset vectors scaled from scene radius).
- For lights with `relative_vector`, optional `rotate_with_pymol_yaw: true` rotates vectors around the PyMOL up-axis using `camera.yaw_offset_deg`.

## Command line from `.pse`

Run with PyMOL's Python interpreter:

```bash
/opt/pymol/bin/python pymol_plugin_bridge/pse_render_cli.py \
  --pse /home/magnusb/blender_bridge/CD3.pse \
  --preset preset_1_interface \
  --mode local \
  --rough 1 \
  --save-blend 1 \
  --blend-output render_session.blend
```

Custom JSON style example:

```bash
/opt/pymol/bin/python pymol_plugin_bridge/pse_render_cli.py \
  --pse /home/magnusb/blender_bridge/CD3.pse \
  --style-json /home/magnusb/blender_bridge/pymol_plugin_bridge/style_preset_1_opaque_pastel.json \
  --mode local \
  --color-follow 1
```

Interactive submit from `.pse` (session must be opened first):

```bash
/opt/pymol/bin/python pymol_plugin_bridge/pse_render_cli.py \
  --pse /home/magnusb/blender_bridge/CD3.pse \
  --preset preset_1_interface \
  --mode interactive \
  --interactive-session <session_id>
```

## Runner CLI

```bash
python pymol_plugin_bridge/bridge_runner.py local \
  --payload-json /tmp/pymol_bridge/<run_id>/payload.json \
  --preset preset_1_interface
```

```bash
python pymol_plugin_bridge/bridge_runner.py slurm \
  --payload-json /tmp/pymol_bridge/<run_id>/payload.json \
  --preset preset_1_interface \
  --slurm-partition gpu \
  --slurm-account my_account
```

## Paths

- Final outputs default: `<cwd>/renders/<timestamp>/...`
- Temporary payload files: `/tmp/pymol_bridge/<run_id>/...`
- Interactive sessions: `$HOME/.pymol_bridge/sessions/<session_id>/...`

## Notes for macOS

- Local rendering is Blender-native (`--blender-exe`) and does not require Apptainer.
- Apptainer execution is Linux-only. On macOS, use local Blender execution.
- If Blender is not at the app-bundle default path, pass `blender_exe='...'` in `blender_render`.
