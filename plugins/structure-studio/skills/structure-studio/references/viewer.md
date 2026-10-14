# Offline viewer reference

Workspaces (Recipe A) are the default. `studio.py viewer` instead writes one self-contained HTML file with no database, for a quick look, Claude Code without artifact tools, or a file to send. It uses the same page template in offline mode. It contains the
structure (gzip + base64), the page code, the Mol* 5.12.0 stylesheet and any renders or files. Mol*
itself loads from `cdn.jsdelivr.net`, which artifact pages allow. Nothing else is fetched, so the page
works even where the PDB is blocked.

## Flags

| Flag | Effect |
| --- | --- |
| `--style cartoon\|surface\|atoms\|site` | Initial representation. `site` needs a ligand. |
| `--color chain\|ss\|rainbow\|bfactor\|plddt` | Initial coloring. `plddt` and `bfactor` are the same button, labelled pLDDT for predicted models. |
| `--ligand KEY` | `HEM A142` or just `HEM`; picks the binding-site ligand. |
| `--water` | Show waters (in `site` style, only waters within 5 Å). |
| `--view JSON` | Start from a Copy-view camera. |
| `--camera-from-session FILE` | Start from the pose in a saved `session` JSON result (`molstar_camera`). |
| `--render PATH[=Label\|Description]` | Add a PNG to the Renders tab (repeatable). Large PNGs become JPEG previews when Pillow is available. |
| `--file PATH` | Bundle into the Files tab's `.zip` download (repeatable; about 8 MB zipped at most). |
| `--tab renders\|files` | Tab shown first. |
| `--predicted` / `--experimental` | Override predicted-model detection. |
| `--title`, `--id`, `--source` | Header text. |
| `--standalone` | Add `<!doctype html>`, `<head>` and `<body>` for opening the file directly in a browser. Do not use this for artifacts, which add their own skeleton. |

The page limit is 16 MB. The builder refuses larger pages and lists what it skipped in `notes`.

## Behavior

- Scenes are MolViewSpec documents built in the page and loaded as in-memory MVSX archives. Style changes keep the camera, while the binding-site view refocuses.
- Ligands, ions, waters and binding sites are computed in the page from the embedded file, so files opened with **Open file…** or drag-and-drop get the same controls. BinaryCIF opens without ligand tools.
- Chain colors match the legend chips. Ligand carbons are amber and other atoms use element colors.
- **Snapshot** and the Renders tab's **Save image** buttons, and the Files tab's `.zip`, use the artifact `downloads` capability. Publish with `capabilities: {"downloads": true}` when renders or files are embedded. Without it the buttons explain that saving works on claude.ai.
- **Copy view** shows JSON like the following, which `studio.py render --view` and `session --view` accept:

```json
{"structure_studio_view": {"format": "molstar-camera", "structure": "4hhb.cif",
  "position": [19.9, 16.2, 65.1], "target": [19.9, 16.2, 24.8], "up": [0, 1, 0],
  "fov_deg": 45, "mode": "perspective"}}
```

The conversion to PyMOL's view matrix lives in `scripts/camera.py`. Blender then preserves that PyMOL pose.

## Publishing checklist

1. Run `viewer` and read `bytes`, `notes` and `needs_downloads_capability`.
2. Publish the `viewer` file path, with `icon: "molecule"` on the first publish and `capabilities` if needed.
3. On later changes, rebuild to the same path and publish again (omit `icon`) so the link stays the same.
