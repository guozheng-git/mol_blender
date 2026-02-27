# PyMOL Plugin Bridge

Repository focus: PyMOL -> Blender bridge using JSON-defined style presets.

Primary docs:
- `blender_render/README.md`
- See `blender_render/README.md` sections:
  - `macOS install (Blender + Molecular Nodes)`
  - `Interactive SLURM guide inside PyMOL`
  - `bridge_runner vs pse_render_cli`

Core runtime files:
- `blender_render/pymol_bridge_plugin.py`
- `blender_render/bridge_runner.py`
- `blender_render/blender_custom_render.py`
- `blender_render/style.schema.json`
- `blender_render/style_preset_1_opaque_pastel.json`
- `blender_render/style_preset_2_frosted_blue.json`
- `blender_render/style_preset_3_black_tubes.json`
- `blender_render/style_preset_4_milky_green.json`
- `blender_render/style_preset_5_black_interface.json`
- `blender_render/pse_render_cli.py`

Quick start in PyMOL:

```python
run /home/magnusb/blender_bridge/blender_render/pymol_bridge_plugin.py
blender_render selection='(all)', preset='preset_1_interface', mode='local',rough=1
```
