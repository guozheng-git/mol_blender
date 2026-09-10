# Result recovery (C2)

Rendering completion and local delivery are separate. Recovery never uploads an
input, submits a job, starts compute, or regenerates a missing scene. PNG-only
generation remains the default; only an explicitly requested scene is delivered.

## Verified CLI/Python delivery

`Client.download` (also used by CLI `result`) prepares a writable destination,
records the artifact descriptors, downloads to private temporary files, verifies
size and SHA-256, and atomically replaces the destination. It saves a minimal
receipt before acknowledging any artifact. Receipts contain job ID, artifact
names/checksums/sizes and verified output paths, not coordinates, credentials,
signed URLs or full provenance. The manifest remains a user-selected output file.

Receipts live under the configured local home in `result-receipts/`, scoped by a
hash of the service URL, current credential and job ID. Other credentials or
service URLs cannot reuse that receipt. Credential rotation intentionally requires
the new credential's server-side recovery; existing output files remain intact.
Receipt locks serialize simultaneous downloads for that scope. Every reuse checks
the actual local file's size and hash; a stale path is not accepted as an output.

If a required artifact fails, the return value retains successful `outputs`, includes
per-artifact `errors` and `missing_required`, sets `complete: false`, and returns
`artifact_unavailable` with `recover_verified_local_copy_or_stop`. A result-only
retry can recover an interrupted transfer while its remote artifact remains
available; an unavailable original needs a verified local copy. Neither case
authorizes a new render. The CLI prints incomplete results and exits nonzero. A fresh client/result call
reconstructs verified outputs, including a new destination, and requests only
missing bytes. No molecular input is required. Losing both the local verified
file/receipt and the expired/deleted server original is unrecoverable; this never
authorizes another render.

## Explicit delivery acknowledgement

This is an additive, opt-in API policy, not an implicit change to hosted retention:

- Result metadata advertises `delivery_policy: explicit_ack_v1`.
- `GET /v1/jobs/{id}/result?inventory=true` returns owner-scoped immutable expected
  descriptors and current availability even after delivery/deletion/expiry. The
  ordinary result endpoint retains terminal HTTP 410 behavior. Download links
  are returned only for currently available artifacts.
- Authenticated `GET /v1/jobs/{id}/artifacts/{name}?delivery=ack` serves without
  deleting. TTL still applies and is never renewed.
- After atomic local save, hash verification and receipt persistence, the client
  sends `POST /v1/jobs/{id}/artifacts/{name}/ack` with `{"sha256": "..."}`.
  Acknowledgement deletes that server artifact under the existing delivery rule.
  Owner-scoped checksum tombstones make duplicate/lost-response acknowledgements
  idempotent; a wrong checksum fails. A failed acknowledgement leaves the verified
  local result usable; a later result call may repeat acknowledgement.
- Legacy artifact GETs and signed downloads retain their existing delete-after-GET
  behavior. Preview retrieval remains non-consuming. No browser can be assumed to
  have saved a file merely because it received a response.
- A new resumable client rejects an older server that does not advertise this
  policy before downloading any bytes. It does not fall back to consuming GETs.

The existing fifteen-minute result TTL, input deletion, budgets and scene opt-in
are unchanged. No existing hosted service was migrated or deployed by this task.

## Planned controller replacement

Where temporary data storage will be replaced, explicitly preserve still-unexpired
results before destroying that storage. The operator must drain admission and
finish all queued/running jobs first. `JobStore.preserve_results(new_data_root)`
copies only verified result artifacts to the replacement data root, atomically per
job. It requires the same persistent database on restart and keeps original
expiry/owner/accounting. It does not copy molecular inputs or refresh TTL. An
already matching destination permits retry; different files are never overwritten.
Source outputs remain subject to the existing deletion/expiry policy.

This library-only handoff is never invoked by status or result recovery. It does
not change the configured hosted storage policy automatically. Operators must
choose storage permitted by their deployment's privacy policy, stop the old
controller, and configure the replacement with the preserved data root and same
database. A replacement missing artifacts reports unavailable instead of done;
it cannot recover destroyed data or start replacement compute.

Validation uses temporary stores, in-process HTTP and fake workers only. Real
backend handoff/storage behavior belongs to C3 parity and E installed validation.

## Authoritative inventory and copies

On completion, `expected_artifacts` records name, SHA-256, byte size, media type,
required flag and original expiry once. `artifact_states` changes independently:
`available`, `delivered`, `expired`, `deleted`, or `lost`. The last four are
tombstones. New acknowledgements use that state and the immutable checksum;
they do not maintain a second delivery state machine.

PNG is always required. A scene is required only when requested in the original
operation. The manifest is diagnostic: its absence is reported but does not make
verified required outputs incomplete. Version-2 local receipts store the full
expected inventory, remote availability, local verification and checksum failure.
Completion requires a known required inventory and actual hash/size verification
of every required file. A fresh client cannot omit a previously consumed PNG just
because a scene or manifest remains. Existing receipts can reuse verified local
bytes; stale paths and modified files cannot establish delivery.

The persistent database registers operator-selected artifact roots before a
handoff starts. All transient, persistent, legacy and registered handoff copies
obey the same tombstone. Acknowledgement purges only the acknowledged artifact
from every managed root. Deletion and expiry purge remaining copies. Startup,
cleanup, handoff and result exposure reconcile durable state; result exposure may
persist observed loss and finish pending artifact cleanup, but never admits work.
Tombstones commit before filesystem deletion, so interrupted deletion can be
retried without resurrection. Cleanup includes terminal jobs, unknown job
directories and abandoned `.handoff-` staging directories. Root registration is
library-only, canonicalizes trusted configured aliases once, and never uses request output paths. Registered canonical roots that are subsequently replaced by symlinks are not followed. Deletion
does not follow symlinked job directories or files.

Migration adds inventory/state columns and the root registry without removing job
or quota records or extending expiry. Older rows use trusted embedded or
checksum-verified manifest metadata when available, and any prior C2 draft
acknowledgement records. PNG remains required even without a descriptor. An
older completed row's shrinking artifact list remains evidence of delivery:
original manifest artifacts absent from that list become delivered tombstones,
even when stale physical copies exist. An
unrecoverable original required inventory remains explicitly incomplete. Earlier
version-1 local receipts are refreshed from the server's expected inventory;
without that upgrade they cannot assert complete delivery. Unregistered copies
outside managed storage are not discoverable; deployments must preserve the
database and register handoff roots before copying, with one controller owner.

The installed-wheel contracts were executed outside the checkout using the
existing isolated environment; this is not real hosted storage validation or a
clean dependency installation. See the progress record for exact commands.

Status and completed-result reporting require every expected required remote
artifact to be available. A consumed PNG or requested scene prevents advertising
a reusable complete result; diagnostics alone cannot. Retries still recover the
same operation, with `result_available=false` and the existing unavailable/expired
status where appropriate. Fully delivered jobs retain `delivered` status. This
does not change durable render history or authorize replacement work. Inventory
queries and retrieval of remaining artifacts remain usable with local receipts.
