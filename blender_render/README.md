# PyMOL -> Blender Bridge (JSON Preset Pipeline)

This bridge uses one render-definition format: JSON style documents.
Preset names (`preset_1_interface` ... `preset_5_black_interface`) are shortcuts to JSON files in this folder.

## Load in PyMOL

```python
run /home/magnusb/blender_bridge/blender_render/pymol_bridge_plugin.py
```

## Main PyMOL command

```python
blender_render selection='(all)', preset='preset_1_interface', mode='local'
```

`blender_render` with no arguments defaults to local preset-1 render.

Useful flags:
- `rough=1` for fast trial renders.
- `style_json='/abs/path/style.json'` to bypass preset shortcuts.
- `color_follow=1` to adapt Blender materials from current PyMOL colors.
- `save_blend=1, blend_output='scene.blend'` to export a Blender session file.
- interactive sessions auto-close when this PyMOL process exits; use `auto_close_interactive_session=0` to disable.
- `mode='local' | 'interactive' | 'slurm' | 'interactive_open' | 'interactive_status' | 'interactive_close'`.

## Interactive SLURM guide inside PyMOL

Interactive mode keeps one warm GPU worker and queues renders FIFO (one-at-a-time).

1. Cold-start and render in one command (auto opens session if needed):

```python
blender_render selection='(all)', preset='preset_1_interface', mode='interactive'
```

Expected on first start: `Interactive GPU session ready`

By default, session bootstrap uses:

```text
qlogin -p gpu --gres=gpu:a4000:1 --mem=64g -c 4
```

2. Explicitly open/reuse a session:

```python
blender_render mode='interactive_open', attach=1
```

- `attach=1`: reuse an existing healthy session if one exists, otherwise open a new one.
- `attach_only=1`: fail if no healthy session exists (no new session created).
- auto-close is enabled by default; pass `auto_close_interactive_session=0` to keep session alive after this PyMOL exits.
- In a second PyMOL client, `attach=1` or `attach_only=1` lets you join the same warm session.

3. Check status:

```python
blender_render mode='interactive_status'
```

Or target a specific session:

```python
blender_render mode='interactive_status', interactive_session='<session_id>'
```

4. Submit renders to the warm worker:

```python
blender_render selection='(all)', preset='preset_1_interface', mode='interactive', interactive_session='<session_id>'
```

Requests are processed FIFO. Per-request result/status includes:
- `session_id`
- `submitted_by`
- `queue_position`
- `started_at`
- `finished_at`
- `status`

5. Close the session:

```python
blender_render mode='interactive_close'
```

You can also close a specific one:

```python
blender_render mode='interactive_close', interactive_session='<session_id>'
```

### Interactive spool/session paths

Default root:
- `$HOME/.pymol_bridge/sessions/<session_id>/`

Layout:
- `requests/<request_id>/request.json`
- `responses/<request_id>/result.json`
- `responses/<request_id>/output.png`
- `session/session.json`
- `session/ready.flag`
- `session/heartbeat.json`

Set `PYMOL_BRIDGE_SESSION_ROOT` to change the root directory (must be shared/visible to login + compute nodes).

### Worker resilience

- Heartbeat is updated while worker is alive.
- Stale request locks are recovered by worker loop.
- On `interactive` submit, stale/dead worker is auto-restarted before queueing the request.

## bridge_runner vs pse_render_cli

`bridge_runner.py`:
- Low-level execution backend.
- Runs `local`, `slurm`, `interactive-open`, `interactive-submit`, `interactive-status`, `interactive-close`, and worker loop.
- Consumes a prepared payload JSON (`--payload-json`) plus preset/style options.
- Owns session lifecycle, spool protocol, queueing, and subprocess/container execution.

`pse_render_cli.py`:
- High-level convenience wrapper for batch usage from `.pse` files.
- Loads `.pse` with `pymol2`, builds payload (`cif_text`/`view`/metadata), writes temp payload JSON, then calls `bridge_runner.py`.
- Does not replace session management commands; for interactive mode it submits to an existing `--interactive-session`.

Rule of thumb:
- Use `blender_render` in PyMOL for interactive user workflows.
- Use `pse_render_cli.py` for scripted `.pse -> png` runs.
- Use `bridge_runner.py` directly for debugging backend/session behavior.

## JSON presets shipped

