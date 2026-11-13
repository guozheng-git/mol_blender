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

**Cluster gate at this checkpoint:** the authorized Linux machine reported online through
Tailscale, but SSH connections through LAN, Tailscale IPv4/IPv6 and the Tailscale
SSH wrapper timed out. No remote files were changed, Slurm jobs submitted, SIF
built, Linux runtime reused or CUDA rendering verified there. The reusable uv
container definition and Slurm launch arguments still need real target-host
validation before opening the service to other users. The source/wheel tests and
Mac renders do not satisfy that gate. See [shared cluster setup](shared-cluster.md).

# Real Slurm and Apptainer acceptance, 2026-11-01

SSH access was restored and the Linux cluster gate above was completed. Version
**0.4.0rc3** contains the fixes discovered during real execution. All work remains
on the local branch; nothing was pushed or published.

The checks used public **1STP**, four CPU cores, 8 GiB RAM, and zero or one GPU per
job. Each acceptance run submitted a 192 × 144 still, a four-frame rocking movie
at four samples, and a cancellation probe through the real PyMOL bridge,
authenticated loopback HTTP gateway, Slurm, Apptainer and shared renderer.
No scheduler substitute was used in these runs.

| Gateway package | Worker runtime | Container base | Result and elapsed time |
| --- | --- | --- | --- |
| Source | Existing Linux uv runtime, CPU | Debian 12 | Passed, 44.16 s |
| Source | Image-built uv runtime, CPU | Debian 13 | Passed, 37.25 s |
| Installed wheel outside checkout | Image-built uv runtime, CUDA | Debian 13 | Passed, 66.57 s |
| Source | Existing Linux uv runtime, CUDA | Debian 13 | Passed, 83.90 s |

These times include queueing, status polling, downloads and verification; they
are not renderer benchmarks. Both CUDA cases reported exactly one **NVIDIA L40S**
in the render manifest. Slurm accounting recorded one allocated GPU and successful
exit codes for the still and animation jobs. CPU jobs reported CPU execution.

Every successful run verified artifact sizes and SHA-256 hashes, reopened both
saved Blender scenes, decoded the MP4, checked changing animation frames and a
four-frame looping GIF, and restored a bundle containing the generated trusted
PyMOL session, Blender scene, movie, GIF and frame archive. The restored PSE and
Blender scene reopened. Actual CPU and CUDA movies also played in Chrome with
advancing playback time; CPU and CUDA stills were visually inspected. These are
small execution checks, not final-quality figure review or new interactive PyMOL
GUI acceptance claims.

Authentication, per-user ownership, token revocation, original-request recovery,
same-request idempotency, conflicting retries and result deletion passed against
the live gateway. The CUDA image run observed the cancellation probe in Slurm's
running state, cancelled it, and verified terminal cancellation and cleanup.
Completed test spools retained only ownership/idempotency tombstones. Temporary
gateways and the build-download proxy were closed, credentials removed, temporary
build directories deleted, and the Slurm queue was empty afterward.

The reusable Linux runtime stayed at its original absolute path and was bound
read-only into Apptainer. Its environment/configuration fingerprints and installed
engine manifest were unchanged by the jobs. No Mac venv was copied, and no job
ran an installer or dependency resolver. The image independently built the same
uv-managed Python **3.11.15**, Blender **5.0.1**, Molecular Nodes **4.5.10**, NumPy
**1.26.4** and Biotite **1.6.0** environment. A direct local Linux CLI render passed
in **7.42 s**. The final Mac source also passed real still/movie rendering and
scene reopen checks in **26.02 s**, using its existing native Blender runtime.

Real execution required these corrections:

- The build's renderer probe needs `BLENDER_RENDER_HOME` set in `%post`; runtime
  `%environment` settings do not supply it there.
- Apptainer needs a digest-only OCI reference instead of a tag plus digest.
- Installed rendering must tolerate an absent Git executable while retaining the
  complete byte-level source inventory.
- The target scheduler accepts GPU GRES requests (`--gres=gpu:1`). Rejected
  submissions now retain bounded diagnostics in private job files.
- Worker scratch files must live under the private job directory; the contained
  `/tmp` was too small for the frozen chemical-component database.
- The NVIDIA libraries supplied by the compute nodes require a newer glibc than
  the original Debian 12 image. The final pinned Debian 13 base supplies glibc
  2.41 and passed both CUDA modes. Other clusters still require their own GPU
  compatibility check.

Final regression checks, all with real PyMOL and Chrome enabled:

- Mac source: **418 passed**, no skips, four warnings, **24.63 s**.
- Linux source: **418 passed**, no skips, four warnings, **20.54 s**.
- Isolated Linux installed wheel outside the checkout: **418 passed**, no skips,
  four warnings, **19.83 s**. The test tree contained no package source.

The warnings are the existing duplicate-ZIP regression case and dependency
deprecations. `cluster doctor` is also exercised with the built-in CPU/CUDA runtime
and the existing Linux runtime. Reproduce the hardware checks with
`scripts/validate_cluster_slurm.py`; add `--cancel-running` for the running-job
cancellation check. See [shared cluster setup](shared-cluster.md).

Validated renderer identity:
`8ba17ac9b491c09594fd22acef5277d2d159dd746d971569fb60f88d59ee016b`.
The final Debian 13 SIF was built using the release artifacts from `fd87c16` and
has SHA-256
`ace21645e71c265c12a86f0870b56bbd05f36f3401dfd678d973292a6ff8688c`.
The final wheel and plugin ZIPs are rebuilt from the clean committed source;
`release.json` records that exact revision and all hashes. `verify_release.py`
checks every packaged source byte. The final wheel payload is compared with the
wheel used for the successful cluster tests, and detailed execution evidence is
stored alongside the frozen artifacts in `validation.json` without committing
machine paths or molecular outputs.

That validation used temporary gateways. The persistent deployment is recorded
below. The separate live hosted-client attachment/save/reopen/image-context
acceptance gates noted above remain separate work.

## Persistent shared cluster service — 0.4.0rc4 (2026-11-01)

At the user's request, a persistent gateway was deployed on the authorized Linux
desktop's cluster interface using free port **8772**. The user explicitly selected
**no authentication**: all callers share a queue and can list, fetch, cancel and
delete its jobs. Token authentication remains the default for other deployments;
open mode requires `authentication: none`. HTTPS certificate validation remains
enabled, using a private CA supplied by the shared client setup script.

The gateway is an enabled systemd user service with `Restart=on-failure` and
`Linger=yes`. A forced process kill during the animation test recovered
automatically in **7.02 seconds**, with a new gateway PID and the **same Slurm job**.
The gateway remained active after the test clients and SSH commands exited.
Boot startup is configured; the desktop was **not rebooted** for this test.

Actual runtime checks:

- Mac source suite: **425 passed**, four existing warnings, **24.05 s**. The tested
  and deployed wheel contents are byte-identical; ZIP timestamps differ between
  the intermediate and frozen builds.
- Installed Linux wheel outside the checkout: **45 cluster tests passed**, one
  dependency deprecation warning, **3.58 s**. Both token and open modes exercise
  real HTTP connections; a scheduler substitute in these tests is complemented
  by the hardware checks below.
- A real PyMOL process inside a separate CPU Slurm allocation used the shared
  controller and verified HTTPS endpoint without credentials. Its still and
  four-frame rock animation ran in separate real Slurm/Apptainer allocations on
  **NVIDIA H200 NVL / CUDA**. The complete endpoint acceptance took **562.59 s**,
  including queue time, restart recovery, downloads and inspections. Slurm
  recorded **3:44** for the still and **3:37** for the animation allocation.
- Downloads passed size/hash checks. Both Blender scenes reopened, the movie
  decoded, GIF/frame counts matched, and animation frames differed. Chrome played
  the downloaded 192×144 MP4 with advancing time and `paused=false`.
- A generated, trusted PyMOL session and the Blender/movie/frame artifacts were
  bundled, restored with matching hashes, and reopened. No executable session
  was uploaded to the gateway.
- Two credential-free client profiles saw the same job. Exact retries reused
  one Slurm allocation, conflicting retries were rejected, and a separate queued
  allocation was cancelled through the API. All three test jobs were deleted;
  only their small idempotency records remain in the service spool.
- Direct local rendering on the Mac still completed on Apple Metal, and its saved
  Blender scene reopened. Shared client setup preserved existing local runtime
  paths while recording its controller interpreter. The Mac client also reached
  the live gateway using the configured private CA.

The deployment uses one GPU, four CPUs and 8 GiB per render, at most four active
jobs in the shared queue, a one-hour render deadline, 24-hour result retention,
and a 20 GiB spool admission limit. Jobs use the configured service Unix identity
and Slurm account. Shared client files are not writable by other users; TLS keys
and the spool remain private. The open API intentionally exposes shared job
results to callers who can reach the endpoint.

The **frozen application revision is `67ea217`**. Its wheel and plugin ZIPs passed
`scripts/verify_release.py`. The offline image update verified identical runtime
contracts and render/PyMOL requirements, then replaced only the application wheel
using uv. Existing Linux environments stayed at their original absolute paths;
no Mac environment was copied and jobs run no installers. The gateway and image
both reported renderer identity
`ffa37d1043dd73118c60004b04b8583e3bfaedd6bd43d646a959f0fc4b91f3d2`.
The deployed SIF has SHA-256
`d2705cd5fd6a7037b967091522dd99f43999ff174b30fcf3bece2bf0f1be668d`.
Machine-specific connection details and execution evidence are retained locally,
outside version control. All source/documentation commits remain local; no push
was performed.

## Embedded PyMOL controller handoff (2026-11-01)

The live Mac PyMOL 2.5.4 application exposed an integration gap in the earlier
headless checks: its Conda launcher sets `PYTHONEXECUTABLE`. Inheriting that value
made the explicitly selected uv Python lose its virtual-environment package
paths, producing a platform-library warning followed by a missing `jsonschema`
error. The dependency was already installed in the correct controller. The
failure happened before writing a submission receipt or creating a Slurm job.

Commit `1c2c6ee` gives both local and cluster PyMOL bridges the same child-process
environment helper. It removes `PYTHONEXECUTABLE`, `PYTHONHOME` and
`__PYVENV_LAUNCHER__`, selects the current package explicitly, and preserves the
running PyMOL's own environment, client configuration and GPU visibility.

Executed checks:

- Reproduced the reported import failure using the actual launcher setting.
- **68 relevant source tests passed** in **4.02 s**, with three dependency
  deprecation warnings. New regressions launch real controller processes under
  foreign Python settings and submit over real HTTP in token and open modes;
  a scheduler substitute keeps those automated tests independent of Slurm.
- Built a local wheel/plugin candidate from `1c2c6ee`, verified its source bytes,
  and ran **68 tests against the installed wheel outside the checkout** in
  **6.21 s**. The package-location assertion resolves macOS's equivalent
  `/var` and `/private/var` paths.
