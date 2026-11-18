---
name: recreate-render-preset
description: Recreate the visual style of an example molecular structure render as a reusable mol-blender preset JSON, using local previews to compare and refine it. Use when given a reference image to match, not for ordinary rendering with an existing preset.
---

Produce a full, schema-valid style JSON and an honest visual comparison using this
repository's local renderer. A screenshot cannot reveal exact shader internals;
match its visible appearance without claiming the original parameters were recovered.

1. Locate the checkout, reference image and structure/PyMOL pose. Open the reference
   with the host's image viewer or MCP `read_render`; never infer its appearance
   from a filename, thumbnail description or preset name. For non-PNG references,
   retain the original and create a lossless PNG working copy. If no structure is
   supplied, create the draft JSON and ask for the structure/pose needed to validate
   it. A public example may demonstrate styling, but cannot prove the same composition.
2. Run `mol-blender dev catalog`, `dev inspect PRESET` and `dev schema`.
   Compare representation first (cartoon, surface, ribbon, sticks), then material,
   palette, background, lights, edges, composition and ligand emphasis. Preserve the
   supplied pose and chain colors unless matching the reference requires a change.
3. Write `observations.json` with the fields in the example below. Use actual catalog
   IDs and material names from the inspected preset. Put only supported fields in
   `overrides`; distinguish visible observations from uncertain implementation guesses.
   `required_capabilities` describes capabilities needed for the target; it is not
   evidence that the image used that implementation.

   ```json
   {
     "base_preset": "preset_1_interface",
     "representations": ["cartoon", "sticks"],
     "material_family": "clay",
     "palette": ["describe observed colors here"],
     "background": "describe observed background",
     "lighting": "describe highlights and shadows",
     "edges": "describe outlines and rounding",
     "composition": "describe camera, crop and relative sizes",
     "ligand_treatment": "describe visible ligand or state none visible",
     "required_capabilities": [],
     "overrides": {},
     "notes": "record uncertainty and what must stay fixed"
   }
   ```

4. Bind the observations to the reference's hash and create the full JSON:

   ```sh
   mol-blender dev reference reference.png --observations observations.json \
     --style-output candidate.json > decision.json
   mol-blender dev validate candidate.json
   ```

   This command records and ranks **your** observations; it does not perform vision.
   A low-fit decision means the generated JSON is only a starting point. Refine the
   full JSON against `style.schema.json`. If the requested effect is unsupported,
   describe the gap; do not invent fields or silently edit the engine as part of a
   preset-only request. `candidate.json` is a full style, whereas a saved catalog
   envelope contains metadata plus `style`; pass the full style to `--style-json`.
5. With local runtime configured (`mol-blender doctor`; setup in README), render:

   ```sh
   mol-blender render structure.pse --style-json candidate.json \
     --rough -o renders/match-01.png
   ```

   Open the first PNG beside the original at the same display size. Keep this
   render and its full style JSON as the first baseline. For each round:

   - Inspect the whole image. Check framing, silhouette and the shape of each
     visible component, then its color, highlights, shadows, texture, opacity
     and glow where applicable. Choose the difference that matters most to
     the user's brief. Resolve an ambiguous shape before tuning its finish.
   - Start from the best style so far. Change the settings for one visual
     feature and save the candidate JSON and PNG under a new name. Keep the
     pose, resolution, seed and color management fixed unless one is the
     subject of this comparison. If geometry changes with automatic camera
     fitting enabled, check the rendered framing for drift.
   - View the original, previous best and candidate side by side. Use matching
     detail crops when needed, then inspect the full scene for changes elsewhere.
     When the user approves a component, keep that render and its settings as
     a baseline for the component. Check it again after later edits, especially
     when lighting, emission or framing could affect its appearance.
   - Record the intended change, what improved or regressed, and which candidate
     to keep. Use the new style for the next round when it improves the target
     and preserves approved details. Otherwise continue from the previous best.

   Finish when the render meets the brief or the remaining differences follow
   from the example structure or an unsupported effect. Keep the native molecular
   geometry and identify those limits. Render the best style at final quality,
   repeat the visual comparison and inspect its `.render/manifest.json` and
   `resolved-style.json`. If Blender is unavailable, deliver the validated draft
   and mark the visual comparison as pending.
6. Save the reusable preset and prove it reloads in a fresh process:

   ```sh
   mol-blender dev save user/reference-match candidate.json \
     --description "Describe the observed style" --provenance decision.json
   mol-blender dev inspect user/reference-match
   mol-blender render structure.pse --preset user/reference-match \
     -o renders/saved-preset-check.png
   ```

   Use `--camera-policy authored` only when the preset should replace the input
   pose; the default is `preserve_input`. Explicit camera fields in a full style
   override that policy, so inspect them deliberately. Existing IDs need an explicit
   `--replace`. Built-ins must not be overwritten.

Deliver the full JSON, reference decision, final rendered PNG and concise remaining
mismatches. Keep generated artifacts in the user's output directory, outside tracked
source unless requested. Do not claim an exact reconstruction from a single image.