- `style_preset_1_opaque_pastel.json`
- `style_preset_2_frosted_blue.json`
- `style_preset_3_black_tubes.json`
- `style_preset_4_milky_green.json`
- `style_preset_5_black_interface.json`

All presets include broad selections for polymers, nucleic acids, ligands, ions, and waters.

`preset_1_interface` mirrors the design constants from
`/home/magnusb/blender_bridge/render_cd3_single_frame_standalone.py`
in JSON form:
- same chain A/B inner materials, PTR ligand material, and cartoon dimensions
- same world/background and key/fill/rim light vectors
- same PyMOL camera world-scale (`0.014`)
- camera framing uses override mode with `direction_source: pymol`,
  `focus_style: ligand_ptr_sticks`, and `distance_scale: 3.1`

How it is applied in Blender:
- `bridge_runner.py` resolves `preset_1_interface` to `style_preset_1_opaque_pastel.json`.
- `blender_custom_render.py` loads CIF + PyMOL view, builds styles from JSON, assigns materials to GN style nodes, computes scene bounds, and places lights.
- For lights with `relative_vector`, `rotate_with_pymol_yaw: true` rotates vectors around the PyMOL up-axis using `camera.yaw_offset_deg`.

## Command line from `.pse`

Run with PyMOL's Python interpreter:

```bash
/opt/pymol/bin/python blender_render/pse_render_cli.py \
  --pse /home/magnusb/blender_bridge/CD3.pse \
  --preset preset_1_interface \
  --mode local \
  --rough 1 \
  --save-blend 1 \
  --blend-output render_session.blend
```

Custom JSON style example:

```bash
/opt/pymol/bin/python blender_render/pse_render_cli.py \
  --pse /home/magnusb/blender_bridge/CD3.pse \
  --style-json /home/magnusb/blender_bridge/blender_render/style_preset_1_opaque_pastel.json \
  --mode local \
  --color-follow 1
```

Interactive submit from `.pse` (session must already exist):

```bash
/opt/pymol/bin/python blender_render/pse_render_cli.py \
  --pse /home/magnusb/blender_bridge/CD3.pse \
  --preset preset_1_interface \
  --mode interactive \
  --interactive-session <session_id>
```

## Runner CLI

```bash
python blender_render/bridge_runner.py local \
  --payload-json /tmp/pymol_bridge/<run_id>/payload.json \
  --preset preset_1_interface
```

```bash
python blender_render/bridge_runner.py interactive-open \
  --attach 1
```

```bash
python blender_render/bridge_runner.py slurm \
  --payload-json /tmp/pymol_bridge/<run_id>/payload.json \
  --preset preset_1_interface \
  --slurm-partition gpu \
  --slurm-account my_account
```

## Paths

- Final outputs default: `<cwd>/renders/<timestamp>/...`
- Temporary payload files: `/tmp/pymol_bridge/<run_id>/...`
- Interactive sessions: `$HOME/.pymol_bridge/sessions/<session_id>/...`

## macOS install (Blender + Molecular Nodes)

1. Install Blender

```bash
# Install Blender.app in /Applications (default expected path)
ls -l /Applications/Blender.app/Contents/MacOS/Blender
```

- Download Blender from blender.org and drag `Blender.app` into `/Applications`.
- Launch Blender once, then quit, so user folders are created under `~/Library/Application Support/Blender/`.

2. Install Molecular Nodes extension

- Open Blender.
- Go to `Edit -> Preferences -> Extensions`.
- Search for `Molecular Nodes` and install/enable it.
- Restart Blender once after installation.

3. Verify Molecular Nodes files exist

```bash
find ~/Library/Application\ Support/Blender -path "*/extensions/blender_org/molecularnodes" -maxdepth 8 -type d
```

4. Test from PyMOL

```python
run /Users/mb/git/blender_render/blender_render/pymol_bridge_plugin.py
blender_render
```

If Blender is not installed at `/Applications/Blender.app/Contents/MacOS/Blender`, pass an explicit path:

```python
blender_render blender_exe='/absolute/path/to/blender'
```

If you see Molecular Nodes/CCD errors, reinstall the Molecular Nodes extension and run `blender_render` again.

## Notes for macOS

- Local rendering is Blender-native (`--blender-exe`) and does not require Apptainer.
- Apptainer execution is Linux-only. On macOS, use local Blender execution.
- If Blender is not at the app-bundle default path, pass `blender_exe='...'` in `blender_render`.