- Reloaded the repaired bridge in the user's existing PyMOL GUI and verified
  that its child imported `jsonschema` from the uv controller environment.
- Retried the user's bundled thioredoxin example once, at **384×288 / 8 samples**.
  The real Slurm/Apptainer job completed on **NVIDIA L40S / CUDA** in **16.98 s**
  from submission to worker completion. Fetching through the same GUI verified
  the artifact hash, saved the PNG and visibly displayed it in PyMOL.
- A separate direct local render completed in **11.26 s** while the parent had
  the same problematic launcher setting, exercising the repaired local bridge.

The repaired client was validated against the frozen persistent gateway/image
over protocol 1. GUI observations and machine-specific evidence are retained
outside version control. The fix and validation commits remain local.

## Optional warm local worker (2026-11-01)

Runtime source is frozen at `19c3fed` after the shared-runtime refactor
(`1f7ddd4`), optional worker integration (`ad4070d`), and scratch/socket lifecycle
fix (`19c3fed`). The local wheel and both plugin ZIP candidates were rebuilt from
that exact clean revision, retaining version `0.4.0rc4` for local validation.
Their renderer identity is
`93788420aaa6bf41e82253c58a31188028178091d318a966b1f97ba70212e79c`.
These are local candidates; no release or push was performed.

`scripts/validate_warm_local.py` exercises a real PyMOL export/bridge, four warm
requests, one ordinary fresh render, movie encoding, saved-scene reopening,
checksummed session-bundle restoration, explicit stop and idle shutdown. Only
public structure fixtures and sessions generated by the runner are used. Generated
PSE files are explicitly trusted inputs, not arbitrary uploaded programs.

Executed runtime checks:

| Package/runtime | Result |
| --- | --- |
| Initial source acceptance with native Blender / local GPU | Passed; four requests used the same Blender process, while the fresh render used another process |
| Installed wheel outside the checkout / native Blender / local GPU | Passed, including real PyMOL bridge handoff under a foreign `PYTHONEXECUTABLE` |
| Installed wheel / Linux GPU / Apptainer / reused Linux Studio uv runtime | Passed; four requests reused one Blender process and a fresh render used another |

The initial source acceptance preceded the scratch/socket follow-up. The final
installed-wheel native and Linux checks, Slurm checks, full source test suite and
normal-profile warm render used the frozen revision above.

The native installed-package run took **9.53 s** for the first warm request,
**3.58 s** for the second, and **8.81 s** for the fresh render. The Linux container
run took **11.79 s**, **5.22 s**, and **11.62 s**, respectively. These are small
192×144 / 4-sample acceptance renders, including controller/export overhead;
they are not general performance benchmarks. Both sequences changed styles,
rendered a four-frame rocking animation, saved Blender scenes and restored a
session bundle. Warm-versus-fresh default-style pixel RMS differences were below
**0.011** on an 8-bit channel scale. Native and Linux stills were visually
inspected, including the change to green paint.

MP4 decoding and four distinct saved animation frames passed. Native source and
installed-package movies also played in Chrome with advancing playback time and
`paused=false`. Restored PSE sessions were reopened in real PyMOL and restored
Blender scenes were reopened in Blender. The current desktop was locked, so the
new warm option was not retested in its existing interactive PyMOL window; the
real headless PyMOL bridge and saved output checks above did run.

The Linux check reused the existing pinned environment at its original absolute
path through a read-only Apptainer bind, replacing only the application wheel in
that private validation runtime. No Mac environment was copied and no packages
were installed during rendering. Large source/CCD captures stayed on explicitly
selected scratch storage; a separate short private socket path was removed on
shutdown. The validated Debian 13 base SIF remains
`d2705cd5fd6a7037b967091522dd99f43999ff174b30fcf3bece2bf0f1be668d`.

The final source suite passed **445 tests in 39.00 s**. The wheel installed into
an isolated uv environment outside the checkout passed **445 tests in 43.44 s**.
Both runs enabled the browser, real PyMOL and exact-wheel checks; each reported
four existing dependency/fixture warnings and no skips. The warm-specific
regressions use real supervisor and IPC processes with a substitute only at the
Blender boundary. They cover concurrent startup, busy rejection, timeout/crash
retirement without replay, forced stop, source/runtime mismatch, private socket
permissions, bounded messages, idle expiry and long scratch paths. The real GPU
checks above establish rendering behavior separately.

Real Slurm/Apptainer acceptance from that same installed wheel also passed:

- Still rendering and a four-frame rocking animation ran as **two distinct Slurm
  jobs**. All frames of the animation stayed in its single allocation. Both
  requested and used CUDA, and their saved Blender scenes reopened successfully.
- The test's existing allocations were shortened to ten minutes. The queued
  animation was moved from the permitted backfill partition to the permitted
  regular GPU partition under the same account and job ID. No replacement render
  was submitted. These changes affected only the private acceptance jobs.
- A third, separate cancellation probe was cancelled successfully. Verified
  downloads, request deduplication, ownership/revocation, artifact cleanup and
  closure of the temporary gateway all passed. The complete acceptance took
  **824.15 s**, including queue time. No acceptance jobs remain active.

The downloaded Linux warm-worker and Slurm MP4s also played in Chrome with
advancing playback time and `paused=false`, after verifying the Slurm download
hashes. The Slurm still was visually inspected, and its restored session bundle
and Blender scene checks passed.

The persistent open cluster gateway remains active with its original frozen
image and application. Its HTTPS information endpoint was checked from the
cluster host. The Mac could not resolve the cluster's internal DNS name during
this final check, so current Mac VPN reachability is not claimed; that is separate
from the successful cluster-side tests. Only missing runtime paths were added to
the normal local controller configuration, preserving existing Python and cluster
profiles. A real warm render through that normal profile passed, and the test
worker was stopped afterward. Machine-specific paths, job IDs, logs and artifacts
remain outside version control.

## Blender editing and preset capture (2026-11-02)

Implemented local scene opening and preset extraction in `b507c9e`, followed by
the authored-camera compatibility diagnostic fix in `888463b`. The wheel and both
plugin ZIP candidates were built from the exact clean revision
`888463bd0dcff1cefe7a7636292b39efbd1dc1ec`, retaining `0.4.0rc4` for local validation.
Their engine identity is
`264eebd09866695b55608c3718e812468a602cc822d542452b409cb2a547cf36`.
The later validation documentation commit does not change packaged renderer code.

- Full source suite: **462 passed in 49.83 s**.
- Wheel installed into a separate uv environment outside the checkout:
  **462 passed in 48.71 s**, with Python `-I` and installed module-location and
  exact wheel/source inventory checks. Both suites enabled the real PyMOL and
  browser tests, reported four existing dependency/fixture warnings, and had
  no skips. Cluster, fresh-local and warm-worker regression checks passed.
- Real source and installed-package checks used the public `1sps` example,
  pinned native Blender 5.0.1, and the configured local GPU. The final installed
  acceptance ran `scripts/validate_scene_edit.py` from outside the checkout.
  Unedited saved scenes imported with no changes or unsupported settings.
- Edited the default material's pigment and roughness, background, light power,
  size and transform, camera pose/shift/scale, and exposure. Saved and reopened
  the scene, extracted an authored-camera preset, and rendered it again through
  the shared renderer. Node renaming and rearrangement did not create false
  shader edits. Direct edited-scene versus preset-replay pixel RMS was below
  **0.079** per 8-bit channel.
- Repeated the workflow for paint texture distance, tint, roughness range and
  specular response. Pixel RMS was below **0.081** per channel. Paint now shares
  its shader across generated chain variants while retaining atom colors;
  the original paint render before and after this change was pixel-identical.
- Direct saved-scene comparison renders used CPU; preset replays used the local
  GPU. Both were 192×144 at 8 samples. These small acceptance images were visually
  inspected; they are not publication-quality previews. The two replay requests
  reused the same warm Blender process, which was stopped afterward.
- The real PyMOL bridge imported a saved `.blend` through its configured
  controller under a foreign `PYTHONEXECUTABLE`, then rendered the newly named
  preset. The default camera policy reported ignored camera edits. A scene with
  an added object was refused without creating a preset. Explicit partial-import
  and preset-revision behavior are covered separately by automated tests.
- A generated text block marked for automatic execution did not run when the
  edited scene was inspected. Import left the input `.blend` bytes unchanged.
  Opening and extraction use disabled embedded-script execution.
- A separate final-wheel check captured a perspective camera with numeric depth
  of field. Its direct-versus-preset pixel RMS was below **0.068** per channel.
  The preserve-input policy correctly refused to discard focus. A four-frame
  rock from the captured preset produced four distinct frames, a four-frame GIF
  and a decodable MP4. Its checksummed Blender bundle was restored and reopened;
  preset metadata remained usable and reported the intentionally omitted movie
  recipe. This check decoded the movie; it did not add a new interactive playback
  verification.

`--open-blender` also completed a real render, saved the scene without a separate
`--save-blend` flag, and launched the configured desktop Blender process on that
file. The process remained alive after launch. The Mac was initially locked, so
that first check could not inspect the editor window. The edit/save/reopen checks
above used Blender's real Python API. Only the test's own editor process was
closed during that cleanup.

After the desktop was unlocked on 2026-11-02, repeated `render --open-blender`
with `mint_sky_assembly` and the public `1sps` example at 256×192, 8 samples.
Visually confirmed the saved scene's window, protein geometry, camera and lights.
Through Blender's normal UI controls, changed the Key light's Power from about
6.872 to 12, used **Save As** to create a new `.blend`, and verified its new window
title and saved power value. Imported that file with `dev from-blend` into a
private validation catalog: exactly `lights.0` changed, with no unsupported or
ignored edits. A fresh local render using the named preset recreated Power 12
exactly. Independent background reopening confirmed both saved files' values.
The GUI-edited scene rendered on CPU versus the preset replay on the local GPU
had pixel RMS below **0.051** per 8-bit channel; both PNGs were visually inspected.
The starting render and both brighter edited outputs retained the same protein pose.
This closes the desktop open/edit/Save As validation gap. The edited test scene
was left open for inspection; its artifacts and `evidence.json` are retained in
ignored `.validation/blender-edit/gui-check/`.

Local candidates and evidence remain in ignored `dist/blender-edit-final/` and
`.validation/blender-edit/`, including `validation.json`, complete logs, edited
scenes, extracted presets/reports, comparison PNGs, and restored movie artifacts.
Temporary installed environments and worker source captures were cleaned up.
No installer, remote server update, new Slurm allocation, release publication or
push was performed for this feature.


## Optional local chat editing bridge (2026-11-02)

Added the desktop bridge in `220c8f0` and CLI/PyMOL/stdio MCP integration in
`5d89b79`. Real checks identified and fixed Blender's unsaved-file indicator
(`bfaeeb9`) and repeated undo across an intervening manual edit (`5e91172`).
The final wheel and both plugin ZIP candidates were built from clean revision
`5e91172a9b6a298a3da35c5c0f847f784d19c310`, retaining `0.4.0rc4` for local validation.
Their engine identity is
`96d4cf9c31e30ca0ee955e0c31266cc5dbc01870bc6afccd0900cb1558b57511`.
`verify_release.py` verified artifact hashes and every packaged source byte against
that revision. The following validation/harness documentation commit does not
change packaged renderer code.

