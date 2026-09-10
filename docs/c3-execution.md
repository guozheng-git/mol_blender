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
Policy conflicts fail. Exact builds are required in C3. The installed wheel
contains `engine-manifest.json`; source processes calculate and cache the identity
once. Workers report the identity loaded at startup and cached runtime versions.
A stale warm worker is stopped and restarted once before dispatch. A mismatched
replacement or unsupported requirement fails explicitly. Runtime setup and CCD
provisioning are explicit setup/image activities, never ordinary render actions.

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

Health and capabilities distinguish controller declarations from loaded worker
observations. They do not promise GPU availability until a worker actually selects
a compatible device. `GPU/AUTO/require_gpu=true` allows host Metal and Slurm CUDA
without rewriting the specification. Workers record actual device/hardware.

## Gateway results

`child_operation_id` and `child_completed` distinguish execution from delivery.
A `transfer_pending` parent reports `child_observation_pending`,
`child_transfer_pending`, or `artifact_preparation_failed`; it does not claim that
the child render failed. POST `/v1/jobs/{id}/recover-transfer` queues only recovery
of this child. Status/result reads do not start a service or enqueue compute.
Controller restart recovers this same child. An unknown initial submission is
looked up by operation key; a missing lookup is not permission to resubmit.

Verified bytes and receipts live in the controller's durable state, and its sync
boundary completes before each acknowledgement. Acknowledgement loss retries the
ack, not the download when local bytes still verify. Child render failure, missing
required child artifacts, parent preparation failure and parent result readiness
remain distinct. Child expiry/tombstones cannot be overridden by stale remote
copies. Pending transfer expiry and explicit cancellation purge staging. State
volume loss is not recoverable without a surviving verified copy.

Timings contain only `phase`, `hop`, `status`, `duration_ms`, `attempt`. Status is
one of executed/skipped/cached/unavailable/interrupted. Molecular data, credentials,
private paths and signed URLs cannot enter these records. Each process uses its
own monotonic clock. No cross-machine timestamp subtraction is used.

## Validation

Prepare test dependencies explicitly, then run
`python scripts/validate_c3_contracts.py --output /temporary/outside-checkout`.
It runs the full guarded repository suite, builds offline, installs the wheel
outside the checkout, asserts import locations and verifies every packaged file
against the source and installed bytes. Installed contracts use prepared test
dependencies, not a clean dependency installation. The audit loads before
application imports and fails even when a forbidden boundary attempt was caught.

After review and commit, run `python scripts/validate_c3_acceptance.py --commit
EXACT_COMMIT --profile PRIVATE_PROFILE --output /temporary/acceptance`. The runner
checks prerequisites first, asserts chains A/D, protein/peptide and PTR before
one DSS export from `examples/1sps_pose.pse`, freezes one bundle/spec pair and uses
isolated host-Blender and private Slurm services. It checks operation/render counts,
digests, build, summary, devices, PNG, cartoon and native sticks, with no scene.
Visually inspect both PNGs afterward. Missing local host runtime is a blocker;
container execution cannot satisfy host acceptance. Successful renders do not
prove unexecuted remote recovery/stress scenarios.

Modal implemented and contract-tested; real backend execution unverified.
