# C3 execution and recovery contract

Normalized admission binds a canonical bundle and resolved RenderSpec in one
transaction. Raw admission returns `conversion_pending` with null digests, captures
the preset/options bytes, then binds after isolated conversion. C1 fingerprints
and operation keys retain their algorithms. Transient run metadata and file
locations are outside the bundle digest. Terminal cleanup drops molecular and
style snapshots while retaining digests and provenance.

Controllers use `/internal/v1/resolved` with a separate controller credential.
The envelope requires version, controller, scoped owner, operation, bundle digest,
resolved request, preset provenance, requirements and expected engine. Consumer
credentials and OAuth cannot invoke this route. Read/ack requests from controllers
carry `X-Render-Owner`; the receiving service scopes ownership by authenticated
controller plus owner. Configure `serve --controller-token-file` separately from
`--token-file`; HTTPS profiles use `controller_token_file` or
`controller_token_env` and must explicitly declare `private: true`. No ordinary
caller can select another owner's scope; gateway data cannot transit an ordinary
shared endpoint even when that endpoint would subsequently reject authentication.

The receiving controller validates the structure, schemas, digests, requirements
and receiving resource/security policy without resolving or editing the snapshots.
Policy conflicts fail. New execution requires the exact admitted engine build.
Artifact retrieval after a clean controller upgrade validates the original child
identity without requiring that build to equal the recovering controller's build.
Execution and stored-integrity validation are separate paths. Stored envelope
version 1 retains its canonical JSON/hash rules and original engine declaration;
recovery never invokes the current bundle/style consumer, preset catalog or
capability derivation. Its owner, parent operation and child/lookup identities
come from durable controller state and authenticated retrieval. Unsupported or
inconsistent stored evidence preserves the operation as `recovery_required`.

## Inventory and protected actions

`engine.package_inventory` recursively identifies runtime package inputs by
normalized package-relative POSIX paths. All regular package files participate,
including Python/native modules, schemas, presets, locks, scripts, shader/geometry
assets, and packaged CCD when present. Included symlinks, path collisions and
root escapes are rejected. Exclusions are `__pycache__`, `.pytest_cache`,
`.mypy_cache`, `.ruff_cache`, `render_outputs`, and `.pyc`, `.pyo`, `.tmp`, `.log`
and `.sif` files. SIFs are external launcher artifacts, not packaged engine inputs.
External user/project presets are not package assets. The generated engine
manifest is verified separately to avoid self-hashing.

The broader reviewed-checkout input set additionally includes `setup.py`,
`pyproject.toml`, README, LICENSE, THIRD_PARTY_NOTICES, LICENSES, and the C3
acceptance/contract runners. These top-level inputs do not change engine_build
unless packaged or consumed by the running engine. Packaging, staging, startup,
mutation checks and recursive source/wheel/install comparisons share the runtime
inventory; local and remote checkout checks share the broader scope definition.

Startup compares the installed manifest with actual file hashes and declarations.
Source processes seal the same inventory. Protected actions verify actual bytes,
load their explicit `ACTION_MODULES` dependency closure and captured resources,
then check again. Controller closures contain no Blender or provider SDK imports
for inspection. Worker rendering loads the scene implementation in the worker.
Package resources are captured by the process seal; request data remains the
operation's immutable bundle/spec and private staging. A verification failure
latches the process; it never refreshes its cached identity in place.
Startup captures both physical and invocation package-root paths. Resource lookup
classifies those paths before following mutable links, and retargeting a captured
root alias latches the process.
Image staging checks the current process seal before selecting a package capture,
including when its requested root is a retargeted alias or a different package.
Durable transactions share the seal's synchronization lock: detection before
admission rejects the new job/reservation, while detection after its commit
preserves the admitted operation. Conversion reservation uses the same gate.

Action preloads compile captured Python bytes after checking integrity; included
native modules load from a private captured package. Local controllers, converters
and Blender workers launch from private package captures, including the generated
manifest and nested resources. Workers keep their capture until termination.
Remote helpers receive a captured verifier by value, verify the remote installation,
then execute its captured helper. Allocation scripts reference a session-owned
package; local and remote proxies receive captured code and command arguments by
value. Captures are never refreshed or overwritten. Service/allocation captures
remain private lifecycle state for later administrative cleanup; per-conversion
and stopped-worker captures are removed. Configured runtime executables, external
dependencies and SIF images remain explicit setup prerequisites.
Administrative status/stop and attachment use the controller's captured stdlib
helper by value; they can reach the existing registry even if the unused remote
installation is damaged. Attachment still requires a clean controller and
authenticated supported retrieval protocol. New start/identity actions verify
the remote installation. Modal CLI staging and deferred image uploads use the
same verified private package capture, excluding caches/SIFs and rejecting links.