- Final source suite: **487 passed in 54.28 s**. Final wheel installed into a
  separate uv environment outside the checkout: **487 passed in 41.64 s**.
  Both enabled real PyMOL and browser tests, had no skips and reported four
  existing dependency/fixture warnings. Installed checks used Python `-I`, module
  location assertions and exact wheel/source inventory checks. One earlier
  installed run stalled in its isolated headless browser; that test process and
  browser were stopped, and the complete rerun passed.
- Initial real acceptance ran from source. Final acceptance ran
  `scripts/validate_editor.py` through a real stdio MCP subprocess using the exact
  installed wheel, the public trusted `1sps` example and native Blender 5.0.1.
  It exercised fresh rendering, desktop opening, inspection, grouped material/
  background/light edits, stale-revision rejection, atomic invalid-edit rejection,
  undo, unsaved preset capture, scene saving, overwrite refusal and independent
  saved-scene reopening. No unsupported preset settings were reported.
- The final installed run rendered a **256×192, 8-sample CPU preview in 2.54 s**
  and a **local GPU final still in 4.71 s**, using separate captured-scene
  processes. Native MCP image bytes matched the saved PNGs exactly. Preview
  snapshots preserved the editor's filename, revision and unsaved state, and
  temporary render-source captures were removed afterward. These small images
  are acceptance checks, not publication-quality renders.
- Replayed the captured preset through the existing `render_structure` MCP tool
  with the warm local worker. The edited-scene final versus preset replay had
  pixel RMS below **0.010 per 8-bit channel**. The warm worker was stopped after
  the check. Visually inspected the original mint render, orange final and later
  GUI-edit preview: the protein pose and ligand were retained.
- In the real Blender window, confirmed chat changed Key light Power to **9**
  and the filename acquired its unsaved marker. Changed Power manually to **11**
  through Blender's normal controls. MCP rejected the stale revision, then made
  a fresh chat edit to **12**. Chat undo restored **11**; a second undo refused to
  cross the manual edit. MCP saved a new preset and `gui-chat.blend`, then returned
  a native PNG preview. The UI showed the new filename without an unsaved marker
  and retained Power **11**.
- An external attachment script connected to an already-open scene with an
  unsaved light edit. Real socket checks rejected an invalid token and recovered
  a duplicate operation without replaying its mutation. The 30-second idle
  shutdown removed the registry/socket/source capture while leaving the desktop
  alive. Explicit disconnect was also checked for capture removal before closing
  only the test's own saved desktop.
- The real embedded PyMOL bridge, run outside the checkout with startup scripts
  disabled, used the installed wheel and configured controller under a foreign
  `PYTHONEXECUTABLE`. `edit_bridge=1` rendered a fresh **192×144, 8-sample** PNG,
  saved the scene and opened the desktop bridge with **116 controls**. Inspection
  and clean disconnection passed. No user PyMOL configuration was changed.

Local, warm-worker and cluster regression tests passed in both full suites. The
cluster API and Slurm allocation model were not changed: one independent Blender
process remains allocated per submitted render. This feature's real runtime
checks used macOS; no new Linux/Slurm allocation or cluster deployment is claimed.

The final local candidates are in ignored `dist/chat-editor/`. Evidence is in
ignored `.validation/editor-bridge/`, including `installed-current/`,
`installed-attachment/`, `installed-pymol/`, PNGs, scenes, capture reports,
provenance and complete logs. Test workers, desktops and temporary source captures
were cleaned up. No runtime installer, remote server update, public release or
push was performed.

## Selected editor session and interaction controls (2026-11-02)

The session-reuse workflow (`fabc30b`), bounded interaction controls (`e317155`),
and playback preservation fix (`c26f986`) were validated against an already-open
Blender 5.0.1 window containing the public `1sps` example with illustrative
sidechain motion. No desktop window was opened, closed, or replaced during these
checks. The scene's existing native Geometry Nodes loop was edited in place.

- Final source suite: **450 passed, 50 skipped in 23.93 s**. The skipped tests
  require optional browser, embedded PyMOL, or Structure Studio wheel fixtures.
  Local, warm-worker, and cluster regression tests were included. The focused
  editor/scene tests passed **55 tests** from source and **55 tests in 1.54 s**
  against the wheel installed in a separate uv environment outside the checkout.
- The wheel was built from a clean archive of
  `c26f986bc9c52a5c5596e73285572cbe43141505`. All packaged Python/JSON source bytes
  matched that revision. The additional `engine-manifest.json` is generated by
  the existing build hook; source and installed controllers retain their own
  strict identity checks. Wheel SHA-256:
  `30e4523f5568b8dd517d31db95b62f7c9ee98d8e2c67b6b5e93fbcde403032f9`.
- In-place reconnect preserved the selected session ID, desktop process, open
  filename, and an unsaved material edit. Preparing reconnect did not interrupt
  the old bridge. The trusted external bootstrap replaced only bridge code;
  source-to-wheel and wheel-to-source reconnects used the same window and ID.
- `scripts/validate_editor_controls.py` passed through real stdio MCP from both
  source and the installed wheel. It checked session discovery/reconnection,
  rejection of implicit `editor_open`, invalid speed and stale revisions,
  a doubled loop speed followed by undo, wireframe followed by undo, and fast
  material-color shading without changing shader or render controls.
- Explicit play/play/pause requests were idempotent, sampled frames advanced,
  and pause retained the frame at execution. The bridge also refuses to start
  playback when another scene in the same process is already playing. That
  cross-scene guard and multiple-window behavior were checked with focused tests;
  the real acceptance used one window in the selected process.
- Both MCP runs rendered **384×288, 8-sample CPU previews** in separate background
  Blender processes: **3.13 s source**, **4.36 s installed wheel**. Native MCP image
  bytes matched the corresponding PNG files. Capturing previews retained the
  editor's revision, filename, and unsaved state. The orange protein, sidechain
  sticks, purple ligands, and fast colored desktop viewport were visually checked.

Speed controls support inspected, unlinked Geometry Nodes period inputs; they
do not retime arbitrary keyframes or simulations. The sidechain motion is
illustrative, not a physical simulation. Geometry/modifier changes remain
reported as unsupported for reusable preset export and stay in the full scene.
This change does not modify the shared renderer, local/Slurm workers, runtime
pins, or cluster deployment. No new Slurm allocation was run for it.

Local evidence is under ignored `.validation/editor-session-controls/`, including
source and installed-wheel acceptance, PNGs, manifests, and reconnect receipts.
The wheel is under ignored `dist/editor-session-controls/clean-c26f986/`. The
normal source bridge was restored in the selected desktop afterward, retaining
the faster loop and quick color view. Changes were committed locally only.

### Captured native timeline preview (2026-11-02)

Rendered one two-second loop of the current orange `1sps` scene's illustrative
sidechain motion: **48 frames at 24 fps, 640×480, 16-sample CPU Cycles**. The selected
desktop was briefly paused to save a copy of its unsaved scene, then playback was
resumed. A background-only Blender 5.0.1 process rendered frames 1–48 from that
copy, retaining its existing camera, materials, geometry, and 48-frame loop.
This used a local capture/encoding runner; `editor_render_scene` remains a still
render tool. No desktop window was opened, reloaded, closed, or replaced.

The frames rendered in **241.49 s**; rendering and MP4/GIF encoding took **250.39 s**
overall. All 48 PNGs passed dimension and digest checks. The H.264 MP4 decoded
without errors and reported 48 frames and 2.000 s; the looping GIF contained 48
frames totaling 2,000 ms. Representative frames were visually checked. The
selected session retained its process, filename, controls, unsaved state, and
active playback. Temporary source capture was removed. Media and provenance
remain under ignored `renders/chat-editor-demo/sidechain-motion/fast-preview/`.

### Reference-style draft (2026-11-03)

Validated a local cyan surface/mesh and pink/gold ribbon preset against public
1QJB using background Blender. Final demonstration: 840×650, 64 CPU samples;
schema validation and fresh-process catalog reload passed with identical style
JSON. Reference and generated pixels were inspected separately. The result is a
partial match: gloss, saturation, mesh distribution and central-bundle composition
remain different. The example relabels original chains A/B residues 165–210 as
C/D for ribbon selection without moving atoms; selectors require adaptation for
other structures. An earlier camera-policy mismatch was observed; the saved
draft now explicitly retains authored framing. No desktop scene was changed.
Reference pixels, preset JSON, comparison notes and render manifests remain in
ignored local outputs; no reference image or molecular data was committed.

### Cyan reference refinement (2026-11-03)

Used public 2R28 biological assembly 1: calmodulin chains A/B and peptide
chains C/D. Surface-only cutaways omit protein residues within 10 A of the
peptides, relabeled X/Y for selection. Exact atomic coordinate multisets match
the original assembly after solvent removal. No atoms were repositioned.
Cyan shell roughness is 0.12 with coat 0.5; mesh width/spacing are 0.009/0.55 A.
Real CPU renders at 840x650 and 64 samples were visually inspected against the
separate reference. Fresh catalog reload preserved the complete JSON and produced
pixel-identical output. The four-lobed assembly silhouette and uniform mesh
remain different from the reference; this is an illustrative cutaway, not an
exact reconstruction. Source and saved-preset manifests are retained in ignored
local outputs. No renderer code or desktop Blender scene was modified.

The same local draft was refined with brighter cyan, stronger specular/coat
response and softened area lights. Two real CPU renders were inspected; the
first had overly sharp highlights. The final 840×650, 96-sample render used
`#00AAFF` shell pigment and roughness 0.21. Fresh-process catalog reload matched
the saved style exactly; camera and geometry selections matched the earlier
draft. The final manifest/resolved style confirmed those settings. Gloss and
brightness improved, while uniform mesh coverage and the example's composition
remain visibly different from the reference. No desktop scene was changed.

A subsequent reference comparison used three sequential, visually inspected
iterations at fixed structure/camera: mesh beneath a patchy rounded cyan shell,
a deeper blue core, broader ribbons and softened highlights. The final local
catalog revision 6 rendered at 840×650 and 128 CPU samples. A fresh-process
catalog render reproduced its PNG pixels exactly; style JSON reload equality
and resolved sample count were checked. Remaining mismatches are peripheral
rather than central helices, irregular rather than square mesh, and softer
translucent patch boundaries. This is still an approximation. Evidence is in
ignored local `match-01`, `match-02`, `match-03` and `match-reloaded` artifacts.

The next color/brightness pass compared five real CPU candidates against the
original reference, retaining the existing 1QJB front-pose input and camera
(not the earlier 2R28 cutaway). Final local catalog revision 7 uses intermediate
opacity-patch size, brighter cyan highlights, darker gold and a gray-blue
background. JSON validation passed. A fresh-process preset render at 840×650,
128 samples reproduced the direct JSON render pixel-for-pixel; full JSON reload,
CIF-first input and DSS were verified in the manifest. No renderer code or
desktop scene changed. Evidence is in ignored local `tone-*` artifacts.

