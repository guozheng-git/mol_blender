# C3 exact-commit local and Slurm acceptance — 2026-09-10

Acceptance and paired visual inspection passed for rendered candidate
`8741242d585dc4cf0a0ef76ca96c78e892a7cea4` (`CANDIDATE_SHA`). The unchanged
acceptance runner was invoked once, with one local host-Blender render and one
private Slurm/Apptainer allocation and render. No developer preview, Modal action,
retry, recovery, replay, or replacement allocation occurred.

The documentation-only commit introducing this record (`EVIDENCE_SHA`) has
`CANDIDATE_SHA` as its direct parent. The renders ran at the candidate, not at the
later evidence commit. No renderer, runtime, launcher, or acceptance-runner source
was changed for this run. Consolidation is a subsequent Git operation; its exact
evidence and archived-main commit IDs are recorded in PR #1 and the completion
report rather than implying that the evidence commit itself was rendered.

## Preparation and command record

Fresh detached local and shared-remote worktrees were created at the candidate.
Both passed exact-commit, reviewed-checkout cleanliness and sealed-engine checks.
Only `remote_root`, `expected_commit` and `expected_engine` changed in the isolated
`c3-ipd` profile. The existing Blender, PyMOL interpreter, pinned runtime, CCD,
SIF, remote Python/runtime, Slurm transport, qlogin resource arguments and long
private NFS registry base were preserved. No replacement runtime was installed.

`doctor --profile c3-ipd`, the absent validation-registry check and prerequisites-only
acceptance passed before allocation. The established external scheduler guard was
recreated without changing its submission semantics: an intercepted first call
was forwarded unchanged, and a second returned exit 78 without forwarding. That
test made no scheduler submission. The real guard had a fresh, unused exclusive
marker; the acceptance run consumed it once.

Commands below preserve the executed arguments, with private machine paths
represented by variables. The verbatim command, environment references, worktree
identities and guard settings remain in the private evidence record.

```sh
CANDIDATE_SHA=8741242d585dc4cf0a0ef76ca96c78e892a7cea4
cd "$CANDIDATE_WORKTREE"
git rev-parse HEAD
git status --short
"$C3_PYTHON" -m blender_render.cli doctor --profile c3-ipd
"$C3_PYTHON" scripts/validate_c3_acceptance.py \
  --commit "$CANDIDATE_SHA" --profile c3-ipd --blender "$BLENDER" \
  --output "$EVIDENCE_ROOT/prerequisites" --prerequisites-only
"$C3_PYTHON" scripts/validate_c3_acceptance.py \
  --commit "$CANDIDATE_SHA" --profile c3-ipd --blender "$BLENDER" \
  --output "$EVIDENCE_ROOT/acceptance"
```

The isolated `BLENDER_RENDER_HOME`, pinned `PRESS_BIOTITE_CCD_PATH`, candidate
`PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1` and the established external guard's PATH
were set for execution. Allocated-node preflight verified the candidate and
required shared paths before starting the service. The runner exited 0 after
131.765 seconds. No direct GPU-node SSH outside the allocation was used.

## Immutable identities and molecular content

| Identity | SHA-256 |
|---|---|
| Engine build, both workers and candidate | `37c731a7ceebeade49213b9399837dfe6fabc6eab2f3dd728c408ddca84cbd58` |
| Frozen StructureBundle, both backends | `aa6dff5fb2306af65a8d86f5b2e94edfa7c65546db2db175fb4a03646ea5a4ee` |
| Frozen RenderSpec, both backends | `03687d5885e359593ade052e3bfcc4ea39b92e3af6a110fb37519ce211729f0b` |
| Locked and captured CCD, both workers | `dffdb3a19600d51854058daecfbc9bd1b94aab413a04e7afed8d4b7670621d47` |

The public `examples/1sps_pose.pse` was exported once with DSS and selection
`(chain A) or (chain D)`, preserving enabled content and the saved PyMOL camera.
Assertions confirmed protein chain A, peptide chain D and PTR before dispatch.
The frozen bundle contains **1,106 atoms and 110 residues**, with chain IDs A and
D. Both manifests report this same summary and the fixed identities above.

Both workers verified Blender 5.0.1, Python 3.11, Molecular Nodes 4.5.10,
Biotite 1.6.0 and NumPy 1.26.4. CCD evidence reports capture, effective activation
and hash verification. The RenderSpec requested `GPU/AUTO/require_gpu=true`;
both actual devices were CUDA GPUs with no CPU fallback.

## Operations and output

| Evidence | Local host | Slurm/Apptainer |
|---|---|---|
| Job ID | `2985a7b2984355c57a6c3c18916e2b07` | `da01e86e31d4aacf8d7b1d2ec56f19cc` |
| Operation alias | `validation-local` | `validation-slurm` |
| Logical operations | 1 | 1 |
| Leaf renders | 1 | 1 |
| Final lifecycle | delivered | delivered |
| Actual device | CUDA, NVIDIA GeForce RTX 4080 SUPER | CUDA, NVIDIA RTX A4000 |
| PNG dimensions | 512 × 384 | 512 × 384 |
| PNG bytes | 237140 | 237140 |
| PNG SHA-256 | `7baa9afcb7c2fd8cad21fe90b70780d65579a5f67754eb21cbc32d60c640ffb7` | `832dcccc365b3b79de2d54f1f66fda54a199665c46dea1f4e1f13e3fe4b53d6f` |

