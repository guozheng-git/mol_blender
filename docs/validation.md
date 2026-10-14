# Local branch validation

Executed on Apple Silicon macOS, 2026-09-28. Package version: 0.3.0.

## Automated checks

- Source suite: **147 passed**, with two FastMCP/Authlib deprecation warnings.
- Built wheel installed into a separate environment: **147 passed**. Tests were
  copied into a temporary directory outside the checkout and run with Python `-I`.
  Module paths confirmed imports from the installed wheel. Two inherited test
  fixtures were updated to locate JSON relative to the imported package instead
  of assuming the current directory was the source checkout.
- All **54 packaged code/assets** matched source bytes; every packaged file digest
  matched `engine-manifest.json`.
- Skill frontmatter/name validation passed; the Claude skill symlink resolves to
  the canonical Codex skill. Internal module imports and documentation links checked.
- `git diff --check` passed.

Principal commands:

```sh
python -m pytest -q
python -m build --wheel
# Install dist/pymol_plugin_bridge-0.3.0-py3-none-any.whl into an isolated environment.
# From a separate temporary directory containing a copy of tests/:
/path/to/installed/python -I -m pytest tests -q
```

## Executed local rendering

The runtime verified Blender **5.0.1**, Molecular Nodes **4.5.10**, Python **3.11**,
NumPy **1.26.4**, Biotite **1.6.0**, and the committed CCD SHA-256. Validation used a
separate `BLENDER_RENDER_HOME` under ignored `.validation/`; existing user runtime
configuration and agent MCP registrations were not changed. A temporary conda-forge
PyMOL 3.1 environment supplied the headless file-export interpreter.

- Rendered the public `examples/1sps_pose.pse` through CLI export and the default
  pastel preset at 256 × 256, 8 samples, with a saved Blender scene.
- Ran `bridge_render` inside a real `pymol2.PyMOL()` instance on that public session.
  Export, configured CLI interpreter, Blender subprocess and `cmd.load_png` all
  completed. Both manifests report `dss_ran=true`, `polymer_backend=mn_cartoon`,
  `ligand_backend=mn_native_sticks` and `ligand_atom_spheres=false`.
- Rendered dried green paint using a public CIF bundle and inspected the PNG.
- Used that generated green image as a skill-workflow smoke-test reference: recorded
  visible observations, generated and validated a full candidate JSON, saved
  `user/reference-smoke`, reloaded it in a new process and rendered it. The inspected
  PNG retained the same visible composition and green material. This is workflow
  validation with a known generated example, not proof of arbitrary image matching.
- Started the **installed wheel** as a real stdio MCP subprocess with Python `-I`.
  Listed all four tools, rendered the public PSE with the generated style, then
  called `read_render`. Decoded MCP image bytes matched the saved PNG exactly.

Example reproduction after runtime configuration:

```sh
blender-render render examples/1sps_pose.pse --width 256 --height 256 --samples 8 \
  --save-blend -o renders/pastel.png
blender-render export examples/1sps_pose.pse -o renders/bundle.json
```

Local smoke artifacts/logs are in ignored `.validation/`: `pastel-03.png`,
`pymol-bridge.png`, `green.png`, `reference-reloaded.png`, `wheel-mcp.png`, the sibling
`.render/` directories, `candidate.json`, `decision.json`, `installed-tests-final.log`
and `stdio-render.log`. They contain local runtime paths and are not committed.

## Limits and observed failures

An initial existing CCD copy failed the pinned checksum. A matching existing copy
was selected; the lock was unchanged. An initial manually assembled default-preset
bundle failed the required DSS check. Repeating with real PyMOL export passed.
These failures were not counted as successful renders.

Previews were intentionally small with low sample counts; they are not publication
quality comparisons. PyMOL was exercised headlessly, not visually in a GUI window.
Actual Claude/Codex client registration, skill discovery and in-chat image display
were not exercised. Stdio protocol and image-byte delivery were tested independently.
No remote renderer, deployment, paid compute, push or publication was used.

## Fresh Mac installation

Executed 2026-09-28 with a new, repo-local `BLENDER_RENDER_HOME=.runtime`.
The managed installer downloaded and checksum-verified the Blender 5.0.1 Apple
Silicon DMG, mounted it, copied a new Blender.app into `.runtime/runtime/`, detached
the disk image, and installed the hash-pinned Python wheels into a new
`.runtime/runtime/site/`. The installed Biotite CCD matched the existing lock
without copying a database from another Blender installation. No installer code
changes or manual runtime repairs were needed.

A separate PyMOL environment was installed with Pixi under `.runtime/pymol/` and
configured as the file-export interpreter. The existing project `.venv` was reused
as the CLI environment. Download/package caches could be reused, but all managed
render-runtime destinations were new. This verifies a fresh runtime installation
on this Apple Silicon host, not every supported OS or a pristine operating system.