Color-region sRGB luma measurements guided comparison, but are not physical
luminance or registered image matching. Background median is 0.921 versus
reference 0.918; cyan highlight P90 improved from 0.735 to 0.772 versus reference
0.819. Cyan median remains too low (0.584 versus 0.625). The selected texture
balance is not an exact match: translucent patch boundaries, irregular mesh,
and the different molecular arrangement remain visible. Preset selectors still
require adaptation for other structures.

Helix-only follow-up retained the accepted cyan surface materials and styles,
lights, world and renderer settings. Two sequential 840×650, 128-sample CPU
renders were inspected against the original reference: fuller rounded ribbons
and broader satin highlights, then a richer pink and restored gloss. Final
ribbon width/thickness are 2.35/0.9; roughness 0.27, specular 0.45, coat 0.2.
Local catalog revision 8 passed schema validation and fresh-process JSON reload;
its preset render reproduced the direct JSON render pixels exactly. CIF-first
input and DSS were confirmed. Evidence is in ignored local `helix-*` artifacts.
Camera settings/orientation stayed fixed, but automatic fit changed translation
slightly and orthographic scale by about 0.006 percent as ribbon bounds changed.
Blob appearance is therefore not claimed pixel-identical. Different helix
placement, underside brightness and highlight positions remain approximate.
No renderer code or desktop Blender scene was modified.

A further helix reference pass corrected overly stacked turns using narrower
rounded spiral bands (width 1.7, thickness 0.8), after visually comparing two
new 840×650, 128-sample CPU renders. Fresh pink/yellow materials use modest
colored emission to lift shadows; this is an artistic approximation, not a
claim about the reference shader. Blob materials/styles and scene-light,
world, renderer and camera settings were checked unchanged. Bounds-driven
framing and indirect illumination can still affect blob pixels. Final local
catalog revision 9 passed schema validation and fresh-process JSON reload;
the saved-preset render was pixel-identical to the direct JSON render. Manifest
checks confirmed CIF-first input and DSS. Evidence is in ignored `curl-*`
artifacts. Different helix placement/occlusion and slightly lighter/cooler pink
remain. No renderer code or desktop Blender scene was modified.

On 2026-11-04, two more full-quality helix material comparisons increased pink
and yellow saturation/emission, then softened overly sharp highlights and
reduced excessive yellow brightness. Only the two helix materials changed;
blob settings, geometry, lights, world and actual camera matrix/scale matched
the preceding spiral render. Colored emission may affect nearby surface pixels.
Local catalog revision 10 passed schema validation, fresh-process full JSON
reload and a pixel-identical saved-preset render at 840×650, 128 CPU samples.
CIF-first input and DSS were verified. Original reference and both candidates
were visually inspected. Evidence is in ignored `neon-*` artifacts. Different
helix placement and partial occlusion still limit resemblance to the prominent
central bundle in the reference. No renderer code or desktop scene changed.

The subsequent helical-tube request changed only native rounded cartoon width
and thickness to 1.35/1.35 for the two colored helix styles. All material values,
blob styles, lighting and camera settings were checked unchanged. The resulting
full-quality render was visually inspected for a plump coiled cross-section
and continuous highlights. Native backbone paths were retained; straight helix
cylinders were not enabled. Local catalog revision 11 passed schema validation,
fresh-process full JSON reload and pixel-identical preset rendering at 840×650,
128 CPU samples. Manifest confirmed CIF-first input and DSS. Geometry-driven
auto-fit and light interactions can still change nearby pixels. Evidence is in
ignored `tube-*` artifacts. No renderer code or desktop scene was modified.

The approved tubular style was promoted to built-in `cyan_candy_mesh` with its
full JSON unchanged and the exact approved PNG added to the README gallery.
Gallery provenance records public 1QJB preparation, pose and hashes without
committing coordinates, executable sessions, original reference pixels or local
paths. Selection limitations and adaptation are documented. The default remains
`mint_sky_assembly`. The built wheel was installed into a fresh environment
outside the checkout; qualified/unqualified preset lookup, schema validation,
15-entry catalog, option resolution and equality to the approved JSON passed.
Gallery JSON/PNG hashes were checked. Relevant schema, reference, local and
authoring tests passed (101 tests). The retained render/reload checks above
establish visual behavior; this promotion did not rerun Blender.

# Cluster onboarding preflight (2026-11-03, local only)

Added site-configurable Slurm GPU requests (`gpu_request`, `gpu_type`,
`constraint`), an Apptainer version floor (1.1; SingularityCE/legacy Singularity
warn as untested) and an `sbatch --test-only` dry run to `cluster doctor`. Each
batch script now records the compute node's `apptainer --version`, which
`validate_cluster_slurm.py` parses (an error message there fails acceptance) into
`evidence.json`. The acceptance preflight calls `cluster doctor` itself and stores
its report under `evidence.doctor`. The batch step repeats the job's GPU request,
scheduler commands search only the configured tool directories plus
`/usr/bin:/bin`, and the doctor dry run uses a directory inside the spool. The onboarding steps are
in `.agents/skills/cluster-onboarding/SKILL.md`. Defaults still produce
`--gres=gpu:1`, so existing `server.json` files are unchanged in behavior.

Mac source tests: **485 passed, 50 skipped** (real PyMOL, browser and built-wheel
checks were not enabled for this run). The cluster tests use a scheduler
substitute. **Not yet run on a real cluster:** `cluster doctor` against real
`apptainer --version` output and real `sbatch --test-only`, `--gpus`/typed-GRES
jobs, step GPU visibility under `--gpus`, and compute-node version recording in
`validate_cluster_slurm.py`. The 1.1 floor is provisional; the oldest Apptainer
compatible with the job flags has not been measured.

## PR #6 integration (2026-11-04)

Reviewed the four cluster-onboarding commits and merged current main_merged
into the PR branch. The sole conflict appended different validation entries;
both histories were retained. The combined source suite passed: **490 passed,
50 skipped**. The first run exposed a stale Structure Studio preset catalog
from the prior cyan-candy addition; regenerated its JSON/Markdown references
and reran the full suite successfully. Built and installed the combined wheel
in a fresh environment outside the checkout; doctor module import, runtime
version parsing, pinned Python compatibility, typed GPU options and presence
of the cyan-candy preset passed. No live server was changed or real Slurm job
submitted in this integration check. The PR author reports real-site CPU/GPU
acceptance in the PR description; that is distinct from these local checks.

## Northern Nights preset (2026-11-04)

Adapted the supplied Northern Nights scene's style settings and inspected its
poster as a private reference. The repository preset and gallery contain only
style configuration and a newly rendered public 1SPS preview, not research
coordinates, executable scenes, private paths or the original poster. Three
900×900, 96-sample CPU renders were inspected while correcting palette encoding,
selecting public peptide chain D and retaining the pale background. The final
render loaded `northern_nights` by built-in ID in a fresh process. Manifest
checks confirmed CIF-first export and DSS. The source's per-residue atom colors
and custom Gaussian emission halo are approximated by a spatial gradient and
native glow; custom interaction guides and topology patches are not included.

Built and installed a wheel in a fresh environment outside the checkout:
preset aliases, schema validation, full JSON equality and the 16-entry catalog
passed. Refreshed the Structure Studio plugin catalog. Full source tests:
**492 passed, 50 skipped**. No desktop Blender session or running cluster service
was modified; no animation execution was newly validated. Local evidence is
retained in ignored `renders/style-onboarding/northern_nights/` artifacts.

## Northern Nights STAT previews and cutaway (2026-11-04)

Kept two separate presets: bright `northern_nights` ribbons and black
`northern_nights_cutaway` with an isolated protein surface cut and complete
nearby residue sticks. Both select chain B as the mint peptide. PTR/SEP/TPO
sticks now use an explicit element palette and `ElementColor`, with uniform
mint emission disabled on those sticks. The shared renderer is unchanged.

Replaced the earlier 1SPS gallery example with the user-selected STAT input.
Inspected the supplied animation frame and its saved scene, then transferred
its viewing direction relative to the molecule. Corrected a transposed camera
matrix after inspecting the first renders. Both final 900×900, 96-sample CPU
renders loaded the built-in preset in fresh Blender processes and were visually
inspected against the references. Framing fits the whole complex rather than
copying the reference's close-up crop. Manifests confirm CIF-first export, DSS,
Blender 5.0.1 and Molecular Nodes 4.5.10; the cutaway manifest confirms a
camera-oriented cut applied to the isolated surface. The presets approximate
the source spatial colors/glow and omit its custom hydrogen-bond dashes.

For PTR B11, the selected PyMOL source contains one Cα and both boundary bonds:
GLY 10 C → PTR 11 N and PTR 11 C → VAL 12 N. Reopening the saved bright Blender
scene with scripts disabled also confirmed one Cα, peptide/cartoon selection
membership and both boundary edges in its atom mesh. This is evidence for this
input, not a general TPO/SEP render-continuity test.

Full source suite: **494 passed, 50 skipped**. Built a wheel and installed it in
a fresh environment outside the checkout; both preset aliases, JSON equality,
schema validation, the 17-entry catalog and unchanged default preset passed.
Refreshed the Structure Studio catalog and gallery hashes. No desktop window
was launched and no cluster deployment or Slurm execution was changed. Molecular
coordinates, sessions, source animations and private paths remain outside tracked
files; only the requested presets and newly rendered previews are included.

## Northern Nights reference reconstruction and rocking cutaway (2026-11-04)

This revision supersedes the simplified Northern Nights settings above. Inspected
the supplied bright close-up, STAT cutaway animation, H3T11 palette reference,
saved scenes and creation scripts. Recovered residue-order palettes, PTR atom
colors, source light strengths and emission-only Gaussian halo settings. The
bright preset uses the source close-up framing; the dark preset uses the STAT
rounded opening and orientation with the H3T11 violet-blue-purple binder palette.
Only newly rendered STAT previews are tracked; the unpublished H3T11 coordinates,
reference images, original animations and executable sessions are excluded.

Both built-in presets rendered in fresh background processes at 900 × 900,
96 CPU samples, Blender 5.0.1 / Molecular Nodes 4.5.10. Final PNGs were inspected
against their references. The selected input retains its native geometry; no
claim of pixel-identical reconstruction is made. Pocket stick colors and inferred
contact endpoints can differ slightly from the source's manually prepared scene.
A separate 320 × 320, 8-sample cutaway preview rendered all 16 frames of a two-second,
8-fps, ±8° camera rock. Inspected frames at both extremes and the central view;
the baked surface opening, PTR and contact guides stay aligned. GIF and MP4 were
produced; the small GIF is linked from the README.