The single validation allocation was **23432476**, session
`b61e37a3db2e9ba9785da65c`. Manifest worker-render counters and exactly one
`Custom render complete:` log entry per backend corroborate leaf-render counts;
read-only database checks corroborate logical-operation counts. These direct
backend submissions did not create gateway child operations.

Both PNGs decode successfully. The runner used `preset_1_interface` and rough
performance settings; the actual output dimensions above reflect that scaling.
Both manifests confirm `mn_cartoon` protein and `mn_native_sticks` ligand output,
with ligand atom spheres disabled. No `.blend` was produced.

## Timings

Each record has `phase`, `hop`, `status`, `duration_ms` and `attempt`. All attempts
are 1; statuses are executed except conversion, which was skipped because the
same already-exported bundle was submitted. Durations below are milliseconds,
measured by the owning process, without cross-machine timestamp subtraction.

| Phase / hop | Local | Slurm |
|---|---:|---:|
| queue / controller | 8.692 | 99.066 |
| conversion / converter (skipped) | 0 | 0 |
| startup / worker | 2414.423 | 27079.058 |
| scene_build / worker | 4379.485 | 3641.662 |
| rendering / worker | 1850.278 | 1369.536 |
| artifact_preparation / worker | 0.654 | 1.349 |
| artifact_preparation / controller | 1.557 | 41.423 |
| delivery / client | 16.486 | 929.120 |

The stored timing records contain no molecular data, credentials, private paths or
signed URLs. Scheduler accounting is retained separately; the table does not
claim to measure allocation time from timestamps on different machines.

## IPC, durable storage and cleanup

The fresh registry remained beneath the same long private NFS base. Its durable
path was **88 bytes**; the former registry-derived worker socket would have been
**115 bytes**. Logs and the job database remained under their configured durable
roots, with private read-only backups retained for evidence.

The outer Slurm supervisor recorded `base_provisioned=true`,
`bind_configured=true`, `container_verified=true` and `base_kind=tmp`. The nonce-bound
container check verified the explicit same-path read/write bind through
`apptainer exec --nv --cleanenv`. The actual inner worker socket was **43 bytes**,
its directory mode was **0700**, and `durable_root_independent=true`. Inner worker
and outer base both recorded `cleanup_complete=true` after child exit. Absolute
node-local paths are intentionally absent from this public record.

The local worker used a **33-byte** socket in a mode-0700 directory. A read-only
process observer saw the socket, confirmed worker exit and confirmed directory
removal. The validation runner, observer, worker and tunnel processes exited;
validation credential files were removed. The remote session reported stopped,
`squeue` contained no entry for allocation 23432476, and all recorded accounting
steps were terminal. The allocation was explicitly cancelled after successful
delivery; the supervised service exit was -15. Startup readiness probes and the
terminated service step include nonzero scheduler step statuses, which do not
represent an additional render or a failed molecular output.

## Visual inspection and retained evidence

Both PNGs were opened and inspected together after cleanup. **Paired inspection
passed**: matching orientation, camera framing and content placement; visible
protein cartoon secondary structure and native stick content; no obvious clipping,
missing counterpart or camera mismatch. Molecular A/D/peptide/PTR identity is
established by the export assertions and manifests, not by visual atom counting.
The expected rough-render grain remains. Pixel identity is not required across
the two GPUs, and the PNG checksums differ.

Private evidence identifier: `acceptance-8741242-y2pny9ue`. Retained files include
the verbatim runner command; passive and guard checks; prerequisites output;
`acceptance.json`, `execution.json`, both PNGs/manifests and service logs;
`outer-ipc.json`, local IPC observations, allocation/session and scheduler cleanup
records; read-only database backups; `verified-evidence.json`; and
`visual-inspection.json`. Raw private logs, paths and configuration are not copied
into this documentation. The runner's own JSON correctly leaves visual inspection
as an external step; the separate visual record supplies that completed gate.

Historical operations `25fd27c98fe342402d13ea80bdc4d7e2`,
`e22fcd3f187eecd120c0c7cd8427f297` and `b38089ce229125e37e9325add58be12d`
were not recovered or modified. Their database hashes and unrelated `1qys.cif`
remain unchanged.

## Remaining unverified scenarios

These two successful renders establish this candidate's local Linux CUDA and
private Slurm CUDA parity and normal owned cleanup. They do not execute macOS
Metal acceptance, lost-response or partial-transfer recovery, acknowledgement
loss, older-child recovery after controller upgrade, mid-render cancellation,
storage failure, source mutation or stale-worker replacement, node loss/SIGKILL
cleanup, budget/expiry stress, or public/hosted UI flows. Applicable simulated
contracts remain separate evidence; historical failed operations remain preserved.
No D work, deployment, SIF rebuild or hosted-policy change occurred.

**Modal implemented and contract-tested; real backend execution unverified.**
