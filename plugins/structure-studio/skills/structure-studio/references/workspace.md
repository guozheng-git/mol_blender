# Workspace data layout

One artifact per structure. The page is a fixed template, so everything that changes lives in the artifact's database and file store. Both survive republishing the page.

## Database

| Document | Written by | Contents |
| --- | --- | --- |
| `workspace/meta` | Claude (Recipe A) | `title`, `entry_id`, `source`, `description`, `created_at`; `structure` (`asset_id`, `filename`, `format`, `bytes`, `sha256`); `summary` (method, resolution, chains with genes and descriptions, ions, waters, predicted flag); `ligands` (keys, names, `covalent`, `additive`) |
| `scene/current` | Claude, or the page when someone uses the controls | The shared scene ([scene.md](scene.md)) plus `revision`, `updated_by` (`claude` or `page`), `note` and `written_at` |
| `scene/camera` | The page, after someone moves the view | `position`, `target`, `up`, `fov_deg`, `mode`, `by: "page"`, `saved_at`. Claude may write it with `by: "claude"` and a new `request_id` to move the panel's camera. |
| `scene/selection` | The page, on click | `items: [{chain, resi, ins, comp, atom}]`, `saved_at`. This is "the residue I clicked". |
| `renders/<id>` | Claude (Recipe C) | See below |

Render ids look like `r-20261014-101201-mint_sky_assembly`. A render document starts as `{"status": "rendering", "preset", "label", "started_at", "created_at"}` and becomes:

```json
{"status": "done", "preset": "mint_sky_assembly", "label": "Chain A hidden · B inhibitor site",
 "note": "…", "created_at": "…", "seconds": 24.8, "cpu_adjustments": {},
 "image": {"asset_id": "…", "filename": "….png", "width": 1600, "height": 1200, "bytes": 2155468, "sha256": "…"},
 "session": {"format": "structure-studio-session/1", "asset_ids": ["…"],
             "files": [{"role": "pse", "filename": "….pse", "bytes": 1538100, "sha256": "…"},
                       {"role": "blend", "filename": "….blend", "bytes": 607325, "sha256": "…"}]},
 "scene": {…}, "scene_revision": 2, "camera": {…}}
```

`{"status": "failed", "error": "…"}` marks a failed render. Older records carry `pse` and `blend` objects (each with `asset_ids` of gzip+base64 text parts) instead of `session`. The page and `studio.py unpack` still read them.

Recipe movies also store an `animation` object with `recipe`, `fps`, `frames`,
`duration`, `anchor`, dimensions, focus and the reusable template. Camera paths
and PNG frame data stay in files, not database documents. Their session bundle
adds roles `mp4`, `gif` (when requested), `frames` (the lossless ZIP), and
`animation` (the template JSON). The same upload/restore protocol handles them.
The gallery decodes the MP4 for playback and verifies bundle file hashes before
playback or download. Existing still-only records remain valid.

Access rules declared at publish: everyone admitted reads. `scene/*` is writable at `interact` (the Share menu's Contributor and above). `renders/*` and `workspace/*` are writable at `admin` (Editors and the owner). The owner always writes.

## File store

| File | Type | Notes |
| --- | --- | --- |
| Structure | `<name>.cif.txt` / `.pdb.txt` (text/plain) | Byte-identical copy. `studio.py` reads `<asset id>.txt` downloads directly. |
| Render | `.png` | Full resolution, byte-identical. |
| Session bundle | `<render>.session.json` (application/json) | `{"format": "structure-studio-session/1", "how_to_decode": …, "entries": [{role, filename, bytes, sha256, encoding: "gzip+base64", part, parts, data}]}`. Files above about 15 MiB of encoded data spill into `.session.partNN.json`. |

The store accepts only web types (png, jpeg, gif, webp, svg, mp4, webm, pdf, fonts, csv, md, json, txt), at up to 20 MiB per file. That is why session files travel as JSON. Blender 5 already compresses `.blend` files, so base64 adds about a third. PyMOL sessions shrink about 4×. Images upload in batches (`file_paths`); text and JSON files upload one per call.

## Limits

- 25,000 database documents and 256 KiB per document per artifact. Keep scenes compact and never store file data in documents.
- Each viewer may hold at most 64 live subscriptions; the page uses four.
- Live updates arrive within about a second, falling back to about 30 s polling when the realtime stream drops.