## Integrity and durable operations

`restart_required` is process-local and independent of persistent operator drain.
It closes new execution admission and pauses execution, conversion, startup,
child submission and new transfer work. Passive status, cancellation/deletion,
and already verified local artifact delivery remain available. Restart does not
reopen operator-drained admission. Existing drain semantics still allow admitted
work to drain when the process itself is clean.

Lifecycle states remain queued/running/transfer_pending/terminal. Additive
transactional migrations introduce orthogonal blocked_condition, resume_phase,
and dispatch/recovery/timing metadata. Existing IDs, aliases, molecular snapshots,
reservations, inventories, tombstones and receipts survive. A block alone does
not release a reservation or introduce a new expiry policy.

| Owner-visible condition | Next action |
| --- | --- |
| restart_required | restart_service |
| compatibility_blocked | restore_exact_engine |
| execution_unknown | inspect_execution |
| recovery_required | recover_operation |

A clean restart evaluates saved phase and execution identity before resuming the
same operation. A known child is observed/recovered, never replaced. An older row
without sufficient phase evidence remains recovery_required. An ambiguous leaf
dispatch remains execution_unknown without automatic replay. A completed child
retains transfer_pending while suspended. Conversion reservations are idempotent
per admitted operation. Existing explicit cancellation/cleanup/expiry still apply.
Legacy child identity alone is insufficient to resume without its saved execution
envelope and phase. Cancellation racing with a compatibility or integrity block
settles once; ambiguous execution conservatively consumes the reservation.

A process latch pauses all execution. Operation-specific build blocks are excluded
from runnable claiming; FIFO remains strict among runnable operations. Blocks
retain original submission metadata and cannot spin through claim/requeue.
Worker-only integrity failures permit replacement with a newly sealed worker
while the controller is clean. Worker replacement cannot clear a controller latch.

Shared `Client.wait()` stops immediately on `restart_required`,
`compatibility_blocked`, `execution_unknown`, or `recovery_required`. Its typed
409 `ClientError` retains the condition code, job ID and next action, including
in the message shown by CLI and PyMOL. Waiting never clears a block, cancels or
resubmits. After explicit resolution, callers can wait on the same operation.

CCD is provisioned during explicit setup/image preparation. The actual Blender
daemon captures it before readiness: PRESS_BIOTITE_CCD_PATH takes precedence at
startup, otherwise Biotite's configured path is selected. It opens the source
with O_NOFOLLOW, requires a regular file and verifies the locked SHA-256. Those
bytes are written to a new mode-0400 file in a mode-0700 worker-private directory.
Biotite's setter must succeed and its effective configured path must equal the
capture. Setter failure or ineffective activation prevents readiness.
Readiness, final daemon preflight and the renderer entry guard check only this
captured file and its activation. Rendering never reopens or reactivates the
original source; replacing that source cannot change a running worker's CCD.
The capture lives in the worker's owned temporary state and is removed on
shutdown/startup failure, or with its package capture after forced termination.
The standalone Blender script also captures before rendering and cleans up on
exit. Runtime execution never provisions, repairs or overwrites the source CCD.
The Milestone B developer Blender entrypoint likewise initializes worker_ccd
before invoking the shared renderer. It requires the preview's CCD checksum to
equal runtime.lock.json, selects cfg's already private CCD instead of any inherited
caller runtime path, and places the worker capture inside the preview temporary
root. A finally block closes it after rendering and development source/manifest
evidence, including failures. Source snapshot and provenance checks remain active.
Controllers declare requirements without importing Blender runtime dependencies
to inspect worker files. Private diagnostics report captured path/hash and
activation evidence.
Cold readiness, daemon preflight and the renderer's entry CCD guard return typed
compatibility refusals through worker IPC. The admitted operation keeps its
snapshots and reservation; an
untyped worker exit retains an unknown execution outcome. File-import endpoints
also preserve integrity/compatibility conditions instead of labelling them as
malformed molecular structures.

