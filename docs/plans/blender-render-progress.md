# Blender Render implementation progress

## Starting point and scope

- Started 2026-09-07 on `codex/public-release`, HEAD `b1bb7d1c5d0d832b75c096eeea855b306815d79f`, matching the live private draft PR #1 head. No checkout, reset, commit, push or deployment.
- Starting `git status --short`: only `?? docs/plan/`. No tracked edits. This linked worktree has no configured Git remote; the GitHub connector verified `magnusbauer/blender_render` and read all 19 review threads and four review submissions.
- The supplied plan existed at `docs/plan/blender_render_implementation_plan_v3.md`, not the requested path. It is preserved unchanged; `docs/plans/blender-render-v3.md` is a byte-identical copy. Read all 605 lines. Read root AGENTS.md in full; no applicable nested AGENTS.md found.
- Scope: Milestone A only, local isolated tests. No Slurm allocation, Modal submission, production service changes, credential/privacy/budget changes, publication, or release acceptance claims.
- Reuse `export.py` bundles, `style.schema.json`, all existing preset JSON, `core.py`, `rendering.py`, `blender_custom_render.py`, queue/worker/backend implementations. No replacement framework or file-tree migration.

## Milestones

- [x] A implementation and local contract validation: shared schema, built-in catalog, JSON/override resolution, camera precedence, identity and snapshot/hash. Fresh local fixture verification subsequently passed using the existing runtimes; see follow-up below.
- [ ] B: fresh checkout-aware developer previews, actual Python extension, inspect/save/reuse user presets, reference-image loop.
- [ ] C: lifecycle, operation identity, generic Slurm launch, side-effect-free reads, artifact recovery and runtime negotiation.
- [ ] D: thin consumer adapters, best-fit selection, compact results and explicit install routing.
- [ ] E: rebuilt wheel/runtime artifacts and executed backend/installed-host acceptance.

## Review findings mapped to milestones

All links are PR #1 inline comments. Current-code checks are static unless listed in test evidence; an open thread is not by itself proof of a current defect.

