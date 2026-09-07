# Blender Render implementation progress

## Starting point and scope

- Started 2026-09-07 on `codex/public-release`, HEAD `b1bb7d1c5d0d832b75c096eeea855b306815d79f`, matching the live private draft PR #1 head. No checkout, reset, commit, push or deployment.
- Starting `git status --short`: only `?? docs/plan/`. No tracked edits. This linked worktree has no configured Git remote; the GitHub connector verified `magnusbauer/blender_render` and read all 19 review threads and four review submissions.
- The supplied plan existed at `docs/plan/blender_render_implementation_plan_v3.md`, not the requested path. It is preserved unchanged; `docs/plans/blender-render-v3.md` is a byte-identical copy. Read all 605 lines. Read root AGENTS.md in full; no applicable nested AGENTS.md found.
- Scope: Milestone A only, local isolated tests. No Slurm allocation, Modal submission, production service changes, credential/privacy/budget changes, publication, or release acceptance claims.
- Reuse `export.py` bundles, `style.schema.json`, all existing preset JSON, `core.py`, `rendering.py`, `blender_custom_render.py`, queue/worker/backend implementations. No replacement framework or file-tree migration.

## Milestones

- [x] A implementation and local contract validation: shared schema, built-in catalog, JSON/override resolution, camera precedence, identity and snapshot/hash. Fresh renderer acceptance remains unverified (missing runtime).
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

**Not executed / unverified:** fresh local PNG/scene or camera visual comparison, fresh PSE export, Slurm, Modal, production restart/deployment, regenerated SIF, clean full dependency install, installed ChatGPT/Codex attachment/display, and all B–E acceptance scenarios. Missing runtime does not count as a passed renderer gate. No additional render was attempted to compensate for the probe failure.

## Decisions, conflicts and next task

- Schema remains version 1.0; full style documents remain supported. Objects merge recursively; arrays replace wholesale. Preset identity is provenance, never sufficient to impose representation gates.
- Saved-pose preservation is a default for preset execution. Explicit full-JSON camera and explicit camera overrides win; preserve_view=false selects the preset camera. Explicit dimensions win over viewport-derived defaults. Resource policy is applied after resolution.
- Keep PNG output default and scene creation explicit, including save_blend inside a complete JSON/override document. Transport filenames remain controlled by the service.
- Root AGENTS requires preset 1 cartoon/native sticks; preserve these checks for the actual cartoon/sticks specification, without applying them to a surface-only custom representation. No security requirements weakened.
- Request snapshot metadata changes normalized job records. Existing legacy requests are translated, but historic stored fingerprints and already-running older workers were not migrated or validated; any future upgrade must follow the existing drain/stop rule. No running service was modified.
- The plan's broader hosted allowlist/capability negotiation, fit-without-saved-pose and user/project catalog persistence need later work; A must not pretend static metadata is a live runtime probe.
- No genuine instruction conflict required weakening security or release gates. The requested canonical plan path was supplied without moving the user-owned original.
- Next bounded task after A: Milestone B's local inspect/validate/save/reuse and fresh-process preview foundation, with source identity and isolated public fixtures. Stop for review before starting it.

Stopped after Milestone A. At the user's follow-up request, this milestone is committed on `codex/milestone-a-json-contracts`, branched from the starting HEAD. The original user-owned `docs/plan/` remains untracked and unchanged. PR threads were read but not modified; no remote push or PR update is included.