```sh
export BLENDER_RENDER_HOME="$PWD/.runtime"
blender-render setup --install-runtime
pixi init .runtime/pymol --channel conda-forge
pixi add --manifest-path .runtime/pymol/pixi.toml python=3.11 pymol-open-source
blender-render setup --pymol-python "$PWD/.runtime/pymol/.pixi/envs/default/bin/python"
blender-render render examples/1sps_pose.pse --width 512 --height 512 --samples 32 \
  --save-blend -o renders/fresh-install.png
```

The render completed on the Apple M4 Metal GPU, and its PNG was visually inspected.
Manifest checks confirmed the Blender executable and the Molecular Nodes, NumPy
and Biotite module paths all came from this checkout's `.runtime/`. Runtime versions
matched the lock; DSS assignment, native cartoons and sphere-free ligand sticks
passed. The PNG and Blender scene both exist. PNG SHA-256:
`fe6b2d171a231f87107a02b4cedd00f10f8d22962c653cc3a03ea799ab761e52`.

The local `.runtime/activate` helper selects this checkout's CLI environment and
configuration when sourced. Logs are `.runtime/install.log`,
`.runtime/pymol-install.log` and `.runtime/render-smoke.log`. Installation files,
configuration, logs and renders remain ignored by Git. The source and runtime
installer are unchanged; only this evidence and the README were updated.


## Local animation port

Executed 2026-09-29 on this Mac, using the repo-local Blender 5.0.1 runtime and
Apple M4 Metal GPU. Selected animation code came from PR #2; the local adapters
and artifact handling were implemented on `codex/local-pymol-presets`.

- Source suite: **198 passed**. Coverage includes all 14 preset/animation
  resolutions, bounds and invalid options, adapter forwarding, missing FFmpeg,
  missing artifacts, overwrite refusal, archive validation and real FFmpeg encoding.
  The three warnings are the deliberate duplicate-ZIP test and two existing
  FastMCP/Authlib deprecations.
- Installed wheel outside the checkout: **198 passed**, with Python `-I` and
  installed module-path/manifest checks. Source snapshots and package manifests
  now ignore macOS Finder's mutable `.DS_Store`; it changed during an early test
  run and incorrectly invalidated an otherwise unchanged source identity.
- CLI: rendered `examples/1sps_pose.pse` as a ±12° rock, 2 seconds at 12 FPS,
  320 × 240, 8 samples, producing PNG, GIF, MP4 and the lossless frame archive.
- Installed-wheel stdio MCP: rendered the same public session as a full 360°
  orbit with the same resolution, duration and FPS. `read_render` returned PNG
  bytes identical to the saved still.
- Both GIFs decode to **24 frames**, total **2,000 ms**, with infinite looping.
  FFprobe confirms 24-frame, 2-second MP4s. Each archive has 24 distinct frame
  hashes, and frame 0000 is byte-identical to the output PNG. Manifests record
  the expected sinusoidal rocking angles and 0–345° spin in 15° increments.
- Visually inspected frames 0, 6, 12 and 18 in both animations. The rock preserves
  the starting view and swings each way; the spin exposes front, side and back
  views. These 8-sample smoke renders are visibly noisy, not publication quality.
- A real headless PyMOL instance exported and rendered a four-frame rock with
  GIF and saved Blender scene through the plugin. With `auto_preview=0`, the
  PyMOL view was unchanged. `blender_movie_export` re-encoded its saved frames;
  CLI `movie-export` also re-encoded the longer rock without invoking Blender.
  Pillow was added to the ignored repo-local Pixi PyMOL environment.

Reproduce the small CLI check after activating the local environment:

```sh
blender-render render examples/1sps_pose.pse --animation rock --duration 2 \
  --fps 12 --rock-angle 12 --gif --width 320 --height 240 --samples 8 \
  -o renders/local-rock.png
blender-render movie-export renders/local-rock.frames.zip -o renders/local-rock-reencoded.gif
```

Ignored artifacts include `renders/local-rock.*`, `renders/local-360.*`,
`renders/pymol-rock.*`, their `.render/` logs/manifests,
`renders/animation-contact-sheet.png` and `.validation/animation-*.log`.
Actual PyMOL GUI movie playback and Claude/Codex client media playback have not
been verified. MCP protocol execution and PNG-byte delivery are verified
separately. No remote rendering or publication was used.


## Mint/sky default preset

Executed 2026-09-29 after selecting `mint_sky_assembly` as the default across the
resolver, CLI, developer preview, PyMOL bridge and stdio MCP. The five original
numbered presets remain in the catalog with unchanged JSON; their README gallery
rows were removed. The existing preset-1-specific backend checks remain intact
when that preset is explicitly selected.

- Source suite and installed-wheel suite outside the checkout: **200 passed** each.
  Adapter tests cover omitted preset arguments; original preset IDs still resolve.