The initial saved-scene audit caught PTR's DSS assignment being dropped because
it was a HETATM. Fixed the export to retain PTR/TPO/SEP secondary-structure hints
while excluding unrelated hetero residues, added regression coverage and rerendered
both final previews. Reopened both saved scenes with script execution disabled:
PTR B11 has one Cα, helical secondary structure and two boundary peptide bonds.
Live PyMOL export confirms GLY 10 / PTR 11 / VAL 12 are helical and the residue
name is restored to PTR. This establishes PTR continuity for this input, not
real-render validation of every TPO/SEP case.

Both reopened scenes contain 18 capsule dashes for six putative polar contacts,
PTR emission 0.65 driven by atom Color, 16.5-pixel Gaussian blur at 900 pixels and
an additive halo factor of 0.7. The dark surface contains 2,755 inner-wall faces
with its separate cavity material. The unchanged default mint_sky_assembly also
completed a 256 × 256, 8-sample render of the public example and was inspected.

Full source tests: **505 passed, 50 skipped**. Built a wheel and installed it into
a fresh uv environment outside the checkout: both preset JSON documents match
source, schema validation, required engine capabilities, new module imports,
sequence-color behavior and the 17-entry catalog pass. These installed-package
checks do not constitute an installed-wheel Blender render. Updated the plugin
catalog and gallery hashes. Existing cluster-payload compatibility is covered by
tests; no new Slurm/Apptainer run or service deployment was performed. All renders
ran in background processes without opening another desktop Blender window.
Local manifests, resolved styles, reference decisions and audit results are kept
in ignored renders/style-onboarding/northern_nights artifacts.

## Fresh Structure Studio installation (2026-11-05)

Cloned GitHub into a separate directory. The default branch was `main` at
`1690762888aff97cb3020348ce7befc9a6363da9`, with neither the Claude marketplace
nor Structure Studio. Switched that new clone to remote `main_merged` at
`a83fedf34996ef40d2337d7702fbfa04d22197e1`, built both ZIPs and the wheel using
`build_release.py`, and passed `verify_release.py`.

Tested on Apple Silicon macOS with separate Claude configuration directories,
Python installations, virtual environments, renderer configuration and empty uv
caches. Existing Git, system Python, Claude Code and FFmpeg were prerequisites;
this was not a pristine OS or Linux/claude.ai sandbox test. Existing uv 0.8.22
could not download pinned Python 3.11.15. An isolated uv 0.13.7 installation
resolved that prerequisite without updating the user's uv or shell profile.

- Claude Code accepted the source marketplace install, but its installed plugin
  lacked the renderer wheel. Its wheel lookup failed with the instruction to use
  a staged release package. Registration alone does not establish render readiness.
- Extracted the newly built plugin ZIP and registered it through a temporary
  local marketplace in another empty Claude configuration. Manifest validation
  passed with missing-description/author warnings. No model invocation was used.
- Fetched public 1STP. Workspace generation from the bundled public CIF worked
  before setup and created no runtime. Explicit managed setup then installed
  Python 3.11.15, PyMOL and bpy in **141.74 s**. Doctor reported both ready.
- Installed engine identity matched the release manifest; all **17 presets**,
  including Northern Nights, and **six recipes** were present. No external
  renderer or existing development runtime was configured.
- Rendered the downloaded 1STP through the installed plugin with a workspace
  snapshot: a **384×288, 8-sample** default still took **242.18 s**, and a
  **320×240, 8-sample**, 16-frame cutaway took **53.13 s**. Both manifests report
  Apple M3 Metal GPU execution. These are first-run observations, not benchmarks.
- MP4 decoded completely: 16 frames, 2 seconds. GIF decoded with 16 frames and
  infinite looping; its duration was 2.01 seconds. An initial exact-2-second GIF
  assertion failed; the follow-up records and permits the 10 ms timing difference.
- Restored PSE, Blender scene, MP4, GIF, frame archive and recipe with matching
  hashes. Reopened PSE in the fresh PyMOL environment and the Blender scene in
  fresh bpy with embedded scripts disabled. Inspected the mint still and closed/
  opened cutaway frames; these are small execution previews, not final demo art.

Evidence and packages are retained in the separate fresh-install artifact folder,
including `fresh-install-evidence.json` and per-step logs. The source clone and
existing user runtimes were not modified. Live Claude Artifact publication,
in-chat operation, persistence and cross-chat reopening remain untested. Nothing
was pushed, published or deployed.

## Cluster onboarding discovery and live preflight (2026-11-06)

Inspected `main_merged` at `a83fedf`, including the PR #6 merge (`982faf1`).
The cluster client, server and job tests passed: **87 passed**, one existing
Starlette TestClient deprecation warning, **3.39 s**. CLI help for cluster
administration, doctor and the real acceptance script also ran successfully.

Connected to the existing authorized Linux x86_64 cluster and found the retained
onboarding installation from `493eb39`. Its configured private Apptainer is
**1.5.4**, Python is **3.11.15**, and the current Slurm client reports **25.11.8**.
The system `singularity` is SingularityCE **4.4.2**; the successful checks used
the explicitly configured Apptainer, not that system alternative.

Fresh `cluster doctor` runs using that installation passed for CPU,
`--gres=gpu:1`, and `--gpus=a100:1`, with no warnings (**1.87 s**, **1.93 s**,
and **1.83 s**, respectively). Each checked the SIF checksum, gateway/container
identity, Python compatibility and actual Slurm dry run. An exploratory GPU dry
run with an explicit GPU partition was rejected by the site's submission policy;
the retained configurations correctly omit the partition and let the site select
it from the GPU request. No launcher change was needed.

Read the retained November 3 `evidence.json` files: CPU, generic-GPU and typed-GPU
acceptance all record `passed: true`. They record CPU, **NVIDIA H200 NVL/CUDA**
and **NVIDIA A100 80GB PCIe/CUDA**, respectively, for both still and rocking
renders, with saved Blender scenes reopened and compute-node Apptainer **1.5.4**.
These are historical acceptance results inspected today, not new render runs.

The retained gateway/image pair has engine identity
`3dc2a7a49f30115c4ac9b23c003d009b96f63d10ae1c87eb3fb9ca295ffc6af2`
and SIF SHA-256
`a12a1ff7254dcb0b2b54edaf8e536bb4b1793e9e8ccfc5fd6c72e4ff458c3e73`.
Current `main_merged` has engine identity
`43c25dac0610d31f67ac7698aff4ea1f97d7011b5ee6c1b23130c75df5d263ef`.
The fresh preflight therefore establishes readiness of the retained installation,
not full acceptance of the current renderer. Testing the current revision requires
a matching gateway and worker release followed by new CPU/GPU acceptance outputs.

Onboarding is currently a repository skill plus CLI workflow. The stdio MCP and
Structure Studio command parser expose no cluster onboarding command, and the
plugin packaging script does not include the repository onboarding skill,
server templates or acceptance script. The two local Structure Studio ZIPs also
lack these onboarding resources. No new render allocation, persistent service,
deployment or publication was made during this check. Private cluster connection
details remain outside version control.

## Current main_merged cluster acceptance (2026-11-06)

Built a fresh release from a clean detached checkout of **`a83fedf`**, preserving
the working branch and its existing documentation edits. `verify_release.py`
verified the wheel and both Structure Studio ZIPs against that revision. Installed
the wheel into a new pinned Linux controller environment outside the checkout;
**87 cluster tests passed in 8.31 s**, with one existing Starlette TestClient
deprecation warning.

Verified the existing base SIF's recorded SHA-256 and confirmed unchanged runtime
contracts and dependency locks before using `Apptainer-update.def`. The update
installed only the new application wheel offline. Linux environments and the
reused managed Python remained at their original absolute paths. No Mac runtime
was copied and no render job installed dependencies. The two-CPU image build ran
in a real Slurm allocation and completed in **1:46**. The site's debug QoS permits
only two CPUs per user; an initial four-CPU build allocation was cancelled before
the image build, and the corrected submission respected that limit. Site-specific
render settings stayed in private server configurations; no renderer or launcher
source changes were required.

The new gateway and worker both report engine identity
`43c25dac0610d31f67ac7698aff4ea1f97d7011b5ee6c1b23130c75df5d263ef`.
The updated SIF SHA-256 is
`933141132d10e16877a2b526886cac70b0e720864cd44aed5c4ce127c0c81433`.

Ran the unchanged `scripts/validate_cluster_slurm.py` with `--cancel-running`,
using the installed wheel, fresh private output directories, real PyMOL,
temporary authenticated loopback gateways, Slurm and Apptainer. All three final
`evidence.json` files record **`passed: true`**:

- CPU: two CPUs, 8 GiB, no GPU, **64.61 s**.
- Generic GPU: four CPUs, 8 GiB, `--gres=gpu:1`, **582.38 s**.
- Typed GPU: four CPUs, 8 GiB, `--gpus=a100:1`, **578.66 s**.

Times include queueing and verification and are not renderer benchmarks. Both GPU
configurations left partition selection to the site and recorded one **NVIDIA
A100 80GB PCIe / CUDA** for both still and animation. CPU manifests recorded CPU
execution. Each preflight checked the new image checksum, matching build identity
and actual Slurm request. Gateway and compute-node Apptainer were **1.5.4**;
Slurm was **25.11.8**. Runtime manifests report Python **3.11.15**, Blender
**5.0.1**, Molecular Nodes **4.5.10**, NumPy **1.26.4** and Biotite **1.6.0**.

Each run rendered public **1STP** as a 192×144 still and four-frame rocking movie
at four samples, then cancelled a separate probe after observing it running.
Verified downloads, original-request recovery, idempotency, ownership, token
revocation and deletion passed. Saved Blender scenes reopened with embedded
scripts disabled. MP4 decoding, changing frame hashes, four-frame looping GIFs,
session-bundle restoration and reopening the restored PyMOL/Blender files passed.
After downloading the evidence locally, all **33 artifact hashes** matched.
CPU and CUDA stills and sampled rocking frames were visually inspected: the
mint structure remains in frame against the pale background. These are small
execution previews, not final-quality figure review.

After a chat daemon restart, the first generic-GPU harness ended before recording
its final verdict, despite completing both renders. Its `passed: false` evidence
was preserved. Confirmed its allocations were terminal, retained diagnostics and
removed its temporary job data. The successful generic-GPU rerun and typed-GPU
run used detached logging, independent output directories and the same immutable
release. No interrupted request was silently resubmitted.

Final checks confirmed every acceptance/build allocation had ended, all temporary
listeners were closed, credentials were removed and test spools contained only
ownership/idempotency tombstones. Removed the temporary local build checkout.
Release artifacts, configurations, manifests, rendered outputs and evidence are
retained in ignored local artifacts and the isolated cluster installation. No
persistent production service was changed and nothing was pushed or published.

## Cluster configure credential input (2026-11-06)

Inspected the CLI parser, credential importer and administrator credential writer
on `a83fedf`. `cluster configure` is flag-only: it requires either
`--credential-file` (JSON containing exactly `url` and `token`) or `--url` (an
explicitly unauthenticated service). It has no token argument, stdin token reader
or hidden prompt. Profile naming and a separate CA file are optional flags.

