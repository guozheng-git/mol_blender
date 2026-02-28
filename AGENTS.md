# Blender Bridge Agent Guide

This document defines how to reproduce the validated `.pse -> .png` rendering behavior and how to implement/fix the PyMOL plugin bridge consistently across Linux and macOS.

## Reference Implementation (Current Working Style)

Use this file as the visual/technical reference for preset-1 style behavior:

- `/net/scratch/magnusb/git/blender_bridge/scripts/pse_to_png_onefile_standalone.py`

Core pipeline in the reference script:

1. Read `.pse` via `pymol2`.
2. Export one structure to `structure.cif` (CIF-first); use PDB only as explicit fallback.
3. Export PyMOL camera via `cmd.get_view()` (+ FOV/orthoscopic flags) to `view.json`.
4. Launch Blender in background and run an embedded Python renderer.
5. Load molecule in Molecular Nodes with CCD fallback (`biotite.setup_ccd.main()` if needed).
6. Apply style (cartoon for protein chains + sticks for ligand PTR) with soft pastel clay materials.
7. Reconstruct camera direction/up from PyMOL view matrix.
8. Render PNG and write metadata JSON.

Detailed implementation paragraph for `blender_render` (must match the reference script behavior exactly): when the user runs `blender_render` from inside PyMOL, the client should create one temporary run directory, pick the requested object/selection (default first visible object and `selection='(all)'`), export structure in CIF as `<tmp>/structure.cif` (PDB fallback only if CIF export/load fails), export camera with `cmd.get_view()` and also store `field_of_view` + `orthoscopic` into `<tmp>/view.json`, resolve preset JSON to concrete environment/render parameters, then submit one request payload to the render server; on the server side, Blender runs through one renderer entrypoint with explicit arguments `-- <structure_path> <view_json> <output_png>`; inside Blender, load structure through Molecular Nodes with CCD fallback (`biotite.setup_ccd.main()` only if CCD is missing), apply the preset-defined styles and materials to all detected content classes (protein, nucleic acid, ligands, ions, waters, chains) without legacy backends, reconstruct camera orientation from the PyMOL 18-float view matrix (world->camera rotation transpose, axis-map/sign correction, forward/up vectors, lens/FOV transfer), place normalized soft lights, render one PNG, optionally save `.blend` when requested, return PNG bytes + metadata to the client, and display that image in the PyMOL session; this exact flow must be identical for host-Blender and Apptainer-backed server workers.

## Important PTR Notes (Why It Often Looks Wrong)

The two biggest causes of mismatch are:

1. Structure source mismatch (`input.pdb` chosen before `input.cif`), which changes ligand bond topology.
2. Legacy sticks backend (`legacy_ball_and_stick`) introducing artifacts and version-sensitive geometry differences.

If exact style fidelity is required, do not use legacy sticks and do not silently prefer PDB over CIF.

## Critical Failure Guards (Must Not Regress)

These are hard requirements because they directly map to the current failure image (wrong PTR geometry + no secondary-structure cartoon).

1. PTR / ligand geometry:
   - Do not use any legacy sticks backend (`legacy_ball_and_stick` or equivalent).
   - Use CIF-first input for bond topology; PDB only fallback.
   - For the PTR-like style, disable atom spheres and render clean sticks only (preset-driven radius/quality).
2. Secondary structure:
   - Polymer rendering for preset 1 must use cartoon secondary-structure style, not backbone/tube fallback.
   - Before structure export from PyMOL, ensure secondary structure assignment exists (`dss` on the exported selection/object) so cartoon can be reconstructed reliably.
   - If secondary-structure metadata is missing, fail loudly or auto-run assignment; do not silently downgrade to tube-like trace rendering for preset 1.
3. Runtime traceability:
   - Write a per-run manifest JSON in the temp run dir containing:
     - chosen structure file (`structure.cif` or fallback `structure.pdb`)
     - whether `dss` was run
     - active sticks backend name
     - active polymer/cartoon backend name
   - This manifest is required for debugging fidelity mismatches.

## Required Architecture for Plugin

### User-facing command

- Expose `blender_render` in PyMOL after:
  - `run /net/scratch/magnusb/git/blender_bridge/blender_render/pymol_bridge_plugin.py`
- `blender_render` with no arguments must submit a remote render request using default preset 1.
- Client parameters for execution target are `server_host` and `server_port` (plus optional timeout).

### Operation model (server/client only)