- Rendered the public `examples/1sps_pose.pse` with no `--preset` argument at
  320 × 240 and 32 samples. Its manifest identifies `mint_sky_assembly`, native
  Molecular Nodes cartoons and native ligand sticks. The inspected PNG shows
  mint protein, purple ligand and pale cyan background with the saved input pose.
- Artifacts and logs remain ignored: `renders/mint-default.png`, its `.render/`
  directory and `.validation/mint-default*.log`. Preset JSON files were not edited.

## Structure Studio Claude plugin

Executed 2026-10-14 on an Apple M3 Mac (macOS 26.6) and on claude.ai, using the plugin's own
`studio.py` the way its skill instructs Claude. Public structures only: 9LHJ, 4HHB, 1SPS, 1STP and
AlphaFold model AF-P69905-F1.

Runtime through the `bpy` module:

- `setup` created two uv-managed environments from a clean `STRUCTURE_STUDIO_HOME`, taking 40 s
  with a warm package cache. One holds `pymol-open-source-whl` 3.2.0.2; the other holds `bpy`
  5.0.1 with the pinned runtime requirements and the vendored wheel. A rerun was idempotent.
- Renders go through a small Blender command-line shim, and the renderer's lock checks passed
  (Blender 5.0.1, Molecular Nodes 4.5.10, NumPy 1.26.4, Biotite 1.6.0, CCD digest), using Metal.
- Two renderer fixes were needed for that path. `developer_blender.py` records the `bpy` extension
  module when `bpy.app.binary_path` is empty. `setup` keeps a virtualenv PyMOL interpreter's path
  instead of resolving its symlink to the base Python.
- `setup --background` returned immediately. A `.pse` import started at the same moment waited
  only for PyMOL, and a render waited for Blender and then completed. This was 47 s in total
  with a warm cache.

Workspace artifacts:

- A probe artifact on claude.ai established the following:
  - Mol* 5.12.0 loads in the real artifact sandbox.
  - Database writes from this session redraw the open panel without a reload.
  - The page's camera and selection writes reach Claude.
  - The file store keeps uploads byte-identical. It refuses raw `.pse`, `.blend` and `.zip`;
    the same files stored as compressed base64 text restored with matching SHA-256.
  - The `downloads` capability saved a `.zip`.
- Mol* runs one pair of Emscripten `new Function` wrappers at load. The page serves those two
  shapes with closures, and it drew under a strict local CSP (no `unsafe-eval`,
  `connect-src 'none'`).
- Workspace `UBE2N–UBE2V2 · 9LHJ` was created with Recipe A. A chat request ("hide chain A and
  show the binding site of the chain-B inhibitor") went through the scene command and updated the
  panel live. `mint_sky_assembly` rendered the panel's scene and camera (24.8 s on Metal). Chain A
  went into a disabled `studio_hidden` object, so the render excluded it while the `.pse` kept
  it. The render card moved from "Rendering…" to done with the PNG, `.pse` and `.blend` stored in
  the artifact.
- The streamlined render path (`render --workspace`, one JSON session bundle, `record`) rendered
  `powder_blue_rounded` in 18.9 s. `restore` rebuilt both session files with verified checksums,
  and the restored `.pse` opened in PyMOL with the hidden chain disabled.
- New claude.ai chats with no plugin, link or shared context found the workspace by title:
  - One chat returned the `mint_sky_assembly` PNG and built a poster from it.
  - Another rebuilt the `.pse` and `.blend` from the record. Their checksums matched the original
    files exactly.
- With the plugin uploaded to claude.ai, run by the user:
  - A `.pse` became a workspace with its colors and camera.
  - "Hide chain C" applied live.
  - `dried_paint_green` rendered in claude.ai's Linux sandbox in about 2 minutes on CPU, capped at
    1280 × 1072 and 64 samples.
  - The `.pse` and `.blend` were stored.

  This evidence is the user's report and screenshot. The sandbox logs were not inspected here.
- Found and fixed during these runs:
  - 9LHJ's inhibitor A1EJS is modelled as an in-chain residue, and is now treated as a covalent
    ligand (non-standard, at least 15 heavy atoms).
  - PyMOL-written mmCIF numbers ligands like chain residues, so HETATM residues now also need
    backbone atoms to count as polymer.
  - Plugin-built sessions widen PyMOL's clip slab, because the renderer copies it into Blender's
    camera clip range.
  - The panel now sets an exact camera after Mol*'s first-draw refit, field of view first, and
    matched the session camera numerically.
- The camera conversion between Mol* and PyMOL is column-major, confirmed by ray-tracing a
  three-sphere probe after `turn y, 90`. Both programs use a vertical field of view. At the same
  camera the reference chain filled 57% of the Mol* frame height and 56% of PyMOL's.

`claude plugin validate` passes for the plugin and the marketplace; `python -m pytest` passes 220
tests.

Not yet verified:
- Sharing with another account (Viewer versus Contributor or Editor writes), and Team or
  Enterprise plans.
- Session bundles above 15 MiB in practice (tested only as split unit-test bundles).
- Windows.
