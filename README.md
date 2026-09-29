# Blender Render — local PyMOL bridge

Render molecular structures with local Blender and Molecular Nodes, from PyMOL,
a command line, Claude Code or Codex. This branch keeps the shared renderer,
14 built-in presets, JSON preset authoring and an optional **stdio-only MCP** adapter.
It has no hosted service, OAuth, billing, Slurm, Modal or deployment commands.

## Install and configure

Use Python 3.11 (matching Blender's Python ABI):

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,mcp]'
```

Omit `mcp` if you only need the CLI/PyMOL bridge. Rendering requires the versions in
`blender_render/runtime.lock.json`: Blender 5.0.1, Molecular Nodes 4.5.10, NumPy
1.26.4 and Biotite 1.6.0, plus the pinned CCD database. PyMOL file import uses a
separate interpreter with `pymol2`; an existing PyMOL environment is suitable.

To install the pinned Blender runtime into user-owned storage on Apple Silicon
macOS or Linux x86_64 (downloads Blender and Python dependencies):

```sh
blender-render setup --install-runtime --pymol-python /path/to/pymol/python
```

To keep Blender, its dependencies and configuration inside this checkout, set the
configuration root before running setup:

```sh
export BLENDER_RENDER_HOME="$PWD/.runtime"
blender-render setup --install-runtime --pymol-python /path/to/pymol/python
```

That installs Blender at `.runtime/runtime/Blender.app` on Apple Silicon macOS.
Keep `BLENDER_RENDER_HOME` set when rendering or starting MCP so the same local
configuration is used. `.runtime/` is excluded from Git. The fresh Mac runtime
installation and an actual render have been [verified](docs/validation.md#fresh-mac-installation).

Or configure an existing runtime without installing anything:

```sh
blender-render setup \
  --blender /path/to/blender \
  --pymol-python /path/to/pymol/python \
  --runtime-path /path/to/python3.11/site-packages \
  --runtime-path /path/to/extensions/blender_org \
  --ccd /path/to/biotite/structure/info/components.bcif
blender-render doctor
```

Each `--runtime-path` must be an existing directory supplying the pinned rendering
dependencies. If using a Molecular Nodes extension with bundled wheels, add
`--runtime-wheels /path/to/molecularnodes/wheels`; these are extracted into each
isolated render environment. The dependency directories must include `jsonschema`.
`setup` saves configuration; `doctor` is read-only. A successful render verifies
source hashes, runtime versions and the CCD digest before reporting completion.
Configuration lives in `~/.config/blender-render/config.json`; set
`BLENDER_RENDER_HOME` to use a separate configuration directory.

## Render locally

```sh
blender-render presets
blender-render render examples/1sps.cif --rough -o renders/example.png
blender-render render structure.pse --preset dried_paint_green --save-blend -o renders/paint.png
blender-render render structure.pse --style-json my-style.json -o renders/custom.png
blender-render export structure.pse -o bundle.json
```

Inputs are trusted local `.pse`, `.cif`, `.mmcif`, `.pdb` or exported bundle `.json`
files. PSE loading can execute code; use sessions you trust. CIF/PDB imports get a
PyMOL-generated view. Sessions and bundles preserve their saved pose by default;
explicit camera fields in a style take precedence. `--no-preserve-view` opts into
the preset's camera. Rough mode changes only performance settings.

Each render writes the requested PNG plus a sibling `NAME.render/` directory with
`manifest.json`, `resolved-style.json`, source/runtime provenance and logs. Optional
Blender scenes are saved there as `render.blend`. Output names must be new. Failed
runs retain their logs; choose a new name after fixing the problem. Temporary source
snapshots are removed unless `--keep-temp` is requested.

## PyMOL bridge

Run `blender-render setup` from the installed environment first; this records the
Python used to run renders. In PyMOL, load the plugin using its absolute checkout path:

```pymol
run /absolute/path/to/blender_render/blender_render/pymol_bridge_plugin.py
blender_render
blender_render selection=(all), preset=dried_paint_green, output_dir=renders, output_name=paint.png
blender_render style_json=/absolute/path/to/my-style.json, output_name=custom.png
```

The adapter exports enabled objects, transforms, colors, secondary structure and
camera, calls local Blender through the configured Python, then loads the PNG into
PyMOL. Rendering is synchronous. `width`, `height`, `samples`, `rough`, `save_blend`,
`color_follow`, `preserve_view`, `blender_exe` and `timeout` are optional arguments.
Former service/profile/remote arguments are intentionally unsupported.

## Claude Code and Codex

Open this checkout in either agent and use the CLI above. The repository includes
`recreate-render-preset` in `.agents/skills/` and a linked entry in `.claude/skills/`.
Example request:

> Use recreate-render-preset to match reference.png on structure.pse. Save the full
> style JSON, compare local previews, and register it as user/my-reference.

The skill inspects the actual image, records observable features, generates a
validated starting JSON, refines it through local renders, then saves and reloads
the preset. It explicitly reports remaining mismatches. See the
[skill](.agents/skills/recreate-render-preset/SKILL.md).

Optional MCP configuration, using the absolute path to this environment:

```sh
codex mcp add blender-local -- /absolute/path/to/blender_render/.venv/bin/blender-render mcp
claude mcp add --transport stdio blender-local -- /absolute/path/to/blender_render/.venv/bin/blender-render mcp
```

The four tools are `presets`, `inspect_style`, `render_structure` and `read_render`.
Rendering returns local artifact paths; `read_render` returns the PNG as MCP image
content for inspection. Configure a client tool timeout appropriate for a render
(up to the default 600 seconds). The MCP process uses the same user's local config
and never opens an HTTP listener. Client registration is optional and is not done
by installing this package. These commands follow the official
[Codex MCP](https://developers.openai.com/codex/mcp/) and
[Claude Code MCP](https://code.claude.com/docs/en/mcp) documentation; repository skill
locations follow [Codex skills](https://developers.openai.com/codex/skills/) and
[Claude Code skills](https://code.claude.com/docs/en/skills).

## Preset authoring

```sh
blender-render dev catalog
blender-render dev inspect pastel
blender-render dev schema
blender-render dev reference reference.png --observations observations.json \
  --style-output candidate.json > decision.json
blender-render dev validate candidate.json
blender-render dev save user/my-reference candidate.json \
  --description 'My reference style' --provenance decision.json
blender-render render structure.pse --preset user/my-reference -o renders/saved.png
```

`dev reference` records agent-supplied observations; it is not an automatic image
recognizer. `--style-output` emits a complete style document and refuses to overwrite
an existing file. A saved catalog preset is a metadata envelope; use its ID with
`--preset`, or extract its `style` for `--style-json`. Save defaults to
`--camera-policy preserve_input`; choose `authored` deliberately for a reusable
camera composition. `dev preview --help` exposes explicit runtime paths and source
snapshots for renderer development. Built-ins cannot be overwritten.

## Development and license

```sh
python -m pytest
python -m build --wheel
```

See [validation](docs/validation.md) for executed checks and limits. Public example
structures and citations are in `examples/structures.json`. The client code is MIT;
Blender API scripts and Molecular Nodes assets retain their GPL terms. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
