# Structure Studio plugin

View, analyze and render protein and nucleic-acid structures from Claude:

- **Viewer panel**: an interactive Mol* page published as an artifact, with styles, colors, ligand
  binding sites, snapshots and a Copy view button that hands the camera to PyMOL and Blender.
- **PyMOL sessions**: `.pse` files and ray-traced figures (overview, surface, binding site,
  secondary structure, pLDDT confidence).
- **Blender renders**: Cycles stills, rocking or 360° GIFs and `.blend` scenes from the 14 built-in
  presets of this repository's renderer, run through Blender's `bpy` Python module.

The plugin has one skill (`structure-studio`) and three commands: `/view-structure`,
`/render-structure` and `/pymol-session`.

## Install

The same plugin works in claude.ai (web), the Claude desktop app and Claude Code.

**claude.ai and the desktop app** (Customize → Plugins):

- *Upload plugin*: build the zip with `python scripts/build_plugin.py` and upload
  `dist/structure-studio-plugin.zip`.
- *Add marketplace*: enter this GitHub repository. It is private, so first grant the Claude
  GitHub App access to it.

Plugins added to your account are available in web chat, desktop chat and Cowork. Rendering in
chat needs network egress for package managers (the default), so leave that setting on.

**Claude Code**:

```
/plugin marketplace add magnusbauer/blender_render
/plugin install structure-studio@blender-render
```

For a local checkout, use `/plugin marketplace add /path/to/blender_render` instead.

**Skill only**: upload `dist/structure-studio-skill.zip` under Customize → Skills.

## Try it

> /view-structure 4HHB, show the heme binding site
>
> Render it with dried_paint_green and vivid_binding_site, and give me the PyMOL session.

In claude.ai the PDB is blocked by default, so attach the `.cif` or allow `files.rcsb.org` in your
network settings. The first PyMOL or Blender step in each chat installs the runtime (about 20
seconds for PyMOL and 1–3 minutes for Blender). In Claude Code the install is kept in
`~/.cache/structure-studio`.

## Maintain

The plugin vendors a wheel of this repository's renderer. After changing `blender_render/`, run
`python scripts/build_plugin.py`; `tests/test_structure_studio.py` fails while the wheel or
preset catalog is stale.
