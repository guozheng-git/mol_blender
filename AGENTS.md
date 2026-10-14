# Local molecular renderer

This branch supports PyMOL export, direct local Blender rendering, preset authoring,
and optional stdio MCP for Claude/Codex. Keep one shared renderer in
`blender_render/blender_custom_render.py`. CLI, PyMOL and MCP use `local.py` and the
isolated `developer.preview` runner; do not add service queues or remote backends.

- Export CIF first and assign secondary structure in PyMOL before export.
- Preserve enabled selections, transforms, colors and the saved camera by default.
- Keep `mint_sky_assembly` as the default, native Molecular Nodes ligand sticks, and rough
  mode limited to performance settings. Reject unsupported JSON fields.
- Render trusted local files only. PSE files can execute code during loading.
- Keep original reference pixels separate from previews. Inspect both before
  claiming visual similarity. Use `.agents/skills/recreate-render-preset/SKILL.md`
  for reference-image-to-preset requests.
- Write manifests and resolved styles; clean temporary source snapshots unless
  debugging retention was requested. Never commit research data or local paths.
- Run `python -m pytest` for relevant changes. Check a built wheel outside the
  checkout for packaging changes. Record actual runtime and visual checks in
  `docs/validation.md`; mocks do not establish Blender or host integration.
- Preserve upstream notices. Make coherent commits and respect the user's
  publication boundary.
