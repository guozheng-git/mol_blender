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

## Structure Studio animation recipes

Executed 2026-10-26 on the local Apple Silicon host, on
`claude/structure-studio-animation-recipes` branched from
`claude/structure-studio-plugin`. The Claude plugin now includes turntable, rock,
reveal, cutaway, states and scenes recipes, reusable JSON templates, and movie
bundling. The shared local renderer gained camera paths, state coordinates and
cutaway-lid fading; no service/backend or client-specific dependency was added.

- `STRUCTURE_STUDIO_TEST_PYMOL=<local PyMOL Python> python -m pytest -q`:
  **299 passed**, including two real headless PyMOL integration checks. Three
  warnings are the deliberate duplicate-ZIP test and existing Authlib deprecations.
  Checks cover recipe timing, input/template validation, a 360-frame archive,
  state atom identity, saved-scene camera-only recall, preserved enabled objects,
  selections, colors and transforms, bundled files and existing still rendering.
- `node --test tests/structure_studio_viewer.test.cjs`: **9 passed**. This covers
  browser-script syntax and the existing structure/asset helpers, not actual
  browser video playback.
- Built the vendored renderer wheel and both Claude upload ZIPs using
  `python scripts/build_plugin.py`. Installed the wheel into a new environment
  outside the checkout. With Python `-I`, the installed camera-path and package
  identity tests passed (**27 tests**), including installed module-location and
  manifest checks. The extracted plugin lists all six recipes and contains all
  six JSON templates and the co-pymol MIT notice.
- Ran the extracted Claude plugin outside the checkout using its rock template,
  a real public CIF, a workspace scene snapshot, the installed wheel and the
  existing local Blender/PyMOL runtimes. A 128 × 96, 4-sample, 4-frame movie took
  **11.1 s** for the render invocation. PNG, MP4, GIF, frame ZIP, PSE and Blender
  scene were produced. Recording and restoring its bundle verified all six
  bundled roles: PSE, blend, MP4, GIF, frames and reusable animation JSON.
- Real Blender smoke renders used public `1stp.cif` for turntable, rock, reveal
  and cutaway. States/scenes used an explicitly synthetic three-state DNA
  fixture derived from public `1bna.cif`, with two stored camera scenes. These
  fixtures test rendering behavior, not biological motion. All movies are 2 s:
  turntable/states/scenes have 8 frames at 192 × 144 and 4 samples; rock has
  8 frames at 256 × 192 and 8 samples; reveal/cutaway have 12 frames at 256 × 192
  and 8 samples. Render invocations took 11.0 s, 144.4 s, 12.4 s, 21.5 s, 12.5 s
  and 11.9 s for turntable, rock, reveal, cutaway, states and scenes respectively.
- FFprobe confirmed each MP4's dimensions, frame count and 2.000 s duration.
  GIFs decoded to the expected frame counts with infinite looping. Inspected
  four frames from each movie in a contact sheet: full rotation, both rocking
  directions, pocket approach, closed/open cutaway, changed molecular states,
  and saved-scene camera movement are visible. The cutaway still is anchor
  frame 7, after the lid has disappeared. These are low-sample smoke previews,
  not publication-quality renders.

Logs, synthetic fixtures and images are ignored under
`.validation/studio-animation/`; the external package-check directory is recorded
there. Temporary renderer source snapshots were cleaned by the ordinary local
runner. No live PyMOL GUI session was touched. No Claude artifact was published
or modified. No browser connection was available, so actual gallery playback,
Claude Artifact upload/download and client integration remain unverified.