A real CLI smoke check used a synthetic token and an isolated temporary
`BLENDER_RENDER_HOME`, with stdin closed throughout. File import succeeded,
stdout contained only profile and URL, the saved profile had mode `0600`, and
a credential file with mode `0644` was rejected without printing the token.
Running configure without the required flags exited with usage status `2`.
The command retains the transfer file after importing it. No real credential
was read, no existing profile changed and no server connection was attempted.
The cluster-client tests passed: **18 passed in 3.16 s**.

**Outstanding client reachability gate:** run `blender-render cluster info
--profile lab` from the actual Studio runtime and configuration, using the lab's
real endpoint, administrator-issued credential file and CA where required. The
synthetic import above and real cluster-side loopback acceptance do not establish
Studio-to-lab connectivity or authentication. This session's default Studio home
has no configured runtime or cluster profile; no such live client check was run.

## Mac-to-cluster gateway reachability (2026-11-06)

Started a bounded, temporary authenticated loopback gateway from the validated
`a83fedf` controller and SIF on the authorized cluster. Forwarded a Mac loopback
port over SSH, keeping gateway startup and forwarding on the same SSH connection
so a different login-node selection could not redirect the tunnel.

Transferred a real, newly issued mode-0600 credential file over SSH. From the Mac
CLI, outside the checkout and with an isolated configuration directory, imported
that file using `cluster configure --credential-file`, with stdin closed. The
subsequent `cluster info` succeeded and reported token authentication, version
`0.4.0rc4` and the expected engine identity
`43c25dac0610d31f67ac7698aff4ea1f97d7011b5ee6c1b23130c75df5d263ef`.
An unauthenticated request returned HTTP 401; after explicit token revocation,
the imported credential was also rejected with HTTP 401. No token was printed.

Confirmed both the gateway and local tunnel closed, remote credentials were
removed, the temporary local credential/profile directory was removed, and no
render jobs were submitted. Redacted evidence is retained in ignored local
artifacts. This establishes real Mac CLI authentication and API reachability over
SSH. It does not establish direct HTTPS access, a persistent gateway deployment,
or execution from the Claude app's installed Structure Studio runtime; that last
client integration check remains outstanding.

## Mac submit and fetch through a temporary gateway (2026-11-06)

Using the validated `a83fedf` controller and SIF, started a temporary authenticated
loopback gateway with a fresh private spool. Gateway preflight passed, including
the matching image identity and real Slurm dry run. Connected the Mac through an
SSH tunnel on the same SSH connection, imported a newly issued private credential
file, and verified the gateway identity with `cluster info`.

Submitted exactly one public **1STP** still from the Mac CLI using
`mint_sky_assembly`, **192×144 pixels and four samples**. Local PyMOL exported
the CIF with secondary structure assigned; the selection excluded solvent.
The real Slurm job completed with exit code `0:0` and a **4:02 allocation**.
The fetched manifest confirms **NVIDIA A100 80GB PCIe / CUDA** execution,
Blender **5.0.1**, Molecular Nodes **4.5.10**, Python **3.11.15**, NumPy **1.26.4**
and Biotite **1.6.0**. The Mac submit-through-fetch check took **257.61 s**,
including queueing, startup, rendering, status polling and download; this is not
a pure rendering benchmark.

Fetched the PNG, manifest and resolved style through the gateway API. All three
artifact sizes and SHA-256 hashes matched the gateway's metadata. Decoded and
visually inspected the PNG: the mint cartoon and ligand are visible and framed
against the pale background. This is a tiny execution preview, not a final figure.

After verification, deleted the completed remote job data through the API,
retaining its ownership/idempotency tombstone. Revoked the credential and observed
HTTP 401, closed the gateway and tunnel, and removed temporary local/remote
credentials and the local test profile. Slurm accounting confirms the allocation
ended. Evidence and the fetched image remain in ignored local artifacts. This
establishes actual Mac CLI submission and fetching through the gateway; it was
not executed from the Claude app's installed Structure Studio session.

## Saved lab profile submit and fetch (2026-11-06)

Ran the requested Mac CLI submission of `examples/1stp.cif` through the existing
`lab` profile with the installed PyMOL application's Python interpreter (verified
that `pymol2` imports). Used `mint_sky_assembly`, 192×144 pixels and four samples,
with the default selection. One Slurm job, `22967374`, progressed from queued to
running to completed. The gateway's creation-to-finish interval was **247.05 s**;
this includes scheduling and startup and is not a rendering-only benchmark.

Fetched `renders/lab-1stp.png`, its manifest and resolved style using the saved
receipt. Fetch verified all artifact sizes and SHA-256 digests. Decoded the PNG
as 192×144 RGBA and visually inspected it: the mint cartoon and ligand are visible
and framed against the pale background. The manifest records Blender 5.0.1,
Molecular Nodes 4.5.10, Python 3.11.15, NumPy 1.26.4 and Biotite 1.6.0. The existing
profile, connection and completed job were retained. No renderer code changed;
this was a real submit/status/wait/fetch runtime check.

## co-pymol merged into the repository (2026-11-09)

Merged co-pymol's history into this repository as the top-level `co_pymol`
package, beside `blender_render` so the renderer's engine inventory is unchanged.
Its Blender tools now use this checkout's renderer: `backend="local"` calls
`blender_render.local.render_payload`; `backend="cluster"` (alias `lab`) uses the
saved `blender-render cluster` profile through `blender_render.cluster.client`.
The separate pinned backend checkout and its setup skill were removed.

Ran both paths from the installed PyMOL application's Python (3.10, `pymol2`)
using `examples/2trx.cif` as a cartoon with chain A coloured salmon,
`mint_sky_assembly`, 480 px wide and eight samples, captured with `capture_scene`
and submitted with `BlenderBridge.submit`:

- **Local:** the detached helper rendered on the Apple M3 GPU (Metal) in 13 s.
  The PNG was visually inspected: PyMOL colours were kept and the scene was framed
  as captured. The completion notification was sent.
- **Lab:** after `blender-render cluster connect --profile lab` (the gateway was
  already running), job `55280959b09f60677a4f724cc5e6c1cd` completed in 250 s
  including Slurm scheduling. The fetched manifest records an NVIDIA A100 80GB PCIe
  (CUDA). The download was digest-verified by the cluster client and notified.

## Lab reconnection guidance (2026-11-09)

Updated repository and PyMOL MCP instructions to check the saved cluster
connection before an authorized lab submission and attempt one noninteractive
reconnect before asking the user to sign in. Local receipts, gateway acceptance
and GPU execution remain distinct; reconnecting must not duplicate submissions.

Real runtime observation: the configured profile reported `tunnel_open=false`.
Invoking the existing `connect('lab')` implementation returned `connected`,
`gateway_started=false` and `tunnel_reused=false`: it opened a new local tunnel
to the existing gateway without interactive authentication. The accumulated SSH
log contained timeouts, broken pipes and unreachable-host errors, but did not
establish the exact cause or time of this disconnect. This check establishes
reconnection only, not successful rendering or visual correctness.