## Worker IPC lifetime

The worker's durable root holds logs and package captures. Its Unix socket uses
an independent, newly created `br-*` directory with mode 0700 and a `w.sock`
filename. A canonical, user-owned, mode-0700 writable `XDG_RUNTIME_DIR` is
preferred when it contains no Apptainer bind delimiters (`:` or `,`) and the
complete socket pathname fits within 100 encoded bytes;
otherwise the worker uses node-local `/tmp`. `TMPDIR` and durable registry length
do not select the socket path. Existing directories and stale sockets are never
adopted. No short-path limit is imposed on a durable registry.

IPC is created before command construction and retained until child exit is
observed. Startup/capture/command/readiness failures, interrupted startup, normal
stop, forced termination and abandoned replacement attempts release it. A restart
gets a fresh directory. Shutdown does not move or remove durable logs. Private
status reports a socket only while the worker process is active. When the manager
launches an outer Apptainer command, the IPC directory is explicitly bound at the
same path; a controller already inside a container uses its own runtime directory.

IPC creation failure is a typed pre-execution readiness refusal: it starts no
Blender process and preserves the admitted operation through the existing
compatibility-blocked contract. It does not change ambiguous historical jobs.
Acceptance preflight creates and removes a probe directory and reports the
effective `worker_ipc` strategy, permissions and pathname length. It does not bind
a socket or launch compute. Real Blender/container IPC acceptance for the corrected
build remains a separate gate.

## HPC profiles and lifecycle

Profiles require `version: 2` and `launcher: {kind, arguments}`. Migrate old
`allocation` arrays to `launcher.arguments` and explicitly choose `slurm_sbatch`
or `ipd_qlogin`. There is no inferred scheduling behavior. Packaged templates are
`profile_slurm.json` and `profile_ipd.json`. Sbatch receives a mode-0700 script
as argv and retains its optional cluster identity. Ambiguous submissions require
operator inspection; they cannot silently create a new allocation.

| Action | Behavior |
| --- | --- |
| `doctor --profile NAME` | Configuration and cached data only |
| `service status --profile NAME` | Existing-service read; no repair/tunnel/allocation |
| `service attach --profile NAME` | Attach and explicitly create the requested tunnel; never allocate |
| `service start --profile NAME` | Explicit service/allocation start |
| Render | Durable controller admission precedes conversion/allocation/dispatch |

Public /health and /healthz contain aggregate status only. Authenticated
/v1/capabilities supplies controller declarations and sanitized cached worker
observations. Private authenticated worker readiness supplies negotiation evidence.
HTTP workers independently enforce explicit public atom/byte limits before backend
construction (defaults: 100,000 atoms and 50 MiB). Doctor reads sanitized connection
cache data only; it never probes or repairs the service.
Authenticated HTTP worker responses preserve restart/compatibility/unknown
conditions through the controller hop. An ambiguous response after submission
retains the operation for recovery instead of declaring a generic render failure.

ConnectionIntention separates inspect_cached (local cache reads), attach_existing
(authenticated existing-service probes/tunnel recreation, no allocation/submission),
and start_or_allocate (explicit start or post-admission execution). Legacy flags
translate at one boundary; contradictions fail before external actions.

| Launcher | Transport | Semantics |
| --- | --- | --- |
| slurm_sbatch | slurm | Private sbatch script; srun proxy in recorded allocation |
| slurm_sbatch | ssh | Private sbatch script; ProxyJump to recorded GPU node |
| ipd_qlogin | slurm | PTY/qlogin launcher; srun proxy using published Slurm job ID |
| ipd_qlogin | ssh | PTY/qlogin launcher; ProxyJump to published GPU node |

Missing/unknown transports or launchers fail profile validation. Scheduler cluster
identity, credentials, locking and ambiguous-submission fences remain intact.
Controller declarations and worker observations do not promise GPU availability until a worker actually selects
a compatible device. `GPU/AUTO/require_gpu=true` allows host Metal and Slurm CUDA
without rewriting the specification. Workers record actual device/hardware.

## Gateway results

