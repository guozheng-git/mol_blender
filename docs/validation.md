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
  six JSON templates.
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

### PR #5 review corrections

Executed 2026-10-26 after fixing object-pinned state export and half-turn camera
interpolation, removing unused recipe selection, standardizing lid opacity, and
separating animation preparation from render dispatch.

- Full suite with the local PyMOL integration checks: **304 passed** in 6.67 s,
  with the same three warnings above. Regression coverage includes exact and
  near-180-degree rotations about mixed-sign axes, sessions pinned to states 2
  and 3, unchanged disabled objects, and camera-only preservation of object state.
- Repackaged both Claude upload ZIPs and ran the extracted plugin outside the
  checkout. Renderer sources and the vendored wheel did not change. Real PyMOL
  preparation verified default 640 × 480 / 32 samples, preservation of preset
  samples with `--full-quality`, and explicit size/sample overrides.
- Real Blender rendered three 160 × 120, 2 s movies from the extracted plugin:
  states (8 frames, 32 samples, **12.8 s**), scenes (8 frames, 4 samples,
  **10.0 s**) and cutaway (12 frames, 4 samples, **17.6 s**). The states/scenes
  fixture is synthetic DNA derived from public `1bna.cif`, pinned to state 2,
  with two identical saved cameras at the previously failing half-turn. Cutaway
  uses public `1stp.cif`.
- FFprobe verified frame counts, dimensions and 2 s duration. Inspected the
  contact sheet: supplied states change, identical saved scenes hold steady,
  and the lid fades from opacity 1 through 0.5 to 0 at anchor frame 7. Scene-tour
  frames differ from the still by at most one channel level, with mean absolute
  pixel difference below 0.001 on the 0–255 scale.

Evidence remains ignored under `.validation/studio-animation-review/`. These
checks do not establish Claude browser playback or Artifact integration.

### Cutaway helper placement and plugin notices

Executed 2026-10-26 after moving lid construction and material fading into
`animation_blender.py`. Construction and frame updates now share one property
constant. The main renderer delegates lid creation. The separate co-pymol license
file and its references were removed at the author's request; source copyright
and MIT identifiers remain.

- Full suite with real PyMOL checks: **304 passed** in 4.43 s, with the same
  three warnings. Rebuilt the renderer wheel and both Claude upload ZIPs.
- Installed the rebuilt wheel into a fresh environment outside the checkout:
  **27 camera-path and package-identity tests passed** in 2.03 s with Python `-I`.
- The extracted plugin, using that installed wheel, rendered a 160 × 120,
  4-sample cutaway from public `1stp.cif`: 12 frames over 2 s, **18.0 s** render
  time. Inspected the closed, half-faded and fully open frames. All 12 frames
  differ from the pre-refactor render by at most one channel level on the
  0–255 scale. Evidence is ignored under `.validation/studio-lid-module/`.

# Local `main_merged` candidate, 2026-11-01

Merge order, exact PR heads and selective integration decisions are recorded in
[merge-audit.md](merge-audit.md). This is a local prerelease candidate, not a
published release or evidence of working live hosted clients.

Before the version freeze, the combined source passed **376 tests** with real
PyMOL and Chrome enabled (the generated-wheel comparison was deselected until
the frozen build). The imported browser fixture originally kept Playwright's
event loop alive across unrelated MCP tests; restricting its lifetime to each
browser module fixed the combined-suite failure.

The first frozen-source run passed 377 tests. An outside-checkout run exposed
two source assumptions: the catalog builder read a checkout path instead of the
imported package's data, and a test compared `/var` with its `/private/var`
canonical path. Both were corrected. Setup also rejects existing environments
whose Python patch version differs from the pin; the Blender reopen acceptance
script works with both a native executable and Studio's bpy shim.

Real local acceptance used public 1STP and a locally generated trusted PyMOL
session, Blender 5.0.1, Molecular Nodes 4.5.10 and NumPy 1.26.4. The controller
and PyMOL used isolated uv environments; Blender and its locked dependencies
were reused read-only from an existing native runtime. This is not a fresh
Blender/bpy installation test.

- Still plus all six recipes rendered at 192 × 144, 4 samples and 4 frames.
  MP4 counts/duration and GIF frames/looping passed, and every animation had
  changing lossless-frame hashes. The 24-frame contact sheet was inspected.
- PSE, Blender scene, MP4, GIF, frame archive and recipe bundles restored with
  exact hashes for all six recipes. A restored PSE and Blender scene reopened.
  A workspace was rebuilt from the restored PSE.
- The generated MP4 played in real headless Chrome with advancing playback time.
  Re-encoding a saved archive passed without launching Blender.
- A real Mac PyMOL Qt GUI played the cached Blender frames, preserved objects
  and camera, exported native cached PNGs with exact RGB pixels, and completed
  native MP4 plus later plugin MP4/GIF exports without a render. The GUI used a
  separate uv environment with PySide6 6.8.1; it closed after the acceptance script.
  Distribution pin: `pymol-open-source-whl==3.1.0.4`; that wheel reports runtime
  version `3.2.0a`. Preserve both identities when comparing Linux results.
- Viewer simulations exercise save/reopen in a new browser context after the
  original URL is revoked. They do not establish live ChatGPT/Claude persistence
  or automatic next-step model image access.

Reproduce on configured local runtimes (output directories must be new):