- The only supported runtime model is remote server/client.
- Clients (PyMOL plugin and CLI) do not execute Blender directly.
- The render server owns execution backend selection:
  - host Blender worker
  - Apptainer worker (`apptainer exec --nv ...`) where available

### Client responsibilities

1. Build payload exactly like the validated flow:
   - run `dss`
   - CIF-first export (+ PDB fallback payload)
   - `cmd.get_view()` + `field_of_view` + `orthoscopic`
2. Send one request to server (`POST /render`) with:
   - payload (same JSON structure as current local pipeline)
   - preset/style configuration
   - render flags (`rough`, `raw_view`, `color_follow`, `save_blend`, `blend_output`)
3. Receive PNG bytes response, write to requested `output_dir/output_name`, return path to caller, and display in PyMOL.
4. Do not add/keep separate client-side execution modes (`local`, `interactive`, `slurm`) as primary paths.

### Server responsibilities

1. Run as a persistent service that listens for incoming render jobs.
2. Keep one FIFO queue and process exactly one render transaction at a time.
3. Keep one warm worker process and auto-restart it if stale/dead.
4. Reuse one renderer code path for both host and Apptainer backends (no style divergence).
5. Stage per-request temp bundle, produce run manifest, and clean up unless `keep_temp` is requested.
6. Expose service endpoints:
   - `GET /healthz`
   - `GET /status`
   - `POST /render`
   - `POST /shutdown` (optional operational endpoint)

### Preset system

- Use one JSON schema for presets (single source of truth).
- Remove legacy style paths/backends entirely.
- Every variable defined in preset JSON must be consumed in Blender code (no dead keys).
- Presets must be broad and robust:
  - render all present content by default
  - support proteins, DNA, RNA, ligands, and multi-chain complexes
  - no hidden assumptions on chain IDs/res IDs unless explicitly configured

### Color-follow mode

- Add a `color_follow` mode:
  - read PyMOL object/atom colors
  - map them to Blender materials in a style-preserving way ("inspired by PyMOL color", not raw flat color)

### Output and session

- Always return rendered PNG path and display it in PyMOL.
- Add option to save `.blend` session for inspection/reuse.

## Cross-Platform Constraints

- Linux:
  - client mode: remote
  - server backend: host Blender and Apptainer supported
- macOS:
  - client mode: remote
  - server backend: host Blender mandatory
  - Apptainer may be absent and must not block host-worker server operation

## Command-Line Compatibility

Keep support for the existing CLI invocation style used in tests, including explicit args:

```bash
pymol -cq ./CD3.pse -d "run /net/scratch/magnusb/git/blender_bridge/blender_render/pymol_bridge_plugin.py; blender_render selection='(all)', style_json='/net/scratch/magnusb/git/blender_bridge/blender_render/style_preset_1_opaque_pastel.json', mode='remote', server_host='127.0.0.1', server_port=8765, output_dir='.', output_name='render_exact5.png', rough=0; quit"
```

## Implementation Rules

1. Prefer CIF-first when both CIF and PDB are available.
2. Remove legacy sticks code paths and fallback tuning.
3. Keep camera transfer exact (`cmd.get_view()` -> Blender camera transform).
4. Ensure rough-mode only affects performance knobs, not style semantics.
5. Ensure host-worker and Apptainer-worker server backends share the same rendering code path.
6. Keep plugin install simple via `.pymolrc` `run .../pymol_bridge_plugin.py`.
7. Create one temporary run directory per server render invocation and delete it on success/failure by default; only keep it when an explicit debug/keep-temp option is set.
8. Client execution targeting must be host/port only.
9. Commit only when a coherent project milestone is complete (not per minor loop).

## Acceptance Criteria

1. `blender_render` with no args submits preset-1 render through server/client flow.
2. PyMOL plugin and CLI both submit the same payload schema to server and receive PNG bytes.
3. Server processes incoming jobs FIFO with a persistent warm worker.
4. Server can run with host Blender or Apptainer backend without style divergence.
5. macOS remote client flow works with host-worker server (no Apptainer dependency).
6. JSON preset keys are all wired and verified by test.
7. Legacy style/sticks code removed.
8. `color_follow` mode works and produces styled color-consistent output.
9. Optional `.blend` save works.
10. Updated remote command example renders a PNG successfully.
11. For the CD3 test case, PTR has clean sticks without ball-joint artifacts.
12. For the CD3 test case, protein shows cartoon secondary structure (not tube/trace fallback).
13. Preset 1 is invalid unless (CIF-first + cartoon backend + non-legacy sticks + no ligand spheres).