`child_operation_id` and `child_completed` distinguish execution from delivery.
A `transfer_pending` parent reports `child_observation_pending`,
`child_transfer_pending`, or `artifact_preparation_failed`; it does not claim that
the child render failed. POST `/v1/jobs/{id}/recover-transfer` queues only recovery
of this child. Status/result reads do not start a service or enqueue compute.
Controller restart recovers this same child using attach_existing, including
recreating a dead tunnel. Current-build equality gates new execution, not
retrieval of an older completed child through the supported protocol. An unknown initial submission is
looked up by operation key; a missing lookup is not permission to resubmit.
The initial invocation also switches to attach_existing immediately after binding
the child ID. General status/download requests never inherit allocation permission.

Verified bytes and receipts live in the controller's durable state, and its sync
boundary completes before each acknowledgement. Acknowledgement loss retries the
ack, not the download when local bytes still verify. Child render failure, missing
required child artifacts, parent preparation failure and parent result readiness
remain distinct. Child expiry/tombstones cannot be overridden by stale remote
copies. Pending transfer expiry and explicit cancellation purge staging. State
volume loss is not recoverable without a surviving verified copy.
Integrity is checked before each new inventory, artifact and acknowledgement
request. A latch between artifacts preserves already verified bytes and receipts.
The same check precedes a request that resumes transfer at a downstream gateway.
Result job identity and immutable artifact inventory must agree with prior
receipts. Availability may change; a changed checksum, required-artifact set,
protocol or child identity cannot overwrite previously bound evidence or trigger
new acknowledgements. Older C3 receipts retain compatibility through their
already-bound outer child job ID.

Timings contain only `phase`, `hop`, `status`, `duration_ms`, `attempt`. Status is
one of executed/skipped/cached/unavailable/interrupted. Molecular data, credentials,
private paths and signed URLs cannot enter these records. Each process uses its
own monotonic clock. No cross-machine timestamp subtraction is used. Child timing
imports persist child ID, sanitized-list SHA-256, import time and record count in
one transaction. The first set is imported once. A later differing set records
timing_integrity_conflict while retaining the first set and continuing valid
artifact recovery. Transfer/delivery attempts are separate and excluded from the
child digest; a timing conflict never authorizes cancellation or rerendering.
The original sanitized child set is also retained inside its import marker, even
when the bounded aggregate timing window rotates. Restart transfer attempts
increment independently of that marker.

## Validation

Release hardening deferred from this narrow blocker correction: distinguish
explicit source/developer mode from manifest-required installed/release mode
(including partial runtime-image bootstrap), capture passive public page handlers
and refresh capabilities integrity diagnostics, and align input-in-use/queue
position reporting with transfer and blocked-operation states. These remain
requirements before E or public deployment, not executed release gates.

Prepare test dependencies explicitly, then run
`python scripts/validate_c3_contracts.py --output /temporary/outside-checkout`.
It runs the full guarded repository suite, builds offline, installs the wheel
outside the checkout, asserts import locations and verifies every packaged file
against the source and installed bytes. Installed contracts use prepared test
dependencies, not a clean dependency installation. The audit loads before
application imports and fails even when a forbidden boundary attempt was caught.
The partial Modal image setup contract imports its copied runtime bootstrap in
process and stops at a fake installer boundary. The provider-free conversion
helper is preloaded before protection finishes, and sandbox creation checks
integrity again. These contracts do not build or deploy a provider image.

Real acceptance is not authorized in the correction checkpoint. For a separately
authorized acceptance run, prefer an immutable clean worktree or exact verified
wheel. Mutable-checkout guards check tracked and relevant untracked/ignored inputs
locally and remotely; unrelated molecular fixtures outside the input scope are
permitted. After the required review and authorization, run `python scripts/validate_c3_acceptance.py --commit
EXACT_COMMIT --profile PRIVATE_PROFILE --output /temporary/acceptance`. The runner
checks prerequisites first, asserts chains A/D, protein/peptide and PTR before
one DSS export from `examples/1sps_pose.pse`, freezes one bundle/spec pair and uses
isolated host-Blender and private Slurm services. It checks operation/render counts,
digests, build, summary, devices, PNG, cartoon and native sticks, with no scene.
Visually inspect both PNGs afterward. Missing local host runtime is a blocker;
container execution cannot satisfy host acceptance. Successful renders do not
prove unexecuted remote recovery/stress scenarios.

Modal implemented and contract-tested; real backend execution unverified.
