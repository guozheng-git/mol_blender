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

- `co_pymol/` is the PyMOL-side MCP server; it runs in PyMOL's Python (3.10+) and must
  not import the renderer. Its detached `core/blender_worker.py` runs in the render
  environment and calls `local.render_payload` or `cluster.client.Client`. Keep it a
  sibling of `blender_render/` so the engine inventory is unaffected. Test it with
  PyMOL's Python: `python -m pytest tests/co_pymol`.
- Keep the pure recipe compiler in `co_pymol/core/animation.py` and reuse its
  PyMOL camera/capture helpers from Structure Studio. Studio reads these helpers
  from the bundled wheel; do not copy their algorithms into plugin scripts.
  Compiled paths enter the existing renderer payload. PyMOL's ghost-shell
  preview is a display mapping of the same cutaway opacity timeline.
- Export CIF first and assign secondary structure in PyMOL before export.
- Preserve enabled selections, transforms, colors and the saved camera by default.
- Keep `mint_sky_assembly` as the default, native Molecular Nodes ligand sticks, and rough
  mode limited to performance settings. Reject unsupported JSON fields.
- Render trusted local files only. PSE files can execute code during loading.
- Use `.agents/skills/recreate-render-preset/SKILL.md` for reference-image-to-preset
  requests. Keep the original reference separate and available throughout. Compare
  the whole image every round, but change one visual feature at a time. Judge each
  candidate against the reference and the best previous render, and preserve the
  render and settings for features the user has approved.
- Use `.agents/skills/cluster-onboarding/SKILL.md` to configure the cluster service
  for a new Slurm site. Keep site differences in `server.json`, not launcher code.
- For an authorized lab render, check the configured cluster connection before
  submission. A running gateway can have a closed local SSH tunnel. If needed,
  try `blender-render cluster connect --profile PROFILE` once with the saved
  profile; its noninteractive SSH can reuse existing authentication. Ask the user
  to reconnect in their terminal only if that attempt needs login/MFA or fails
  with an actionable connection problem. Never request or handle their MFA codes.
  Keep a local render receipt distinct from gateway acceptance and GPU execution.
  Reconnect and recover existing jobs by their saved ID/receipt; never create a
  duplicate render to repair a connection. Inspect ambiguous submissions first.
- Write manifests and resolved styles; clean temporary source snapshots unless
  debugging retention was requested. Never commit research data or local paths.
- Saved Blender scenes carry data-only preset editing metadata. Disable embedded
  script execution when opening/importing them, report unmapped edits, and keep
  camera capture explicit. Desktop editing stays outside local/Slurm workers.
- The optional `editor` bridge accepts bounded, typed control changes on a
  revision-checked desktop scene over private local IPC. Keep bpy on the main
  thread, use captured copies for previews, and never expose code execution or
  desktop commands through the cluster API. Disconnect leaves the editor open.
- Reuse the selected Blender editor session for all scene edits. Never launch
  another desktop Blender instance unless explicitly requested. Preserve unsaved
  edits. If an operation is unsupported, add a bounded bridge control or explain
  the limitation; do not create and open a replacement scene.
- Discover existing windows with `editor_sessions`, retain the selected
  `session_id`, and reconnect that session when needed. `editor_open` is only for
  an explicit request for another window. Background preview renders may run
  separately without opening desktop windows.
- Run `python -m pytest` for relevant changes. Check a built wheel outside the
  checkout for packaging changes. Record actual runtime and visual checks in
  `docs/validation.md`; mocks do not establish Blender or host integration.
- Preserve upstream notices. Make coherent commits and respect the user's
  publication boundary.
