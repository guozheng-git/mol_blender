# Reference presets

Eight additional JSON presets extend the existing renderer. The six original preset identifiers and JSON files remain unchanged. PNG remains the default; `--save-blend` or `output.save_blend=true` explicitly requests a scene. The [example gallery metadata](../examples/presets/gallery.json) shows all 16 choices using actual local Blender renders of public structures.

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
# Configured local PyMOL and Blender runtimes.
blender-render render pose.pse --preset cyan_mint_cutaway -o pocket.png
blender-render render structure.cif --preset neon_duplex_hero --no-preserve-view -o hero.png
blender-render render pose.pse --style-json edited-style.json -o custom.png
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

All controls use the existing schema and scene builder. Unsupported keys, zero gradient directions and inverted opacity ranges fail validation. Preset authoring and local previews use this same rendering engine.

## Evidence

See [local validation](validation.md) for checks executed on this branch.
[Gallery metadata](../examples/presets/gallery.json) retains the upstream public
coordinate URLs/hashes, preset hashes, views, sizes and image hashes. Those gallery
assets are historical examples, not newly verified renders from this branch.

Use the configured Molecular Nodes environment and pinned CCD. A successful
local render does not by itself verify image display in Claude or Codex.

## Cyan candy mesh

`cyan_candy_mesh` combines glossy cyan surface patches, a deep-blue core, fine
recessed mesh and vivid pink/yellow rounded helical tubes. The approved material
and geometry settings are retained in the full preset JSON. It is a stylized
representation of real coordinates, not a reconstruction of the reference illustration.

```sh
blender-render render prepared-pose.pse --preset cyan_candy_mesh -o candy.png
```

The preview uses public **1QJB**, with solvent removed. Original chain A residues
165–210 were relabeled C and original chain B residues 165–210 relabeled D,
without moving coordinates, to expose selected protein segments as colored
tubes. The remaining A/B residues form the cyan surface. Its other chains Q/S
are not selected by these styles. This is a demonstration-specific split, not
an automatic chain assignment or a claim that 1QJB has a central helical bundle.
For another structure, edit the three A/B surface selections and the C/D cartoon
selections together; preserve the intended backbone paths. The saved PyMOL
orientation determines the view; the authored camera fits an orthographic frame.
Use `--no-preserve-view` to select the preset's authored framing.

The gallery preserves the approved 840×650, 128-sample render. The preset is
available as `cyan_candy_mesh` or `builtin/cyan_candy_mesh`; it is distinct from
any personal `user/cyan_candy_mesh` copy.

## Northern Nights

`northern_nights` adapts the Northern Nights paper-render look: satin teal,
midnight-blue, violet and purple ribbon cartoons, luminous mint peptide/sticks,
and a pale mint/lavender diagonal background. The gallery uses public **1SPS**,
with its saved example pose; no private research structure or animation is included.

```sh
blender-render render prepared-pose.pse --preset northern_nights -o northern-nights.png
# Optional rigid turntable, not a molecular trajectory:
blender-render render prepared-pose.pse --preset northern_nights --animation 360 -o northern-nights.gif
```

In PyMOL, use `blender_render preset=northern_nights`. Chain D is the mint peptide
in the public example; other protein chains receive the teal-to-purple spatial
gradient. Phosphoresidues PTR/SEP/TPO are also shown as mint sticks, excluding
backbone N/C/O. For other complexes, edit both cartoon selection queries to
choose the intended peptide chain. Colors are encoded for the renderer's linear
shader inputs; they need not resemble the display palette as hexadecimal swatches.

This portable preset uses the shared renderer only. Its spatial gradient and
native glow approximate the original residue-by-residue coloring and custom
Gaussian emission halo. It does not add the original scene's computed hydrogen
bond guides, custom atom-color emission, or paper-specific topology patches.
It is a reusable style adaptation, not an exact recreation of that scene.
