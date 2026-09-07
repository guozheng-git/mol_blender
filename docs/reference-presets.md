# Reference presets

Eight additional JSON presets extend the existing renderer. The six original preset identifiers and JSON files remain unchanged. PNG remains the default; `--save-blend` or `output.save_blend=true` explicitly requests a scene. The [README gallery](../README.md#preset-gallery) shows all 14 choices using actual local Blender renders of public structures.

| Reference | Preset / alias | Example | Match and scope |
|---|---|---|---|
| 1 | `powder_blue_rounded` / `reference_01` | [8AW3](https://www.rcsb.org/structure/8AW3) | Powder blue rounded protein, pink RNA, black background. Structure analogue; original identity unconfirmed. |
| 2 | `cyan_mint_cutaway` / `reference_02` | [8EA6](https://www.rcsb.org/structure/8EA6) | Cyan/mint surface, bounded pocket opening, intact interior ribbons, pink ligand and element-colored nearby residues. Explicit A/B surface selections. |
| 3 | `pastel_duplex_rounded` / `reference_03` | [7PZB](https://www.rcsb.org/structure/7PZB) | Thick rounded green/blue cartoons and thin pink DNA rungs. |
| 4 | `pastel_duplex_cartoon` / `reference_04` | [7PZB](https://www.rcsb.org/structure/7PZB) | Thin secondary-structure ribbons, blue/green chains, pink DNA and purple ligand. |
| 5 | `vivid_binding_site` / `reference_05` | [1AZM](https://www.rcsb.org/structure/1AZM) | Teal cartoon with enlarged element-colored ligand: cyan C, blue N, magenta O, yellow S. Acetazolamide supplies a real sulfonamide; original ligand identity unconfirmed. |
| 6 | `ivory_lime_frosted` / `reference_06` | [7PZB](https://www.rcsb.org/structure/7PZB) | Ivory/lime shell with a fine quad grid and glassy cyan DNA. Explicit A/B protein colors and C/D DNA residues 5–12; a closer composition analogue, with original structure identity still unconfirmed. |
| 7 | `mint_sky_assembly` / `reference_07` | [8AA5](https://www.rcsb.org/structure/8AA5) | Mint AP1/CP1, blue BP1/DP1; purple I/J/K and pink L/M/N nucleic acids, on pale cyan. |
| 8 | `neon_duplex_hero` / `reference_08` | [7PZB](https://www.rcsb.org/structure/7PZB) | Green cartoons, a spatial nucleic-acid color gradient, diagonal pink/cyan background. |

The complete 7PZB entry includes DNA chains E/F as well as C/D. The supplied session contained only C/D; using the full public entry restores the extended duplex in references 3, 4 and 8. Supplied sessions and molecular coordinates were not edited. Public 7R6R was also tried for reference 1 but had a less suitable silhouette. No exact pixel-match claim is made, especially for references 1, 5 and 6.

## Select and customize

```python
# Inside the existing PyMOL application; uses its current cmd object.
blender_render preset=cyan_mint_cutaway
blender_render preset=mint_sky_assembly, preserve_view=0
```

```sh
# Existing local profile, with its configured PyMOL and Blender runtimes.
blender-render render pose.pse --profile local --preset cyan_mint_cutaway -o pocket.png
blender-render render structure.cif --profile local --preset neon_duplex_hero --no-preserve-view -o hero.png
blender-render render pose.pse --profile local --style-json edited-style.json -o custom.png
```

Named presets preserve the input pose by default. `--no-preserve-view` selects the preset's authored camera; explicit camera overrides and full JSON retain their requested settings. Some styles use `direction_source=pymol`, so the initial orientation still matters. The gallery metadata stores the public example view and exact render dimensions; a preset alone cannot imply the same pose for an unrelated structure. Full JSON and preset-plus-overrides share the same resolver. Objects merge recursively; arrays such as `styles` replace in full.

The same installed PyMOL is used throughout. Its `pymol2` API creates isolated instances for file export; rendering from the running PyMOL application uses `cmd` directly. These changes do not add a second PyMOL dependency or replace the application's session.

## Cutaway geometry

The pocket cut operates on two isolated surface meshes. It never Boolean-cuts the shared interior cartoon, ligand or pocket-residue mesh. Native Molecular Nodes sticks remain sphere-free. Residues with an atom within 5 angstrom of the ligand expand to complete residues, so a distance cutoff cannot amputate a side chain. Their carbon is muted cyan, nitrogen blue, oxygen red and sulfur yellow; the ligand remains pink with a restrained compositor fog glow. The pocket sticks use a larger radius so the colored side chains remain legible through the opening.

The cut uses a 12-angstrom base radius with left/right scales 0.95, top 2.7, bottom 1.0 and back 0.9. Its open side follows the **final camera direction**, including explicit yaw/pitch overrides; its vertical axis follows camera up. `ligand_cutout_roll_deg=-10` tilts the opening within that frame without turning it away from the viewer. The lower shell remains in place. Cut faces inherit their surface pigment, remain flat-shaded and form closed walls. The real-molecule audit compares interior vertices and polygon indices before and after both cuts, checks complete residue selections and stored element colors, and counts boundary/nonmanifold edges.

This is a deliberately configured A/B example. For another complex, edit both surface selections and the pocket dimensions together; inspect the resulting PNG. Empty/absent ligands cannot establish a meaningful binding-pocket cut. Protein chain groups and assembly selections are explicit JSON, not inferred structure identity.

## Additional shared controls

- `camera.mode=override`: `projection`, local-axis `roll_deg`, `fit` and `fit_margin` fit evaluated geometry in landscape or portrait frames. `ortho_scale_angstrom` explicitly overrides automatic orthographic scale; `focus_style` targets the selected atoms' centroid. Explicit scale requires orthographic projection. Shifts that place the focus outside a fitted frame fail clearly.
- Surface styles: `surface_scale_radius` and `surface_relaxation_steps` expose native surface coarseness/smoothing. Defaults retain Molecular Nodes 4.5.10's previous values.
- Cartoon styles: `nucleic_base_scale` changes rung thickness without changing its length or the shared Molecular Nodes asset. `color_by_chain=false` preserves explicitly chosen pigments.
- `geometry.element_palette` stores element colors; `color_attribute=Color` uses native stick color boundaries. `ElementColor` is also available and requires an explicit palette. Existing color-follow/secondary-structure palettes can override `Color`; `ElementColor` retains the element mapping.
- Materials: `color_gradient` supplies spatial pigment; `frosted` combines patch opacity and an optional shader grid. `frosted.coordinates=angstrom` keeps patch size tied to molecular coordinates instead of the combined object's bounding box. Frosted opacity multiplies base alpha. A gradient or attribute replaces solid pigment; attribute and gradient cannot both be selected.
- `edge_highlight` controls continuous grazing-angle emission using `power`, `color` and `strength`. The cutaway uses pale white-pink rims around pink sticks, with a small `glow` pass. There is no cellular/noise texture on the ligand. Glow size retains the logarithmic pixel-size convention and is converted to the fractional image size used by newer Blender versions.
- `wireframe_width_angstrom` creates an isolated wire overlay from a style's geometry; `wireframe_spacing_angstrom` controls quad-cell spacing (default 1 angstrom). A uniform voxel remesh and smoothing operate only on the overlay. Rear-facing cells are removed using the final camera so transparent objects show a single readable grid. The ivory/lime preset uses separate overlays for the protein shell and cyan DNA. The original molecular geometry remains intact. These authored overlays, like cutaways, must be regenerated after changing the camera in a saved scene.
- World: `gradient_direction` controls the visible background ramp; `ambient_color` and `ambient_strength` separate illumination from that background.

All controls use the existing schema and scene builder. Unsupported keys, zero gradient directions and inverted opacity ranges fail validation. No developer workbench, new deployment path or separate rendering engine is included.

## Evidence

The execution record is [blender-render-progress.md](plans/blender-render-progress.md). [Gallery metadata](../examples/presets/gallery.json) records public coordinate URLs/hashes, preset hashes, views, output sizes and image hashes. Full-size local previews and detailed runtime manifests are retained in the task's output folder; only public example thumbnails and sanitized evidence belong in Git.

The Blender regression command is:

```sh
PRESS_BIOTITE_CCD_PATH="$TEMP_CCD" BLENDER_RENDER_SITE="$RUNTIME_SITE"   "$BLENDER" --background --python-use-system-env --python-exit-code 1   --python scripts/validate_reference_render.py -- "$OUTPUT_DIR"
```

Use the configured Molecular Nodes extension environment. Factory startup without its dependency paths is not a successful runtime check. An explicit CCD path is exclusive: it does not mirror into the installed addon or shared user cache. Local tests and gallery renders do not verify Slurm, Modal, deployments, or installed Codex/ChatGPT attachment flows.
