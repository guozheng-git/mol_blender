# Blender Bridge Agent Guide

This document defines how to reproduce the validated `.pse -> .png` rendering behavior and how to implement/fix the PyMOL plugin bridge consistently across Linux and macOS.

## Reference Implementation (Current Working Style)

Use this file as the visual/technical reference for preset-1 style behavior:

- `/Users/mb/Documents/blender_bridge/scripts/pse_to_png_onefile_standalone.py`

Core pipeline in the reference script:

1. Read `.pse` via `pymol2`.
2. Export one structure to `structure.cif` (CIF-first); use PDB only as explicit fallback.
3. Export PyMOL camera via `cmd.get_view()` (+ FOV/orthoscopic flags) to `view.json`.
4. Launch Blender in background and run an embedded Python renderer.
5. Load molecule in Molecular Nodes with CCD fallback (`biotite.setup_ccd.main()` if needed).
6. Apply style (cartoon for protein chains + sticks for ligand PTR) with soft pastel clay materials.
7. Reconstruct camera direction/up from PyMOL view matrix.
8. Render PNG and write metadata JSON.

Detailed implementation paragraph for `blender_render` (must match the reference script behavior exactly): when the user runs `blender_render` from inside PyMOL, the plugin should create one temporary run directory, pick the requested object/selection (default first visible object and `selection='(all)'`), export structure in CIF as `<tmp>/structure.cif` (PDB fallback only if CIF export/load fails), export camera with `cmd.get_view()` and also store `field_of_view` + `orthoscopic` into `<tmp>/view.json`, resolve preset JSON to concrete environment/render parameters, then invoke Blender in background with one renderer entrypoint and explicit arguments `-- <structure_path> <view_json> <output_png>`; inside Blender, load structure through Molecular Nodes with CCD fallback (`biotite.setup_ccd.main()` only if CCD is missing), apply the preset-defined styles and materials to all detected content classes (protein, nucleic acid, ligands, ions, waters, chains) without legacy backends, reconstruct camera orientation from the PyMOL 18-float view matrix (world->camera rotation transpose, axis-map/sign correction, forward/up vectors, lens/FOV transfer), place normalized soft lights, render one PNG, optionally save `.blend` when requested, return the final PNG path to PyMOL, and display that image in the PyMOL session; this exact flow should be shared by local/apptainer/interactive modes so style output is identical across modes and platforms.

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
  - `run /home/magnusb/blender_bridge/blender_render/pymol_bridge_plugin.py`
- `blender_render` with no arguments must run local render using default preset 1.

### Modes

- Keep one command surface for all execution modes:
  - local
  - apptainer
  - interactive GPU session
- On macOS, local mode must work without Apptainer.
- Do not create a separate user-facing command for interactive mode.

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
  - local and apptainer modes supported
- macOS:
  - local mode mandatory
  - apptainer path may be absent and must not block local render

## Command-Line Compatibility

Keep support for the existing CLI invocation style used in tests, including explicit args:

```bash
pymol -cq ./CD3.pse -d "run /home/magnusb/blender_bridge/blender_render/pymol_bridge_plugin.py; blender_render selection='(all)', style_json='/home/magnusb/blender_bridge/blender_render/style_preset_1_opaque_pastel.json',mode='local', use_apptainer=1, apptainer_image='/home/magnusb/blender_bridge/blender_render/blender_bridge.sif',output_dir='.', output_name='render_exact5.png', rough=0; quit"
```

## Implementation Rules

1. Prefer CIF-first when both CIF and PDB are available.
2. Remove legacy sticks code paths and fallback tuning.
3. Keep camera transfer exact (`cmd.get_view()` -> Blender camera transform).
4. Ensure rough-mode only affects performance knobs, not style semantics.
5. Ensure local mode and interactive mode share the same rendering code path.
6. Keep plugin install simple via `.pymolrc` `run .../pymol_bridge_plugin.py`.
7. Create one temporary run/session directory per render invocation and delete it on success/failure by default; only keep it when an explicit debug/keep-temp option is set.
8. Commit only when a coherent project milestone is complete (not per minor loop).

## Acceptance Criteria

1. `blender_render` with no args renders preset 1 locally.
2. Same command with mode switch works for local/apptainer/interactive paths.
3. macOS local render works without Apptainer.
4. JSON preset keys are all wired and verified by test.
5. Legacy style/sticks code removed.
6. `color_follow` mode works and produces styled color-consistent output.
7. Optional `.blend` save works.
8. Test command above renders a PNG successfully.
9. For the CD3 test case, PTR has clean sticks without ball-joint artifacts.
10. For the CD3 test case, protein shows cartoon secondary structure (not tube/trace fallback).
11. Preset 1 is invalid unless (CIF-first + cartoon backend + non-legacy sticks + no ligand spheres).


