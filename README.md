# PyMOL Plugin Bridge

Repository focus: PyMOL -> Blender bridge using JSON-defined style presets.
Operation model: **server/client only**.

- Server: persistent FIFO render service that returns PNG bytes.
- Client: PyMOL plugin or CLI that sends the same payload format as before.
- Execution target is configured by server runtime (`host Blender` or `Apptainer`), not by client mode.

Primary docs:
- `blender_render/README.md`
- See `blender_render/README.md` sections:
  - `Remote server mode (FIFO + persistent Blender worker)`
  - `Platform quick setup`

Core runtime files:
- `blender_render/pymol_bridge_plugin.py`
- `blender_render/pse_render_cli.py`
- `blender_render/render_server.py`
- `blender_render/render_worker_manager.py`
- `blender_render/remote_client.py`
- `blender_render/blender_custom_render.py`
- `blender_render/style.schema.json`
- `blender_render/style_preset_1_opaque_pastel.json`
- `blender_render/style_preset_2_frosted_blue.json`
- `blender_render/style_preset_3_black_tubes.json`
- `blender_render/style_preset_4_milky_green.json`
- `blender_render/style_preset_5_black_interface.json`

Quick start (server):

```bash
pixi run render-server
```

One-command wrapper (server + optional auto tunnel exposure):

```bash
./scripts/start_render_server_exposed.sh
```

With reverse tunnel exposure:

```bash
./scripts/start_render_server_exposed.sh --relay-target user@public-host --relay-port 8765
```

Verbose server logs:

```bash
pixi run render-server-verbose
```

Custom host/port:

```bash
pixi run python -m blender_render.render_server --host 0.0.0.0 --port 8765
```

Quick start (PyMOL client):

```python
run /net/scratch/magnusb/git/blender_bridge/blender_render/pymol_bridge_plugin.py
blender_render selection='(all)', preset='preset_1_interface', mode='remote', server_host='127.0.0.1', server_port=8765, rough=1
```