```sh
uv sync --locked --extra dev --extra mcp --extra hosted
python scripts/build_release.py --output dist/release
python scripts/verify_release.py dist/release
STRUCTURE_STUDIO_TEST_PYMOL=/path/to/pymol/python \
STRUCTURE_STUDIO_TEST_WHEEL=/path/to/built.whl \
python -m pytest -q tests --browser /path/to/Chrome
python scripts/validate_local_acceptance.py \
  --skill dist/release/plugin/structure-studio/skills/structure-studio \
  --config /path/to/config.json --fixture examples/1stp.cif \
  --pymol /path/to/pymol/python --output .validation/acceptance \
  --browser /path/to/Chrome
```

Freeze with version `0.4.0rc1` (plugin `0.4.0-rc.1`) and rebuild from that commit.
`dist/release/release.json` records the exact revision, tree, engine identity and
wheel/ZIP hashes. Final source and installed-wheel results are saved beside the
candidate as `validation.json` after the freeze, avoiding another source change.
The installed check must run outside the checkout, with the package source
removed from its copied test fixtures and installed module paths asserted.

Remaining release gates: Linux Apptainer build/CUDA execution; real hosted
attachment, save/reopen and automatic image-context acceptance; final-quality
visual review. The low-sample tests verify execution and preservation, not final
scientific figure quality. Nothing was pushed, deployed or published.

# Optional shared cluster candidate, 2026-11-01

Version **0.4.0rc2** adds an optional authenticated gateway, durable per-user job
records, Slurm/Apptainer workers, and explicit PyMOL/CLI cluster submission. The
renderer stays shared with local rendering. No service was deployed or published.

- Final source suite with real PyMOL and Chrome enabled: **415 passed** in 28.48 s.
- The wheel installed in a separate uv environment outside the checkout:
  **415 passed** in 28.21 s. Its copied test tree contained no package source;
  installed module locations and the complete engine manifest were asserted.
- Four warnings in each suite: one deliberate duplicate-ZIP test, two existing
  FastMCP/Authlib deprecations and the pinned Starlette test client's HTTPX
  deprecation. There were no skipped tests in these runs.
- Cluster checks cover authentication/revocation, ownership, quotas, bounded
  data-only input, rejection of path-bearing export IDs, safe artifact paths,
  cancellation, expiry, request idempotency, lost-response recovery, scheduler
  uncertainty/reconciliation, polling backoff, checksum failures, TLS-origin
  policy and local-render independence. HTTP client checks use an actual local
  TCP listener; scheduler command behavior is simulated.
- Resource isolation checks confirm CPU/thread limits and CUDA visibility survive
  the isolated preview runner. Only Apptainer's fixed driver-library path can pass
  through; unrelated library paths and service credentials stay excluded. This is
  a code-level check, not evidence of an actual CUDA allocation.

`scripts/validate_cluster_local.py` exercised the real PyMOL bridge through a
temporary authenticated loopback gateway, a **local scheduler substitute**, the
shared worker and actual Blender. It ran once against the final source and once
against the installed wheel, using public 1STP, the existing pinned native Blender
runtime, isolated uv controller/PyMOL environments and CPU rendering:

- One still and one four-frame rocking movie per run, 192 × 144 and four samples.
- Private local receipts recovered the original submitted jobs; no duplicate
  render was submitted. Every downloaded artifact passed size and SHA-256 checks.
- Both saved `.blend` scenes reopened with their camera and objects present.
- The downloaded frame archive passed bounded unpacking and four-frame checks;
  FFmpeg decoded the entire MP4. This does not add a new interactive GUI playback
  claim to the earlier PyMOL GUI acceptance record.
- Source and installed-wheel still/anchor images had identical RGB pixels and
  matching resolved styles/cameras. The source still was visually inspected.
  PNG file hashes differ because of metadata; per-download integrity was verified.
- Runtime: **22.69 s source**, **22.31 s installed wheel**, including PyMOL export,
  submission, polling, both renders, downloads and scene reopen checks.
- Temporary listeners were closed and validation credentials removed.

Reproduce with an already configured local runtime and a new output directory:

```sh
uv sync --locked --extra dev --extra mcp --extra cluster
STRUCTURE_STUDIO_TEST_PYMOL=/path/to/pymol/python \
STRUCTURE_STUDIO_TEST_WHEEL=/path/to/built.whl \
python -m pytest tests --browser=/path/to/Chrome -q
python scripts/validate_cluster_local.py \
  --config /path/to/config.json --pymol /path/to/pymol/python \
  --fixture examples/1stp.cif --output .validation/cluster-acceptance
```

The tested engine identity is
`a2cbbc13912aece3f71af6dab1c0ad2e82b99e0d8d5935e6d16d5300bd330ff4`.
Final wheel/plugin ZIPs are rebuilt from the committed source using
`scripts/build_release.py`; their revision and hashes are recorded in
`release.json`. `scripts/verify_release.py` compares every packaged source byte
against that revision. Execution evidence is stored with the local artifacts in
`validation.json`, without putting local paths or molecular data into Git.

**Remaining cluster gate:** the authorized Linux machine reported online through
Tailscale, but SSH connections through LAN, Tailscale IPv4/IPv6 and the Tailscale
SSH wrapper timed out. No remote files were changed, Slurm jobs submitted, SIF
built, Linux runtime reused or CUDA rendering verified there. The reusable uv
container definition and Slurm launch arguments still need real target-host
validation before opening the service to other users. The source/wheel tests and
Mac renders do not satisfy that gate. See [shared cluster setup](shared-cluster.md).

