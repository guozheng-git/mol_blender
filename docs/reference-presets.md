# Presets and selections

Choose a built-in look with `--preset ID` in the CLI or `preset=ID` in PyMOL.
`mint_sky_assembly` is the default. The [README gallery](../README.md#preset-gallery)
shows featured styles; `blender-render presets` lists all 17 built-ins and your
saved presets. PNG is the default output; `--save-blend` explicitly saves a scene.

## Public structure examples

These examples identify public inputs and the selections used by each style.
The `reference_01` through `reference_08` aliases remain supported.

| Preset / alias | Public example | Appearance and selection |
| --- | --- | --- |
| `powder_blue_rounded` / `reference_01` | [8AW3](https://www.rcsb.org/structure/8AW3) | Powder blue rounded protein, pink RNA and black background. |
| `cyan_mint_cutaway` / `reference_02` | [8EA6](https://www.rcsb.org/structure/8EA6) | Cyan/mint A/B surfaces, pocket opening, interior ribbons, pink ligand and element-colored nearby residues. |
| `pastel_duplex_rounded` / `reference_03` | [7PZB](https://www.rcsb.org/structure/7PZB) | Rounded green/blue cartoons and thin pink DNA rungs. |
| `pastel_duplex_cartoon` / `reference_04` | [7PZB](https://www.rcsb.org/structure/7PZB) | Thin blue/green protein ribbons, pink DNA and purple ligand. |
| `vivid_binding_site` / `reference_05` | [1AZM](https://www.rcsb.org/structure/1AZM) | Teal cartoon with enlarged acetazolamide sticks: cyan carbon, blue nitrogen, magenta oxygen and yellow sulfur. |
| `ivory_lime_frosted` / `reference_06` | [7PZB](https://www.rcsb.org/structure/7PZB) | Ivory/lime A/B protein shell, fine grid and glassy cyan DNA, with C/D residues 5–12 selected explicitly. |
| `mint_sky_assembly` / `reference_07` | [8AA5](https://www.rcsb.org/structure/8AA5) | Mint AP1/CP1, blue BP1/DP1, purple I/J/K and pink L/M/N nucleic acids on pale cyan. |
| `neon_duplex_hero` / `reference_08` | [7PZB](https://www.rcsb.org/structure/7PZB) | Green cartoons, a nucleic-acid color gradient and a pink/cyan background. |

Use the complete 7PZB entry to include DNA chains E/F as well as C/D.
[Gallery metadata](../examples/presets/gallery.json) records input provenance,
views and render settings. A preset supplies appearance; the molecular input,
chain selections and saved camera determine the composition.

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

Named presets preserve the input pose by default. `--no-preserve-view` selects the preset's authored camera; explicit camera overrides and full JSON retain their requested settings. Some styles use `direction_source=pymol`, so the initial orientation still matters. The gallery metadata stores each example view and exact render dimensions; a preset alone cannot imply the same pose for an unrelated structure. Full JSON and preset-plus-overrides share the same resolver. Objects merge recursively; arrays such as `styles` replace in full.

The same installed PyMOL is used throughout. Its `pymol2` API creates isolated instances for file export; rendering from the running PyMOL application uses `cmd` directly. File export leaves the application's session unchanged.

## Shared renderer defaults

Preset JSON keeps the required `renderer.engine` and its explicit appearance
settings. Optional execution settings inherit from
[`renderer_defaults.py`](../blender_render/renderer_defaults.py): GPU with AUTO
backend selection, opaque background, adaptive sampling enabled with threshold
0.01, denoising disabled, and seed 0. A preset or request can override each value;
for example, `"denoise": true`, `"seed": 19`, or `"device": "CPU"`.

The loader and request resolver expand these defaults before saving a preset,
writing the resolved style, or hashing a render request. An explicit default and
an omitted default resolve identically. Existing saved presets retain their
stored digest and acquire defaults when resolved for rendering. Unknown fields
and invalid authored values still fail validation before normalization.

Exposure, gamma, view transform, and look stay explicit in built-in presets.
Custom styles that omit those fields retain Blender's scene settings. GPU
selection still falls back to CPU when unavailable, unless `require_gpu` or
`PRESS_REQUIRE_GPU` requires a GPU. Preset and request settings can override
the shared preference. Check the resolved style for the requested device and
the render manifest's `cycles_device` for the device actually used.

## Cutaway geometry

The pocket cut operates on two isolated surface meshes. It never Boolean-cuts the shared interior cartoon, ligand or pocket-residue mesh. Native Molecular Nodes sticks remain sphere-free. Residues with an atom within 5 angstrom of the ligand expand to complete residues, so a distance cutoff cannot amputate a side chain. Their carbon is muted cyan, nitrogen blue, oxygen red and sulfur yellow; the ligand remains pink with a restrained compositor fog glow. The pocket sticks use a larger radius so the colored side chains remain legible through the opening.

The cut uses a 12-angstrom base radius with left/right scales 0.95, top 2.7, bottom 1.0 and back 0.9. Its open side follows the **final camera direction**, including explicit yaw/pitch overrides; its vertical axis follows camera up. `ligand_cutout_roll_deg=-10` tilts the opening within that frame without turning it away from the viewer. The lower shell remains in place. Cut faces inherit their surface pigment, remain flat-shaded and form closed walls. Inspect the rendered opening and selected residues when adapting the pocket to a new complex.

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

[Gallery metadata](../examples/presets/gallery.json) retains provenance,
preset hashes, views, sizes and image hashes. Public examples include coordinate
URLs/hashes. The Northern Nights previews are prepared-scene illustrations whose
molecular source files are not distributed; they are labeled separately from
public structure examples.

Use the configured Molecular Nodes environment and pinned CCD. A successful
local render does not by itself verify image display in Claude or Codex.

## Cyan candy mesh

`cyan_candy_mesh` combines glossy cyan surface patches, a deep-blue core, fine
recessed mesh and vivid pink/yellow rounded helical tubes. Its material and
geometry settings are available in the full preset JSON.

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

The gallery preview was rendered at 840×650 and 128 samples. The preset is
available as `cyan_candy_mesh` or `builtin/cyan_candy_mesh`; it is distinct from
any personal `user/cyan_candy_mesh` copy.

## Northern Nights: bright ribbons and dark cutaway

Two presets provide related bright and dark looks:

- `northern_nights`: residue-ordered teal/blue/violet protein ribbons, constant
  mint peptide, a bright mint/lavender background and a PTR close-up.
- `northern_nights_cutaway`: a rounded pocket opening and black background,
  using a violet–blue–purple binder palette and teal-to-mint peptide gradient.

Try the ribbon palette on the bundled public 1SPS structure:

```sh
blender-render render examples/1sps.cif --preset northern_nights --rough -o renders/northern-public.png
```

The gallery's phosphoresidue close-up and pocket opening require an appropriate
prepared complex. The two preview images illustrate that composition, but their
source scene is not distributed. For your own complex, prepare the selections
and camera before rendering:

```sh
# Keep your prepared input camera:
blender-render render prepared-pose.pse --preset northern_nights -o bright.png
blender-render render prepared-pose.pse --preset northern_nights_cutaway -o cutaway.png
# Select the authored PTR close-up framing (27 angstrom orthographic extent):
blender-render render prepared-pose.pse --preset northern_nights --no-preserve-view -o detail.png
# Optional camera rocking, not a molecular trajectory:
blender-render render prepared-pose.pse --preset northern_nights_cutaway --animation rock --gif -o rocking.png
```

In PyMOL, select `preset=northern_nights` or `preset=northern_nights_cutaway`.
These presets select chain B as the peptide. Adapt the chain palette and all
cartoon/surface/pocket selections together for your complex. The bright binder
palette is
`#25ABA5 → #3B5A8D → #463D73 → #8D52AD`; its peptide is `#40D9BE`.
The dark binder uses `#463D73 → #3B5A8D → #8D52AD`, with peptide
`#25ABA5 → #40D9BE`. Colors interpolate in sRGB by residue order within each
chain, then convert to linear shader values. They do not depend on orientation.

PTR/SEP/TPO sticks use a 0.36 Å radius and include Cα but exclude backbone
N/C/O/OXT. Carbon follows the peptide palette; oxygen stays vivid red and
phosphorus orange. Native stick color boundaries remain sharp. Only the
phosphoresidue material emits: strength **0.65**, in its atom colors. A Gaussian
blur of the emission pass is added at **0.7**, with width scaled from **22 pixels
at 1200 pixels**. The peptide cartoon itself has no emission; the halo is
localized to the phosphoresidue sticks.

Before DSS, the exporter temporarily classifies PTR/TPO/SEP as TYR/THR/SER,
then restores their original residue names even if DSS fails. Their DSS labels
are retained even when CIF records them as HETATM. Coordinates and bonds do not
change. Secondary structure depends on the input coordinates; phosphoresidues
are not automatically assigned to helices.

White capsule guides show **putative interchain polar contacts**, using PyMOL
`find_pairs` in both donor/acceptor directions, mode 1, 3.6 Å cutoff and 45° angle.
They use three dashes and a 0.20 Å radius. These are geometric inferences, not
experimentally established hydrogen bonds. The payload carries
at most 256 atom-key pairs; the renderer verifies their atoms and distances.
Inputs without exported contact metadata show no guides.

The dark preset cuts only an isolated protein surface with a bounded ellipsoid;
interior ribbons and sticks remain intact. A darker, sequence-colored inner wall
retains the binder palette. Adjust pocket shape, tilt and dimensions to your
complex. The cut is baked for the initial view and can be rocked as a rigid scene.
Animated lids and molecular
state animations are not supported with this rounded cut/contact-guide setup.

These controls use the shared renderer locally and on compatible cluster
workers. The client and worker must have matching renderer capabilities.
