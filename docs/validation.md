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
