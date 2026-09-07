# Local rendering on a Mac

The managed runtime supports Apple Silicon Macs. Intel Macs can use the lightweight client with a remote cluster or HTTPS service. Blender runs through Metal when available and falls back to CPU. Apptainer is not needed on macOS.

Install the beta wheel from the release downloads into a Python 3.11 environment:

```sh
python3.11 -m venv ~/.venvs/blender-render
~/.venvs/blender-render/bin/python -m pip install 'pymol_plugin_bridge-0.2.0b1-py3-none-any.whl[server,mcp]'
~/.venvs/blender-render/bin/blender-render setup --install-runtime
```

Setup downloads Blender 5.0.1 and hash-locked Molecular Nodes 4.5.10 dependencies into user-owned application storage. It verifies the CCD snapshot used for ligand bonds. It does not modify an existing Blender installation. To use a compatible existing Blender, add `--blender /Applications/Blender.app/Contents/MacOS/Blender`; dependencies still live separately.

Setup prints a `run .../pymol_bridge_plugin.py` command. Enter that command in PyMOL, or add it to your `.pymolrc`. Then:

```python
blender_render
blender_render preset=dried_paint_green, transparent_background=1, save_blend=1
blender_render color_follow=1, output_name=my_pose.png
```

The command exports the enabled selection and current camera, starts or reuses an authenticated loopback service, waits for the render, and loads the PNG into PyMOL. The service has a single FIFO queue and shuts down after 15 idle minutes. Files in the temporary render directory are removed by default; use `keep_temp=1` only for debugging.

For the CLI and local Codex file adapter, configure a Python interpreter that already has `pymol2`:

```sh
blender-render setup --pymol-python /path/to/pymol/environment/bin/python
blender-render render saved_pose.pse --preset dried_paint_green --transparent --save-blend -o figure.png
blender-render doctor
blender-render service status
blender-render service stop
```

PyMOL's Python may differ from the Python 3.11 service environment. The exporter is a standalone script and runs in PyMOL's interpreter. Importing the lightweight client does not import Blender or Molecular Nodes.

`figure.png`, `figure.blend`, and `figure.manifest.json` are local downloads and remain until you remove them. Service copies are deleted after download, with a fifteen-minute deadline for undelivered files. Inputs are deleted after rendering. The default preset is still preset 1; `--rough` reduces performance settings without changing its material, colors, geometry, or camera.