| Finding | Milestone | Starting-code verification |
|---|---|---|
| [Custom pastel identity](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528245) | A | CLI/PyMOL send implicit preset with style_doc; core gate accepts name/filename regardless of representation. Fixed in A; adapter-to-worker surface regression. |
| [Camera overwritten](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528248) | A | prepare_options replaced custom camera when preserve_view defaulted true. Fixed in A; full-JSON and override regressions. |
| [Duplicate validation](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618504) | A | Shadowed lists, duplicate structural validation and weak Blender import fallback confirmed. Consolidated in A; semantic checks retained. |
| [Local authoring/catalog](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528249) | A/B/D | Built-in filenames exist; no user save/reuse or editing surface. A provides shared catalog/resolution; B owns persistence/workbench, D owns MCP expansion. |
| [Temporary export identity](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528254) | C | export.py uses UUID combined selection; semantic fingerprint recovery needs dedicated regression. |
| [qlogin coupling](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528256) | C | hpc_remote.py hardcodes qlogin; confirmed statically. |
| [Partial result recovery](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528258) | C | Client.download rebuilds paths from current artifacts; no receipt. Confirmed statically. |
| [Unexpired results on replacement](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528264) | C | deploy waits only for active queue/running jobs before stop. Artifact-loss recovery remains untested. |
| [Outer/child identity](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528268) | C | Gateway submits same content with new key without explicit rerender. Needs operation-lifecycle regression. |
| [Late worker admission release](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528269) | C | worker_http releases only when task done within grace, no late completion release. Confirmed statically. |
| [Raw-upload selection](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946528272) | C | CLI raw upload does not pass selection. Confirmed statically. |
| [Read-only allocation](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618485) | C | stdio read helper uses configured_client without attach_only. Lifecycle consequences require regression. |
| [Unnamed attachment format](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618491) | D | Optional metadata exists; historical suffix-default finding deferred to input-adapter tests. |
| [Recent-job recovery](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618494) | C/D | Existing tool surface lists temporary structures; owner-scoped operation recovery remains deferred. |
| [Local-only install routing](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618500) | D/E | Installed routing not reverified; do not claim data leakage or fixed routing. |
| [One submission surface](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618507) | D | Compatibility import/submit and preferred render coexist; deliberate compatibility decision deferred. |
| [Legacy widget](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618514) | D | FastMCP private-hook subclass remains; retirement deferred. |
| [Compact result](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618517) | D | Full manifest/result presentation remains; PNG/scene option distinction covered in A only. |
| [Installed attachment-to-PNG gate](https://github.com/magnusbauer/blender_render/pull/1#discussion_r3946618521) | E | No fresh installed-host execution in A; historical reports remain qualified. |

## Milestone A implementation

- `spec.resolve_options()` is the shared library entry point. `service.prepare_options()` delegates; direct legacy worker requests translate through it. CLI/PyMOL/MCP requests still converge through the existing service/renderer, with no new service or engine.
- `presets.py` catalogs the six existing selectable IDs, descriptions, aliases, built-in namespace, source JSON, revision, defaults, representation/material requirements and schema. All seven pre-existing JSON assets, including the reference-only asset, remain byte-identical to HEAD. `blender-render presets` retains its ID list and adds catalog metadata. No user-preset persistence or hosted tool redesign in A.
- One cached Draft202012Validator supplies structural validation. Nonblank chain/material/blend-name rules moved into the schema. Material references, residue-range ordering and required cutout frame axes remain semantic checks. Missing Blender-side validation dependencies now fail explicitly.
- Full JSON overrides the adapter's implicit preset identity. The strict reference gate follows its representation/selection layout rather than preset names or output filenames. Changing materials/colors/camera preserves the reference guard; a surface-only style does not inherit cartoon/sticks requirements.
- A queued request contains the resolved JSON, schema version, preset source/revision, required capabilities and SHA-256. The worker verifies the hash, writes that exact JSON to Blender, and records it in the diagnostic manifest. Rough/color/output options resolve before hashing; the gateway forwarding shape does not apply rough twice. Capability metadata is declarative, not runtime negotiation.
- CLI adds `--overrides-json` and `--preserve-view` / `--no-preserve-view`; PyMOL adds `overrides_json` and `preserve_view`. Objects merge recursively and arrays replace. Complete JSON is authoritative. On preset execution, saved pose is the default; any explicit camera override or `preserve_view=false` retains the preset camera plus edits. For reorientation, use the existing `camera.mode="override"` controls; intrinsics in `pymol` mode use `override_intrinsics=true`.
- Explicit dimensions beat viewport-derived defaults; explicit convenience flags beat JSON fields; resource policy runs last. PNG remains the transport output. `save_blend` in JSON/overrides or an explicit adapter option requests a scene. The implicit CLI/PyMOL false default no longer cancels a scene explicitly requested in full JSON. An explicit PyMOL `blend_output` also requests scene creation.

Example shared-library use (no render or service startup):

```python
from blender_render.spec import resolve_options
request = resolve_options({
    "preset": "green_paint",
    "overrides": {"camera": {"mode": "override", "yaw_deg": 30, "lens_mm": 70}},
    "save_blend": False,
}, bundle)
# request["style_doc"] is the existing version-1 style JSON sent to Blender.
# request["render_spec"] records the digest and provenance.
```

## Acceptance and evidence

Test environment: existing `.venv/bin/python` (Python 3.11.15), with `BLENDER_RENDER_HOME=/private/tmp/blender-render-milestone-a/config`, `PYMOL_BRIDGE_SESSION_ROOT=/private/tmp/blender-render-milestone-a/sessions`, `PYMOL_BRIDGE_TMP_ROOT=/private/tmp/blender-render-milestone-a/runs`, and `PYTHONDONTWRITEBYTECODE=1`. Tests use isolated stores and fake workers; no live service connection or compute submission.

| Executed check | Result |
|---|---|
| Baseline: `.venv/bin/python -m pytest -q -p no:cacheprovider` | **111 passed, 4 warnings**, 3.96s. No pre-existing test failures. |
| Existing suite after initial refactor, same command | **111 passed, 4 warnings**, 3.77s. |
| First focused run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_spec.py tests/test_style_schema.py tests/test_pymol_adapter.py tests/test_render_server.py` | **1 failed, 53 passed**, 0.25s. New regression exposed a legacy adapter dropping an unknown field. Fixed by rejecting unknown request keys before staging/dispatch. |
| Focused run after fix and MCP coverage, same command | **59 passed, 2 warnings**, 0.90s. |
| Expanded suite: `.venv/bin/python -m pytest -q -p no:cacheprovider` | **145 passed, 4 warnings**, 4.08s. |
| Final suite after diff-review corrections, same command | **145 passed, 4 warnings**, 3.92s. **34 new tests**, no unresolved new test failures. |
| `.venv/bin/python -m build --wheel --no-isolation --outdir /private/tmp/blender-render-milestone-a/wheels /private/tmp/blender-render-milestone-a/wheel-source` | Could not import `setuptools.build_meta`; existing dev environment lacks setuptools and wheel (and pip). This was an environment limitation, not a passing build. |
| `.venv/bin/python -m build --wheel --outdir /private/tmp/blender-render-milestone-a/wheels /private/tmp/blender-render-milestone-a/wheel-source` | Isolated build succeeded; repeated successfully after final source corrections. Source/package assets copied to temporary staging, declared build dependencies installed only in temporary build environments. Existing `.venv` unchanged. |
| `/private/tmp/blender-render-milestone-a/installed-env/bin/python -m pip install --no-deps --no-index /private/tmp/blender-render-milestone-a/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl` | Installed successfully into a new temporary venv. Verified core/spec imports resolve inside that venv, outside the checkout. |
| `/private/tmp/blender-render-milestone-a/installed-env/bin/python -m pytest -q -p no:cacheprovider /private/tmp/blender-render-milestone-a/wheel-check` | **59 passed, 2 warnings**, 0.78s. CWD was installed `lib/python3.11/site-packages`; tests copied outside checkout. PYTHONPATH put installed site first and existing dev site second for test dependencies. This proves wheel module/assets/contract coverage, not a clean dependency install or live renderer validation. |
| Blender runtime probe: `/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup --python-use-system-env --python-exit-code 1 --python blender_render/runtime_probe.py` | Sandbox attempt exited 139 during Blender startup. Authorized unsandboxed retry started Blender **5.0.1**, then exited 1: `ModuleNotFoundError: No module named 'molecularnodes'`. No render executed. The dev interpreter also has no `pymol2`. Runtime not installed or changed. |
| `git diff --check` | Passed; final review repeated after documentation. |
| Plan/preset/public fixture integrity checks via Python SHA-256 and `git show HEAD:<asset>` | Canonical plan byte-identical to original; all seven preset assets byte-identical to HEAD; all five public structures match `examples/structures.json`. |
| Final source audit using `ast.parse` and `zipfile.ZipFile.read` | All eight changed/new package Python modules and the new test module parse; all eight package modules plus schema are byte-identical between the final wheel and reviewed checkout. Final wheel SHA-256: `5a25b79175c42ae80add0e50c5c05d26c20766c2aefb3ba2d4238bd847f6c05e`. |

Existing warnings are Starlette/httpx, anyio BlockingPortal, Authlib jose and Authlib httpx deprecations. The focused/wheel suites encounter only the latter two. No dependency warning was introduced by A.

Focused regressions in `tests/test_spec.py` prove full JSON / preset override equivalence for pastel and green paint, array replacement, caller input isolation, explicit camera and dimension precedence, public limits, unsupported fields rejected before staging/dispatch, retained semantic checks, stable preset identifiers/aliases, exact worker snapshot/hash, tamper rejection, rough forwarding without repeated reduction, PNG-only versus explicit scene, reference-gate preservation, and full CLI/PyMOL surface-only transactions. In-process HTTP/stdio MCP tests prove shared option and scene semantics; they do not prove installed-host display or backend execution.

Retained baseline evidence (historical, no fresh pixel comparison):

- Plan SHA-256: `75f3ccfce77a62dbfde24773d67879bd6589597a64ba3846d6aeede6a81c876e`.
- Pastel DNA image `.validation/1bna-mac.png`: `4f5c386fcf96259ac9568e4249256ed2c23337626099443cf692815c5cfc2859`; manifest `.validation/1bna-mac.manifest.json`: `983f3822b7a68e47f956199d0da4ff81d5a3f5896d454f62375aeb940dc97c84`.
- Green-paint image `examples/rendered/1sps_dried_paint.png` (also `.validation/1sps-mac-pse.png`): `838c8025fe7516b12f92307a3a44f7e07279d6a1f7e2b5711c64c0bed41296a8`; manifest `.validation/1sps-mac-pse.manifest.json`: `99b9b1050cfcfeb9da63c92bffea330e439d1e7197a0e52a0de781e1cdd9122d`.
- Both historical manifests record Blender 5.0.1, Molecular Nodes 4.5.10, Python 3.11.13, NumPy 1.26.4 and Biotite 1.6.0. They are preserved evidence, not proof that the current runtime is complete.

**At the initial milestone handoff, not executed / unverified:** fresh local PNG/scene or camera visual comparison, fresh PSE export, Slurm, Modal, production restart/deployment, regenerated SIF, clean full dependency install, installed ChatGPT/Codex attachment/display, and all B–E acceptance scenarios. The initial runtime probe failure was not counted as a pass. The user-requested local follow-up below supersedes the local runtime/export/render limitation; remote and installed-host checks remain unverified.

## Follow-up: reuse the existing PyMOL and Blender runtimes

The user's request to verify results prompted a more complete runtime lookup. The earlier environment limitation was too broad: the development `.venv` lacks PyMOL, but the existing validation configuration already identifies a working Anaconda Python 3.9 with **PyMOL 2.5.4** and its `pymol2` API. The `pymol2` import is unchanged from the starting commit. It creates an isolated PyMOL instance for CLI file export; the live PyMOL adapter uses the application's `cmd` directly. No new PyMOL installation is required.

The existing Blender **5.0** extension directory contains Molecular Nodes. Adding that directory and the previous `.validation/config/runtime/site` to the subprocess import path makes the probe succeed with **Blender 5.0.1, Molecular Nodes 4.5.10, Python 3.11, NumPy 1.26.4 and Biotite 1.6.0**. The earlier bare/factory-startup probe omitted the extension import path. No package, credential, application configuration or running service was changed.

For reproducibility, `PYMOL_PYTHON` below is the `pymol_python` value read from the existing `.validation/config/config.json`; `MN_PARENT` is the existing Blender 5.0 `extensions/blender_org` directory. These are machine-local execution inputs, not paths added to shareable presets. Outputs and the validation driver are under `/private/tmp/blender-render-milestone-a-live`.

Executed follow-up checks:

1. `"$PYMOL_PYTHON" blender_render/export.py examples/1sps_pose.pse /private/tmp/blender-render-milestone-a-live/1sps-payload.json`: **passed**. Exported 1,106 atoms / 110 residues / chains A,D; DSS ran. All 18 camera values exactly match `examples/1sps_pose.json` (maximum difference 0).
2. `"$PYMOL_PYTHON" scripts/validate_export.py /private/tmp/blender-render-milestone-a-live/export-validation.json`: **passed**. Two transformed enabled objects, 3,772 atoms, disabled-object exclusion, camera, atom colors and insertion codes preserved. Maximum coordinate error **0.000008529923 angstrom**.
3. Existing Blender `--background --factory-startup --python-use-system-env --python-exit-code 1 --python blender_render/runtime_probe.py`, with `PYTHONPATH` including the two existing runtime locations above: **passed**, matching pinned versions.
4. `.venv/bin/python /private/tmp/blender-render-milestone-a-live/verify_renders.py`: four real renders through the current `prepare_options -> HostBackend -> shared rendering transaction -> Blender` on an isolated local Metal worker. `BLENDER_RENDER_HOME` points to a new temporary configuration root; `BLENDER_RENDER_SITE` points read-only at the existing validation dependency site; `PYTHONPATH` includes the existing Molecular Nodes extension parent. All four outputs are **640 x 480 RGBA** with transparent and opaque pixels, at 32 samples. The driver retains only the explicit output copies; per-render directories were cleaned and the dedicated worker was stopped (socket removed).

| Case | Result | Scene requested | Resolved JSON SHA-256 |
|---|---|---|---|
| Pastel saved PSE pose | **passed**, 3.375s; `mn_cartoon`, `mn_native_sticks`, ligand spheres off | No; PNG only | `6c4ded8ca823f8cfe0da533ff4adda14c4899d42e03ec4a09a83d32c49df7cd6` |
| Green paint saved PSE pose | **passed**, 2.459s; `mn_cartoon`, `mn_native_sticks`, ligand spheres off | No; PNG only | `7d6a2eab3a98b5a8f372d62a169e3fe104179f2e3f135a09f4347c5397ef6a07` |
| Custom surface via full JSON, with implicit pastel ID deliberately retained at input | **passed**, 1.508s; no cartoon/sticks, no incorrect pastel gate | Yes; `.blend` produced | `0554e28d32890410a14f61004402fdf469e4b20eceda428e57f6a3f55cd15ff6` |
| Equivalent custom surface via preset plus overrides | **passed**, 1.354s | Yes; `.blend` produced | `0554e28d32890410a14f61004402fdf469e4b20eceda428e57f6a3f55cd15ff6` |

5. Reopened `surface_full.blend` in background Blender with `--disable-autoexec` and `/private/tmp/blender-render-milestone-a-live/check_scene.py`: **passed**. Camera transform differs from the saved-pose baseline, lens property is 80 mm, resolution is 640 x 480, film transparency is enabled, engine is Cycles. Projection remains orthographic as supplied by the PSE; the lens property is retained but does not control orthographic magnification.
6. Numeric image comparison of the two surface renders: resolved JSON is identical; images differ in **two channel values by 1/255**, mean absolute channel difference **0.000001627604** on the 0–255 scale. Passed maximum-one-level / mean-below-0.00001 tolerance. They are not claimed to be bit-identical. PNGs were visually inspected: expected cartoon versus surface representations, same saved composition for pastel/green, no edge clipping.

The current green-paint manifest also matches the earlier `.validation/1sps-mac-pse.manifest.json` exactly for materials, styles, camera specification, world, lights, geometry and the actual camera matrix (maximum matrix difference 0).

Fresh local evidence files: `export-validation.json`, `render-validation.json`, `scene-validation.json`, four PNGs, four manifests, and the two explicitly requested scenes in the temporary directory above. These are low-sample acceptance images, not final publication renders. No Slurm allocations, Modal submissions, uploads, deployments, or production-service changes occurred. Remote/backend-wide and installed-host acceptance remain unverified.

## Decisions, conflicts and next task

- Schema remains version 1.0; full style documents remain supported. Objects merge recursively; arrays replace wholesale. Preset identity is provenance, never sufficient to impose representation gates.
- Saved-pose preservation is a default for preset execution. Explicit full-JSON camera and explicit camera overrides win; preserve_view=false selects the preset camera. Explicit dimensions win over viewport-derived defaults. Resource policy is applied after resolution.
- Keep PNG output default and scene creation explicit, including save_blend inside a complete JSON/override document. Transport filenames remain controlled by the service.
- Root AGENTS requires preset 1 cartoon/native sticks; preserve these checks for the actual cartoon/sticks specification, without applying them to a surface-only custom representation. No security requirements weakened.
- Request snapshot metadata changes normalized job records. Existing legacy requests are translated, but historic stored fingerprints and already-running older workers were not migrated or validated; any future upgrade must follow the existing drain/stop rule. No running service was modified.
- The plan's broader hosted allowlist/capability negotiation, fit-without-saved-pose and user/project catalog persistence need later work; A must not pretend static metadata is a live runtime probe.
- No genuine instruction conflict required weakening security or release gates. The requested canonical plan path was supplied without moving the user-owned original.
- Next bounded task after A: Milestone B's local inspect/validate/save/reuse and fresh-process preview foundation, with source identity and isolated public fixtures. Stop for review before starting it.

Stopped after Milestone A. At the user's follow-up request, this milestone is committed on `codex/milestone-a-json-contracts`, branched from the starting HEAD. At the user's further request, the original `docs/plan/` is also tracked on this branch, with its contents unchanged and byte-identical to the canonical plan. PR threads were read but not modified; no remote push or PR update is included.


## Follow-up: eight reference presets and complete gallery (2026-09-07)

This is the user's subsequent, explicit request for eight image-inspired presets, public structure matching, a refined cutaway with colored pocket residues, and a README gallery. It does not start Milestones B–E. Starting commit: `24629ce781f4f1e15d271767e2a802fc5022f10b`, branch `codex/milestone-a-json-contracts`. The starting checkout was clean; current changes are this follow-up. No reset, checkout from main, broad migration, remote push or PR publication occurred. The original plan, `docs/plan/`, AGENTS security requirements and seven legacy preset JSON files remain byte-identical to the starting commit.

- [x] Reuse the shared engine and resolver; add eight named presets with `reference_01`–`reference_08` aliases.
- [x] Iterate actual local Blender renders, including the supplied sessions and downloaded public structure candidates.
- [x] Fix camera-facing surface cuts and colored flat walls; retain the lower shell and complete interior geometry.
- [x] Add complete nearby protein residues with element-colored native sticks.
- [x] Render and visually inspect all 14 catalog entries for the README; link each identifier to its JSON.
- [x] Test JSON/override equivalence, scene opt-in, shader/camera/geometry behavior and the installed wheel.
- [x] Review the diff and retain exact evidence and remaining limitations.
- [ ] Identify the original structures behind references 1, 5 and 6; exact image reproduction remains unverified.
- [ ] Slurm, Modal, deployment, clean full dependency installation and installed-host attachment/display checks remain unexecuted.

Implementation: optional fields expose native surface smoothing, nucleic-base thickness, camera roll/fit/projection/focus, element colors, gradient pigments and a frosted grid. These extend the existing schema and scene builder; they do not introduce a second renderer. Explicit camera precedence and PNG-only defaults remain intact. The eight preset contracts also prove full JSON and preset-plus-overrides equivalence. The broader review-to-milestone mapping above remains the historical PR context; this follow-up does not claim to resolve deployment or workbench findings.

The final cyan/mint preset cuts only isolated surfaces. A 12-angstrom pocket radius uses left/right 0.85, top 2.0, bottom 1.0 and back 1.2. Earlier full-height cuts exposed too much of the bottom; three localized candidates were compared, and the final version combines the retained lower shell with an open upper edge. A 5-angstrom proximity selection expands to **19 complete residues / 165 atoms**. Nitrogen/oxygen colors were checked in the stored molecular attributes and inspected in the PNG. The ligand remains pink.

Real 8EA6 geometry audit: both cuts preserve the same **187,960 interior vertices / 308,806 polygons**, SHA-256 `3b90a56d64c5a0da6563f42a511e1ca54c25090687b04088387771f84e87adf8` before and after each Boolean. Both resulting surfaces have **zero boundary edges and zero nonmanifold edges**; each has three flat, pigment-colored cut faces. Closed surfaces and visible interior were verified together, rather than substituting caps that obscure the pocket. Sanitized evidence is committed in `examples/presets/cutaway_checks.json`.

Public examples: 8EA6, complete 7PZB, 8AA5, 8AW3 and 1AZM. The six legacy previews use the existing public 1SPS pose fixture. The complete 7PZB entry restores DNA E/F absent from the supplied session. Public 7R6R was tested but rejected as a less suitable silhouette. Reference 1 uses 8AW3 as an analogue; reference 5 uses acetazolamide in 1AZM for a real sulfonamide; reference 6 uses 8AW3 without inventing the absent organic ligand. Source URLs, coordinate hashes, views, preset hashes and thumbnail hashes are in `examples/presets/gallery.json`. No supplied PSE, private coordinates, reference screenshot, credentials or machine-specific paths are added to the gallery. See `docs/reference-presets.md` for selection assumptions and match limitations.

### Executed checks

Working directory for repository commands is the edited checkout. `$TASK` below is `/private/tmp/blender-reference-presets`. The existing development Python 3.11.15, PyMOL 2.5.4 and Blender 5.0.1 / Molecular Nodes 4.5.10 were reused. Tests set `PYTHONDONTWRITEBYTECODE=1`, `BLENDER_RENDER_HOME`, `PYMOL_BRIDGE_SESSION_ROOT` and `PYMOL_BRIDGE_TMP_ROOT` to task-specific temporary locations. Render checks use `PRESS_BIOTITE_CCD_PATH=$TASK/ccd/components.bcif`, the existing `BLENDER_RENDER_SITE` and configured Molecular Nodes extension environment. Every local worker was dedicated to this task and explicitly stopped.

| Exact command / check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` during development | 155 passed / 4 warnings, then 172 passed / 4 warnings. These intermediate runs preceded the final pocket-residue test. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_reference_presets.py tests/test_spec.py` | 53 passed, 2 warnings, 1.76s before the final additional test. |
| Final `.venv/bin/python -m pytest -q -p no:cacheprovider` | **173 passed, 4 warnings, 3.94s**. Log: `$TASK/pytest-final.log`. |
| `"$BLENDER" --background --python-use-system-env --python-exit-code 1 --python scripts/validate_reference_render.py -- "$TASK/blender-checks-final"` | Passed: native nucleic thickness changes geometry without mutating the shared default; ordinary/procedural cut surfaces stay closed with unchanged interiors; four perspective/orthographic landscape/portrait fits retain all vertices; explicit focus/scale; actual gradient and frosted-opacity renders. JSON: `$TASK/blender-checks-final/checks.json`. |
| `.venv/bin/python "$TASK/render_final.py"` | Eight actual **1600-pixel-wide / 128-sample** Cycles PNGs. The first two driver setup assertions failed before rendering because they omitted resolver-added transport defaults; correcting those expectations allowed all eight to render. |
| `.venv/bin/python "$TASK/render_pockets.py"` | Three actual public-8EA6 candidate renders at 1100 × 737 / 48 samples. All geometry audits passed; images compared before choosing the final opening. |
| `.venv/bin/python "$TASK/render_final.py" 2 7` | Final revised cutaway and public 8AA5 gallery replacement passed at 1600 pixels / 128 samples. Actual atom-color, complete-residue and interior-preservation assertions passed. |
| `.venv/bin/python "$TASK/render_legacy_gallery.py"` | Six actual legacy-preset PNGs at **960 × 720 / 64 samples**. Pastel and green retain cartoon/native sticks and no atom spheres. All 14 gallery outputs requested PNG only, and no scene was returned. |
| `.venv/bin/python "$TASK/package_gallery.py"` | All 14 README thumbnail links and JSON links exist; every catalog identifier appears; public source and SHA-256 records written. Contact sheet visually inspected. |
| `.venv/bin/python "$TASK/build_wheel.py"` then `.venv/bin/python -m build --wheel --outdir "$TASK/wheels" "$TASK/wheel-source"` | Final isolated build passed. No build dependencies installed into the existing dev environment. |
| `"$TASK/installed-env/bin/python" -m pip install --no-deps --no-index "$TASK/wheels/pymol_plugin_bridge-0.2.0b1-py3-none-any.whl"` | Passed in a fresh temporary venv. |
| `"$TASK/installed-env/bin/python" -m pytest -q -p no:cacheprovider "$TASK/wheel-check"` | **83 passed, 2 warnings, 0.73s**, outside the checkout. CWD is the installed site; PYTHONPATH puts that site before the existing dev dependencies. Imported core path was verified inside the temporary venv. This is not a clean dependency-install test. |
| Python `zipfile`, SHA-256 and AST comparison | All **65 package Python/JSON files** match the wheel exactly; Python parses; legacy presets, plan and instructions remain unchanged. Wheel SHA-256 `e9933992ca3d80d5dc40c397368bd5adc46222be654604f84dfe65307b835bac`. |
| `git diff --check` and source/gallery review | Passed. Final review checks links, source provenance, isolated cuts, camera semantics and explicit scene behavior. |

Failures and limitations remain distinct. The inherited four dependency deprecation warnings are unchanged. Factory-startup validator attempts could not import `databpy`; using the configured extension environment then exposed a missing CCD database. Those failed attempts are not passes. A trial exercised the existing CCD recovery fallback and populated runtime/shared CCD caches; their prior contents were not fully recorded, so this task does **not** claim those caches stayed unchanged. The explicit CCD override now targets only task-local storage, and final runs used it. No running production service, credentials, privacy settings, budgets or application configuration were changed. PSE conversion ran under a short-lived OS sandbox with no network/credentials and writes limited to task temporary state; the existing PyMOL printed a missing-license message in that empty home, but export completed successfully. No new PyMOL was installed.

No unresolved new test failures remain. There is no fresh Slurm allocation, Modal render, deployment, publication, remote push or installed Codex/ChatGPT result-flow acceptance. Presets 1, 5 and 6 are intentionally labeled approximations rather than perfect matches. The proposed next bounded task is to establish their exact source structures and refine only those three reference matches; Milestone B remains a separate, unstarted task. Stop here for review.


Final appearance comparison: `.venv/bin/python "$TASK/compare_legacy.py"` rendered `preset_5_black_interface` using a temporary copy of the starting commit's renderer, without checking out or changing the repository. The first attempt lacked the Python package directory layout and failed to import `blender_render`; correcting the temporary layout allowed the comparison to pass. Current versus starting image differs in one channel value by 1/255 (mean absolute difference **0.000000361690** on the 0–255 scale). Its dark appearance is inherited. `legacy-baseline-check.json` records the result. A separate attempt to compare supplied PSE file hashes could not proceed because the initial exported payloads did not store source-file hashes; that retrospective hash check remains unverified. Conversion only had read access to those files, and no save/overwrite operation was performed on them.

## Follow-up: glow, thicker pocket sticks and glass refinement (2026-09-07)

The requested visual refinement stays within the existing shared renderer. The cutaway ligand now declares an emission-backed fog-glow pass in the shared schema and compositor, while keeping its pink material below clipping. The pocket sidechain sticks remain element-colored and use radius `0.3` so the complete nearby residues read through the opening. The ivory/lime shell and cyan nucleic acids use higher transmission, lower frosted opacity and a finer, very light grid; the grid and glass controls remain explicit preset values rather than hidden renderer defaults.

The two affected presets were rendered again through the local Blender 5.0.1 / Molecular Nodes 4.5.10 worker with the task-local CCD path. The verified outputs are `02_cyan_mint_cutaway.png` and `06_ivory_lime_frosted.png` in the task's temporary final directory; their checked-in thumbnails and gallery SHA-256 records were regenerated from those outputs. The actual renders preserve the cutaway interior and show colored sidechains, a controlled pink ligand halo, translucent cyan DNA and the pale frosted shell.

| Exact command / check | Result |
|---|---|
| `BLENDER_RENDER_HOME=/private/tmp/blender-reference-presets/config BLENDER_RENDER_SITE=/Users/mb/Documents/blender_bridge-release/.validation/config/runtime/site .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_reference_presets.py tests/test_spec.py tests/test_style_schema.py` | **81 passed, 2 warnings**, 0.88s. |
| `PRESS_BIOTITE_CCD_PATH=/private/tmp/blender-reference-presets/ccd/components.bcif BLENDER_RENDER_HOME=/private/tmp/blender-reference-presets/config BLENDER_RENDER_SITE=/Users/mb/Documents/blender_bridge-release/.validation/config/runtime/site PYTHONPATH=/Users/mb/Library/Application\ Support/Blender/5.0/extensions/blender_org .venv/bin/python /private/tmp/blender-reference-presets/render_final.py 2 6` | **Both renders passed**. Cyan/mint: 1600 x 1072, 128 samples, style SHA-256 `eb752c5112c634212686b736788e9e17370a98f227e707dd9e31af25c8d504b2`, PNG SHA-256 `b3dc3df812f9a411c8e9f7ed64af1519c88fb1d51b7af2f85ef1654e5b5d03fb`. Ivory/lime: 1600 x 900, 128 samples, style SHA-256 `7adf4d94ae473ec79d8c69823306cb803b6394a814bbebf724dfe4c5e6562bf7`, PNG SHA-256 `a0217c843fc820c6bb1a9e5cd856d8c79b3af5f6e4feeac8e36d250bcd3122b7`. |
| `.venv/bin/python /private/tmp/blender-reference-presets/package_gallery.py` | **Passed**. Regenerated the two thumbnails, `examples/presets/gallery.json`, `examples/presets/cutaway_checks.json` and the isolated delivery bundle. |
| `.venv/bin/python /private/tmp/blender-reference-presets/build_wheel.py && .venv/bin/python -m build --wheel --outdir /private/tmp/blender-reference-presets/wheels /private/tmp/blender-reference-presets/wheel-source` | **Passed**. Fresh isolated wheel `pymol_plugin_bridge-0.2.0b1-py3-none-any.whl` built without changing the development environment. |
| `PYTHONPATH=/private/tmp/blender-reference-presets/installed-env/lib/python3.11/site-packages /Users/mb/Documents/blender_bridge-release/.venv/bin/python -m pytest -q -p no:cacheprovider /private/tmp/blender-reference-presets/wheel-check` from the installed site directory | **83 passed, 2 warnings**, 0.79s. The temporary installed environment itself has no pytest package, so the existing development pytest runner was used with the installed wheel first on `PYTHONPATH`; this is an installed-wheel check, not a clean dependency-install test. |
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **174 passed, 4 warnings**, 4.50s. Warnings are inherited dependency deprecations; no new test failures. |
| `git diff --check` | **Passed** before the final commit review. |

No new remote, Slurm, Modal, production-service, credential or deployment checks were performed. The supplied references remain visual targets rather than exact pixel-match claims; the original structure identity for references 1, 5 and 6 remains unresolved. The next bounded task is a final full-suite/build verification of this refinement, then stop for review.