Using PyMOL's Python, `python -m pytest tests/co_pymol/test_blender.py
tests/co_pymol/test_skills.py` passed all **89 tests** (one Pydantic forward-reference
warning). No renderer or transport code changed.

## Cutaway fade regression and decoded-frame limit (2026-11-09)

Raised the decoded animation-frame allowance from 1 GiB to 10 GiB; frame-count,
archive-size and duration bounds remain unchanged. Boundary tests exercise both
camera-orbit and camera-path inputs at exactly 10 GiB and one row beyond it. A
276-frame, 1280 x 1115 request now passes normalization (about 1.47 GiB decoded).
A wheel containing only the limit change was installed outside the checkout and
verified with `EngineSeal`. A corresponding staged lab image passed the worker
identity check and cluster doctor, including Slurm dry-run submission. It has
**not been activated**; those checks do not establish a GPU render with that image.

Reproduced the reported cutaway artifact using the saved public 8EA6 payload and
Cyan Mint Cutaway colours. The lid's Boolean intersection included cut-plane caps
coincident with the permanent cutaway caps. Its initial zero-opacity still also
left the lid render-visible, unlike subsequent zero-opacity frames. A fresh
400 x 357, 16-sample Apple Metal render showed the dark/jagged planes immediately
before the lid disappeared, even after filling its empty material slots.

The fix marks caps using a private cutter copy and removes only those faces from
the baked fading shell; it also initializes visibility consistently with frame
updates. The original cutter and material assignments are preserved. Recipe
camera paths, palette and timing are unchanged. A fresh four-frame render took
23.68 s including the isolated runner. Visually inspected opacity 0.1, 0.0001,
and two zero-opacity frames: the overlapping planes were absent. The anchor and
next identical-view frame differed by mean 0.086 channel levels (0-255), with no
pixel differing by more than 30; near-zero versus hidden was 1.558, versus 10.669
before cap removal. Low-sample transparency noise remains in that comparison.

With `BLENDER_RENDER_TEST_BLENDER` selecting the real Blender executable,
`python -m pytest` on animation, animation-path, lid, cutout-plane, cluster-client,
cluster-server, cluster-jobs, cluster-connect and Structure Studio animation tests
passed **222 tests** in 17.94 s (two existing warnings). The real Blender geometry
regression checks a half cube has five exterior faces and no cut-plane cap,
including when the original cutter shares the surface material; it verifies
initial/frame visibility and that temporary cutter objects/materials are removed.

History inspection found the original recipe's lid algorithm unchanged since
its October introduction/refactor. The retained older validation preview uses
1STP at 160 x 120 and 12 frames; it does not establish why a deleted earlier user
render appeared correct. Evidence is retained under ignored
`.validation/indigo-lynx-cutaway/`. The lab cutaway update is held for user review
of the corrected fade video; no existing completed job has been resubmitted.

For user review, rendered the unchanged original path from 4.5 through 7.5 s:
73 frames at 24 fps, 400 x 357, 16 samples, in 109.86 s including the runner.
Inspected closed, mid-fade, near-zero and adjacent open frames. Encoded review
video is 3.041667 s (73 frames); the side-by-side comparison uses the corresponding
original frames on the left and corrected frames on the right. Review files are
under the original render's ignored `review/` directory. The two-module candidate
wheel (limit and lid only) was installed into an isolated environment outside the
checkout and passed `EngineSeal`; it is staged locally, not deployed to the lab.

### Follow-up: back-wall pop during the reviewed fade

User review found a remaining defect around the end of the three-second clip.
Adjacent frames had a distinct jump at clip frame 60 (2.5 s), despite an identical
camera: the zero-opacity lid was switched to `hide_render=True`. Diagnostic
renders of the same saved Blender scene separated sample count, transparent-ray
depth and visibility. With 256 samples and an entirely transparent lid still
present, Cycles' default depth of eight left dark patches at the back of the
cutaway. Raising only that depth to 64 removed the localized dark patches:
compared with hiding the lid, maximum channel difference fell from 72 to 24,
and pixels with a channel difference over 30 fell from 222 to zero. Increasing
samples alone did not remove them. These are actual Apple Metal renders, not
mock-based evidence.

The revised fix raises transparent depth to at least 64 only when constructing
an animated cutaway lid, preserves an already-higher value, and keeps the fully
transparent shell render-visible through the end of the fade. This supersedes
the earlier initialization/hide fix above; there is no visibility switch at zero.
The original cap removal remains. The review sample count is raised from 16 to
64 as a preview-quality choice; preset defaults, recipe camera path and timing
are unchanged. Relevant animation/path/lid/cutout/recipe tests passed **122 tests**
in 1.95 s with the real Blender executable enabled (one existing zip warning).
The real Blender test now checks the depth floor, preservation of a depth of 96,
and consistent visibility at zero opacity. The updated two-module candidate wheel
passed an installed `EngineSeal` check outside the checkout. Deployment remains
on hold for user review.

The revised 73-frame review completed in 301.17 s including the isolated runner,
at 400 x 357, 64 samples, Apple Metal. Visually inspected the rear wall from
1.67 through 2.54 s. The adjacent-frame mean change at 2.5 s fell from 1.571 in
the first review to 0.094 channel levels; its maximum per-channel change was 15
with no pixels over 30. Changes immediately before/after were 0.104 and 0.063,
so the earlier isolated jump is absent in this check. The revised MP4 and a
previous-preview/revised-preview comparison are saved in the ignored `review/`
folder. FFprobe verified 73 frames, 24 fps and 3.041667 s; the MP4 is padded to
400 x 358. No lab renderer configuration or existing job was changed.

### Approved lab deployment and GPU acceptance

After the user approved the revised video, activated the two-module update on
the existing lab gateway and Slurm image. The installed controller and container
both report engine `08e4c3729725c8c3b95b5d15b976c0599865b5d8afb5a01a22f8ab924c1a1f2e`.
Only `animation.py` and `animation_blender.py` changed relative to the previous lab
wheel, plus regenerated package identity/RECORD metadata. The pinned Linux
runtime stayed at its original absolute path. The image passed worker identity,
cluster doctor and Slurm dry-run checks. A staging helper initially used the
wrong scheduler class name; correcting the helper and rerunning doctor passed
before any live configuration changed.

No jobs were active at the gateway switch. Backed up the previous wheel and both
server configurations, updated the controller and both image/checksum settings,
then reconnected the existing profile with the same credentials and private spool.
The old image and rollback files are retained. The active controller confirms a
10,737,418,240-byte decoded-frame limit and accepts a 276-frame, 1280 x 1115,
11.5-second path. Earlier notes that deployment is pending are now superseded.

Real acceptance job `3b0c5967db48d05aaedfcc262c2897e1` (Slurm `25467384`) completed
in **272.53 s including scheduling**, rendering four diagnostic fade frames from
the saved 8EA6 payload at 400 x 357 and 64 samples. Its manifest records an
**NVIDIA A100 80GB PCIe (CUDA)**, Blender 5.0.1, Molecular Nodes 4.5.10, Python
3.11.15, NumPy 1.26.4 and Biotite 1.6.0. Downloads passed client digest checks.
Visually inspected the frames. Near-zero versus zero opacity differed by mean
0.134 channel levels, maximum 20, with zero pixels over 30. Reopened the fetched
Blender scene in a background process with embedded script execution disabled:
transparent depth is 64, both lids remain render-visible at full transparency,
their exterior face counts are 11,716 and 11,038, and no marker material remains.
This was a new bounded acceptance job; no previous full render was resubmitted.

## Follow-up report after deployment (2026-11-10)

The user reported another back-wall artifact in a subsequent render. The newest
local animation, `golden-seal`, records the deployed `08e4c3729725...` engine;
its actually loaded `animation_blender.py` SHA-256 equals the current source
(`150bf3d0eeab9fbe1b7536699ee53eba1c001d22a4040ba9d825e72e3b015315`). The live
gateway still reports that engine. This rules out an older lid implementation
for that saved video. It uses a different framing (800 x 925, 32 samples) from
the approved short preview. Inspecting its complete frame sequence did not yet
establish the exact remaining defect reported by the user; a timestamp was
requested. The near-zero/zero fade transition is small in this video as well.

Rendered a four-view local diagnostic from this exact saved payload at 400 x 463,
16 samples, in 25.68 s, then inspected its evaluated cut faces. The two permanent
back caps are coplanar within floating-point precision and overlap in projection.
A bounded diagnostic offset of one cutter changed some overlap pixels, but this
alone does not establish the cause of the reported glitch. No additional
production code or lab renderer changes were made. Evidence is under ignored
`.validation/golden-seal-cutaway/`; do not treat this investigation as a confirmed
third fix. The previous GPU acceptance covered a small fade sample, not all
camera positions in the full recipe.

### Rear-wall depth refinement

The user's clarification identified thin, jagged shapes in the fully open still,
not only a fade transition. In a background Blender diagnostic, hiding the
combined interior mesh removed the small ribbon fragments; a narrow irregular
surface gap remained. Moving the rear plane forward concealed these background
features. A prototype convex backing extended outside the molecular silhouette
and was rejected. No backing mesh or additional Boolean algorithm was added.

Changed Cyan Mint Cutaway's `ligand_cutout_plane_scales.back` from 0.9 to 0.35
on both surface layers. Compared 0.6, 0.35 and 0.2 using fresh shared-pipeline
stills at 640 x 740, 64 samples, Apple M3 Metal. The 0.6 setting retained a pointed
background fragment; 0.2 obscured more of the ligand. Inspected the 0.35 close-up:
the rear plane covers the thin background fragments while retaining the central
ligand and main ribbons. This deliberately reduces visible pocket depth; it does
not alter atomic coordinates or the native interior mesh. The manifest confirms
0.35 for both surfaces. `tests/test_reference_presets.py`, `tests/test_style_schema.py`
and `tests/test_animation_path.py`: **82 passed**.

Full motion validation remains pending at this entry. A 400 x 463, 32-sample
local diagnostic is rendering the entire 276-frame path. The final lab request,
`smooth-mint-cutaway`, reuses the saved scene and original path at 800 x 925,
32 samples, 24 fps and 11.5 seconds. Slurm accepted job `25552525` (gateway job
`ed300c713a02b79a1979af534c6ad451`); the persistent CoPyMOL worker handles retrieval
and the configured macOS completion notification. The earlier 0.6 lab preview
is a separate diagnostic, not this final request. Local clients resolve the
revised preset and send its complete style to the existing lab renderer; its
bundled preset and engine image have not been redeployed for this data change.

### Depth workaround rejected; direct comparison with main

The final 0.35 lab animation completed on the A100 and was downloaded and
digest-verified; its notification was submitted to macOS. The user reported
that the blue side now looked broken. Inspection of the open still and rocking
frames confirms that the prior depth refinement is not an accepted fix. Restored
both back scales to 0.9 and restored the reference documentation. The preset file
again has SHA-256 `98e8d06f73bdbf5122548ec15425c7a92f05457c42e514f265b19377d05025d1`,
exactly matching both `main` and the gallery's recorded preset hash. The depth
refinement entry above records a rejected experiment, not a shipping change.

Fetched `origin/main` and compared commit `1690762888aff97cb3020348ce7befc9a6363da9`
in an isolated detached worktree. Plane construction, frame axes, cap materials,
Boolean modifier setup and coordinate mapping functions are unchanged between
main and this branch. The plane-cut application adds the later optional fading
lid hook. All shared renderer modules actually loaded by the local 0.35 still
and the completed lab job have matching SHA-256 hashes; this does not mean that
the animation implementation is identical to main, which only supports camera
rock/360 animation.

Rendered two controls directly from main using the same saved 8EA6 payload,
64 samples, Apple M3 Metal, with no animation or fading shell. At the gallery's
recorded orthographic view, the 640 x 429 image reproduces its composition and
cutaway appearance (13.66 s). At the user's saved perspective close-up, the
640 x 740 image still contains the rear fragments (18.73 s). Thus those static
features also occur with main's original code at this view: the camera-aligned
cut crosses a different part of the molecule. A smooth wall at every viewing
angle is not established by the gallery example. The two controls, manifests
and source comparison are retained in ignored validation output.

The earlier local full-motion diagnostic resolved to 400 x 925, not the intended
400 x 463: its copied output overrides retained the original height. It is not
evidence of matching full-frame composition. No additional lab image or animation
was submitted during this comparison, and the live PyMOL view was not changed.

### Animation from the approved main-gallery view

The user approved the main-gallery preview as the basis for another cutaway
animation. Prepared a frozen copy of its full style and gallery view, preserving
the original 0.9 back depth, orthographic camera, materials, lights and surface
selections. The recipe's fully open anchor is exactly that view. Its overview is
15% wider, followed by the existing cutaway timing and a 12-degree rock. A private
headless PyMOL saved a PSE with the gallery orientation; the live session stayed
unchanged. Full output is 276 frames, 24 fps, 11.5 seconds, 800 x 536, 64 samples.

A five-frame check sampled the closed shell, mid-fade, fully open anchor and both
rocking extrema. Slurm initially held job `25577723` for `MaxGRESPerAccount`, then
allocated one A100 on a GPU node. While it was pending, the identical local check
completed in 30.96 s on Metal. The Slurm check subsequently completed and its
downloads passed digest checks. Inspected its PNGs: the anchor reproduces the
approved view, with no reported blue-wall defect visible at either sampled rock
extreme. Camera matrix and orthographic scale match the approved main render
within 1e-6; style selections and camera configuration match exactly. Mean RGB
channel difference from the approved still is 0.350/255 (Metal check: 0.218/255).
This establishes these sampled frames, not complete temporal verification.

After inspecting the Slurm check, submitted the full `gallery-cutaway` request
through the persistent worker, with the same frozen style and saved scene. Output
and recovery receipts are in the user's ignored `pymol/gallery-cutaway/` folder;
an alias in the configured PyMOL render directory makes it discoverable there too.
The complete movie remains pending at this entry. No renderer or preset source
was changed for this new request.

### Frame-preserving MP4 export

The user reported flashing in the complete gallery animation despite the sampled
previews looking correct, and specifically asked to investigate the MP4 export.
The full Slurm render had completed on an A100. Decoded and compared all 276 MP4
frames against their matching, checksum-verified archived PNGs. Frame count,
ordering and 11.5-second timing are correct. The original lossy YUV 4:2:0 encode
has mean rear-region error 2.642/255 during the rock, and maximum channel error
85. These differences alone do not establish that encoding caused every reported
flash; five representative images also cannot prove complete temporal stability.

Tested an all-intra CRF-8 YUV encode and a lossless RGB encode from the same
archive, with no new rendering. The former still changes colour pixels; the
latter decodes exactly to every original RGB pixel in all 276 frames. macOS
AVFoundation's native decoder successfully read all 276 frames of the RGB MP4
with completed status and no error. Diagnostic renders that hid the transparent
lid or slightly offset a back cutter were not adopted as geometry fixes.

Changed shared MP4 encoding to lossless `libx264rgb`, CRF 0, all-intra RGB, while
retaining white alpha compositing and odd-dimension padding. Added a real FFmpeg
round-trip test containing moving one-pixel colour detail, verifying every
decoded pixel and frame plus padding. Relevant animation, path and local API
tests: **98 passed**. Re-exported the actual archive through this changed shared
function and verified all 276 decoded frames byte for byte; native macOS decoding
also passes for that exported file. The full movie is about 61 MiB rather than
562 KiB. Geometry, camera, palette and timing are unchanged.

Promoted the verified export to the user's existing `render.mp4`, preserving the
original as `render-original-compressed.mp4` and recording a separate local
re-encoding receipt with both hashes. The original remote manifest is retained.
The source change is in the working tree. Built an encoder-only wheel from the
previously deployed lab wheel, replacing only `animation.py` and generated
integrity metadata. Verified its engine seal outside the checkout; runtime pins
and the existing geometry implementation are unchanged. In the candidate Linux
image, encoded and decoded four moving RGB-detail frames and verified every
pixel exactly. Worker identity, image digest, cluster doctor and Slurm submission
dry-run checks passed. With no active gateway render jobs, activated the new
controller and image, then verified the reconnected gateway reports engine
`8c0b6d562449c3b093770cdf2b79abdfc70cbe6e0eb4b54a8df589fec3c2cba5`.
The previous image, wheel and server configurations remain available for rollback.
No additional GPU render was needed for this encoding-only change.
This proves PNG-to-video pixel preservation, not a visual guarantee that the
underlying rendered frame sequence contains no temporal artifact.

### Rejected RGB export and coincident rear caps

The user reported that the 10:31 replacement still flashes and displays wrong
colours. Its timestamp and digest confirmed it was the replacement, not a stale
file. The native decoder check above only counted frames: extracting its actual
BGRA pixels revealed a severe colour conversion error (mean channel error
32.30/255 at frame 184, versus 2.63 for the compatible YUV export). FFmpeg's exact
RGB round trip did not establish native playback colour correctness. Restored a
YUV 4:2:0 export, rolled the lab controller and image back to the previous engine,
and replaced the RGB regression with a real AVFoundation colour-patch check.
The 10 GiB frame budget and the preceding lid fixes remain intact.

Inspected the actual saved scene's Boolean cap polygons. The blue and mint rear
caps overlap, with depths differing only by floating-point noise. A material-ID
render makes their competing intersections visible as red/green speckles. Across
23 sampled rocking frames, moving one rear plane forward by 0.0001 world units
removes the interior mixed-colour patches. This diagnoses coincident geometry,
rather than MP4 compression. The earlier whole-region mean error obscured this
small but visible defect.

Added deterministic separation for repeated cut planes in the shared renderer:
the first surface retains its requested plane, later matching planes move inward
by 0.1% of the cut radius. Only coincident plane equations are adjusted; requested
and resolved equations are saved on the objects, and actual insets are recorded
in the manifest. Both the permanent cut and fading lid use the same cutter. A
real Blender Boolean test checks three overlapping surfaces have distinct,
ordered cap depths and an unrelated plane stays unchanged. Relevant tests pass
(105 initial passes plus the corrected runtime-configured geometry test).
The new source rendered all 108 frames from the open anchor through the full rock
at 640 x 429 / 64 samples on Metal (320.5 s). The manifest records zero inset for
the first surface and 0.00011058 world units for the second. Its anchor differs
from the approved main-gallery still by mean 0.385/255 across RGB channels.
Rendered material-ID diagnostics for all 107 subsequent frames from that saved
scene: the broad mixed-colour patches are absent, leaving at most ten mixed
interior-mask pixels along antialiased material junctions. The original diagnostic
had 114–1579 such pixels across its 23 sampled frames. Native AVFoundation decoded
all 108 preview frames; the first frame's mean RGB error against the PNG is
0.775/255, and its cyan/mint colours were visually inspected.

Built a wheel containing only the shared cut-plane correction and the compatible
encoder comment relative to the prior deployed renderer. Verified its engine seal
outside the checkout; runtime pins are unchanged. The Linux image passes worker
identity, YUV 4:2:0 encoding, cluster doctor and Slurm submission dry-run checks.
Activated controller and image engine
`ff8b33faf71dd3e30a982b4d40b26dfab178b7519ea8c217832dee07a22642e1`, retaining
rollback files. Submitted a new full 276-frame animation with the original saved
scene, camera path, palette and dimensions. Its result uses a separate output
folder to distinguish it from the rejected 10:31 video.
The user viewed the new rocking preview and confirmed: "Rear wall looks stable."
Full Slurm job `25609712` reports CUDA on an NVIDIA A100 80GB PCIe and is still
rendering at this entry. The persistent worker will retrieve its existing job;
a separate bounded local verifier waits for that receipt and then checks all
276 native-decoded frames, frame timing, exact preset/camera provenance and the
resolved cap separation. It writes a verification receipt beside the movie and
never submits or cancels jobs. Full-movie verification is pending, not claimed
by the successful preview checks above.

The full Slurm render subsequently completed and was retrieved successfully.
Its background verifier passed all 276 native-decoded frames at 24 fps / 11.5 s,
confirmed the original resolved preset and camera were unchanged, and verified
the same cap separation as the user-approved preview. Mean native RGB error
against the PNGs is 2.514/255 (maximum frame mean 2.766/255), consistent with the
compatible lossy export. Inspected the delivered rear-wall frame and its cyan/mint
colours. Final MP4 SHA-256:
`53cea286e7311743b33a5e489665f4bd42cc7f49b76d46f2b7356549db143850`.
The live lab gateway still reports the corrected engine above. Source and tests
remain uncommitted in the `pymol-merge` working tree; the user requires confirming
the commit destination before any commit or push.

### Reveal, Rock and States recipe checks

The user clarified that the cutaway now looks correct and asked to validate the
remaining templates. Prepared Reveal (9.5 s) and Rock (6 s) from the saved 8EA6
scene, with Cyan Mint Cutaway colours. A real Reveal preparation exposed NumPy
`float32` clipping planes escaping into the strict JSON request. Added native
float conversion at the PyMOL framing boundary. A regression using real headless
PyMOL failed for Reveal and Cutaway before the fix; all four tested recipe views
(Reveal, Cutaway, States, Rock) now serialize correctly.

Preparing a 360-state example also exposed repeated/skipped states at floating-
point frame boundaries (`13/360*360` falls just below 13). Added a small rounding
tolerance before taking the state index. The regression now requires exactly
states 1 through 360, once each. Relevant recipe, real PyMOL and path-validation
tests: **50 passed** using PyMOL's Python. Test-only dependencies were read from
the existing development environment; neither renderer runtime pins nor the lab
image changed for these client-side recipe fixes.

For the requested adenylate-kinase example, downloaded 4AKE and 1AKE mmCIF files
from RCSB, retained their hashes, selected chain A and alternate location A, and
matched all 1,656 protein heavy atoms across 214 residues. Fitted 133 CORE C-alpha
atoms (residues 1–29, 68–117, 161–214; RMSD 1.551 Å). Prepared 360 cosine-eased
Cartesian interpolations for a 15-second open → closed → open visualization.
State 1 equals the open coordinates; state 181 equals the fitted closed ones.
This is an interpolated visualization, not a simulation. The inhibitor remains
in the disabled experimental closed reference; the animated object is protein-
only because the open structure lacks it. Experimental references, the PSE,
endpoint CIFs and interpolation provenance are in ignored local outputs.

The States request passes cluster schema/size validation (13.15 MB) and maps one
conformation to each frame. A real local Blender check rendered open, halfway,
closed and return states in 15.53 s on Metal; inspected distinct conformations,
stable domain colours and framing. Full lab outputs are pending; these sampled
checks do not establish complete rendered temporal quality. All recipe fixes
and their tests remain uncommitted, as requested.

## PyMOL merge review fixes (2026-11-12)

Fixed local cancellation through the shared renderer's fresh-process preview,
remote cancellation confirmation, SSE write-failure recovery, animation backend
selection, custom renderer configuration roots, confidence classification,
residue identifiers and half-turn camera interpolation. Consolidated the receipt
writer and session validation, removed the unused operation key, updated the
merged-package installation guide and refreshed `uv.lock`. The existing staged
and unstaged merge work was preserved; no commit or push was made.

Validation performed against this working tree:

- PyMOL's Python 3.10: `python -m pytest tests/co_pymol -q` — **212 passed**
  in **54.28 s**, with one existing Pydantic settings warning. The real headless
  export/PSE preservation check now uses the current exporter signature and runs
  whenever `pymol2` is installed. Regressions cover cancellation of one batch
  child without cancelling its sibling, disconnected and ambiguous cluster
  cancellation, proxy reconnection with outstanding calls, explicit backend
  selection, custom configuration handoff, experimental B-factors, numbered and
  insertion-coded residues across chains, and exact/near-180-degree rotations.
- Renderer Python 3.11: `python -m pytest tests --ignore=tests/co_pymol -q`,
  with the native Blender executable/runtime configured for the two geometry
  checks — **552 passed, 50 skipped** in **42.39 s**. Skips were optional browser,
  generated-wheel and additional Structure Studio PyMOL checks. The generated
  wheel check was subsequently enabled and passed separately. Four existing
  dependency/fixture warnings remained.
- Real local cancellation: exported a synthetic six-alanine peptide with
  headless PyMOL, submitted it through `BlenderBridge`, waited for the actual
  Blender child of the isolated runner, then cancelled. Both runner and Blender
  stopped before the worker reported `cancelled`; no success PNG was published.
  The complete probe took **0.92 s**. A real-process regression also checks that
  cancellation removes the private source snapshot.
- A subsequent **192 × 144, four-sample** render of that peptide completed in
  **10.99 s**. The manifest confirmed **Metal / Apple M3 GPU**; result hash
  verification passed. Visually inspected the PNG: the peptide was present,
  framed and unobstructed. This was a runtime smoke check, not figure-quality
  validation. No desktop editor window or live PyMOL session was changed.
- Built the wheel from an isolated source copy including the custom build hook,
  installed it into a separate target outside the checkout, and verified module
  locations, packaged notification resources and the shared receipt helper.
  All **212 PyMOL tests passed again** against the installed package in
  **54.53 s**. The wheel's renderer inventory and bytes matched the source.
- With the newly locked **MCP 1.27.2** in an isolated dependency directory, the
  hosted adapter, local MCP render and editor MCP checks reported **11 passed**
  in **1.01 s**. `uv lock --check` and `git diff --check` passed.

Cluster cancellation behavior was exercised with controlled transport failures;
no live lab job was submitted. Test renders, installed-package targets and
temporary source snapshots were removed after validation. Renderer runtime pins
and the lab image were not changed.
