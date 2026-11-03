# Local molecular renderer

This branch supports PyMOL export, direct local Blender rendering, preset authoring,
and optional stdio MCP for Claude/Codex. Keep one shared renderer in
`blender_render/blender_custom_render.py`. CLI, PyMOL and MCP use `local.py` and the
isolated `developer.preview` runner. The optional `blender_render.hosted` adapter
presents already-authorized outputs; keep service queues and remote execution out
of the renderer. Import hosted dependencies only when explicitly selected.
The optional `blender_render.cluster` service owns authentication, the private job
spool and Slurm/Apptainer submission. Workers call the same `local.py` renderer.
Keep local rendering the default; never accept executable session uploads or
client-supplied paths/commands on the cluster API. Reuse uv runtimes built from
the shared pins; Linux environments must stay at their original absolute paths.
The explicit local `--warm`/`warm=1` option may reuse an identical, privately
captured Blender process through `warm_worker`. Keep source/runtime identity
checks, bounded local IPC and idle shutdown. Fresh local rendering remains the
default; Slurm jobs keep one independent Blender process per submitted request.

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
- Saved Blender scenes carry data-only preset editing metadata. Disable embedded
  script execution when opening/importing them, report unmapped edits, and keep
  camera capture explicit. Desktop editing stays outside local/Slurm workers.
- The optional `editor` bridge accepts bounded, typed control changes on a
  revision-checked desktop scene over private local IPC. Keep bpy on the main
  thread, use captured copies for previews, and never expose code execution or
  desktop commands through the cluster API. Disconnect leaves the editor open.
- Run `python -m pytest` for relevant changes. Check a built wheel outside the
  checkout for packaging changes. Record actual runtime and visual checks in
  `docs/validation.md`; mocks do not establish Blender or host integration.
- Preserve upstream notices. Make coherent commits and respect the user's
  publication boundary.
