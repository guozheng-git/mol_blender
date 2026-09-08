# Operation lifecycle (C1)

The existing `JobStore` remains the single-writer durable FIFO. `job_id` and
`operation_id` identify the same logical operation; no second queue or renderer
is introduced. Apply schema migrations only after draining/stopping an existing
controller. This development change does not migrate any running service.

## Identity and authorization

- Reuse `operation_key` for one user request, independently of transport
  `idempotency_key`. Changed transport keys still recover that operation.
- Reusing an operation key with a different input or options fails with
  `operation_conflict`, including an attempted hidden rough preview.
- Keys supplied on content-reuse responses are persisted as aliases too; a
  reused result cannot leave a later changed request under that key unbound.
- An explicitly requested additional render uses `rerender=True` and a new
  operation key. It gets its own job and reservation even for identical content.
- Legacy callers may retain their original transport key. Content-based retry
  recovery remains available when a key is lost. It returns the old operation,
  including its terminal/unavailable state; elapsed artifact retention is no
  longer implicit authorization to create another render.
- Preview/result/status retrieval does not authorize a render. Presentation
  failures and consumed/missing artifacts do not cause replacement submissions.

Example using an already running service:

```python
job = client.render_file(
    "public.cif", {"preset": "pastel"},
    idempotency_key="transport-attempt-1", operation_key="user-request-1",
)
# Retry with the same operation_key, even if the transport key changed.
# An explicit additional render uses a new operation_key and rerender=True.
```

`POST /v1/render-files/{format}` returns 202 after committing raw input and a job,
before conversion. Its body is a four-byte unsigned big-endian JSON-envelope
length, a JSON envelope of at most 65,532 bytes, and the raw molecular file.
The existing 50 MiB file limit remains; base64 is only internal temporary storage.
The client handles framing. Envelope fields are `options`, `idempotency_key`,
`operation_key`, `rerender` and `selection`. Unknown fields fail. Converted
StructureBundle validation and the shared resolver run before backend admission;
only the resolved snapshot reaches the renderer. Unsupported converter selection
controls fail instead of being silently discarded.
Raw admission captures the named preset definition, preserving its camera policy
and provenance without turning it into authoritative full JSON. Only resolution
that depends on the exported bundle waits for conversion. Older queued raw jobs
without a captured named preset fail clearly rather than silently using new assets.

The CLI sends molecular files through this admission endpoint, never through a
competing synchronous import/submit fallback. Already exported JSON bundles use
one upload followed by one admission to the same queue. Both report `Job <id>`
on stderr before waiting. `--no-wait` returns that job without status/result
retrieval; waiting and downloads continue from that same ID.

Use `--operation-key REQUEST` across retries. `--idempotency-key` remains
supported; content recovery also prevents an identical lost-key submission from
creating another operation. An additional render uses `--rerender` with a new
stable operation/idempotency key; retry that key if its response is lost.

For HPC CLI submissions, a local controller under `controllers/<profile>` owns
the outer operation before conversion or allocation. Its existing GatewayBackend
dispatches the child after admission; CLI status/result/cancel address the outer
queue. Ordinary local rendering uses the existing `local` controller. Only these
authenticated private loopback controllers use the former CLI's trusted local
file exporter, now deferred with its configured PyMOL interpreter and selection.
Public/untrusted uploads continue to require the isolated converter. Interpreter
changes require stopping the existing controller, never silent replacement.
The legacy synchronous import and blocking render endpoints remain compatibility
APIs; the CLI does not fall back to them. No MCP tool schema or preset-selection
change is included.

## State and stages

Existing states (`queued`, `running`, `done`, `failed`, `cancelled`, `delivered`,
`deleted`, `expired`) remain. A job can be running during conversion; that is not
evidence that a Blender worker has started. The additive stage journal records:

`validation -> conversion (files) -> waiting_for_compute -> scene_build -> rendering -> result_ready -> delivery`

Failure or completed cancellation ends at `failed` or `cancelled`. Cancellation
requested while a task is alive retains the running state/slot until it exits.
Terminal stages cannot be overwritten by late progress. The shared Blender entry
point writes a private stage journal at actual scene-build and render boundaries;
the local worker forwards it to the controller. Remote streaming/timing coverage
is not claimed by the fake/local tests. Legacy rows gain fields without invented
historical timing entries.

Conversion failures before backend entry release the render reservation without
charging GPU execution; the existing separate conversion charge remains. Ambiguous
running work after controller interruption still fails conservatively and is not
automatically replayed.
Draining blocks new work, not conversion of an already-admitted running job;
conversion dispatch still enforces ownership and lifetime budget limits.

The HTTP worker's execution thread owns its admission lock through backend stop
and eventual process exit. HTTP timeout/cancellation never releases an alive
worker's slot. A process that does not exit can leave admission blocked; overlap
is not an acceptable fallback.

## Gateway and read-only access

Each outer job uses its ID as the child's stable operation key and explicitly
requests a distinct child. Once persisted, recovery uses that child ID without
uploading/submitting again. A lost initial child response can recover by key after
reupload of identical content. Unavailable/consumed required artifacts fail; they
never count as a completed outer render or trigger replacement compute.

Configured-client construction is lazy. Reads use cached connections and fail
clearly if absent or unreachable; they never repair/start a service or allocate.
`GET /v1/jobs` recovers the owner's last 50 operations without retaining inputs.
Status checks artifact availability without changing stored state or rendering.
Capability lookup reports installed-controller declarations and explicitly marks
the runtime unverified. `doctor` is inspection-only; explicit `setup` retains its
runtime probe. Explicit `service start` calls the startup path directly and returns
the existing health response: local creation uses `ensure_local`; HPC creation
uses `hpc.connect`. This command controls the compute service, while CLI HPC job
handles belong to the outer controller. `service status` attaches to the existing
cached compute connection and reports stopped if unavailable; it never runs an
SSH status command or creates a tunnel/allocation. Job recovery, quota and reads
attach to their existing controller. No read repairs an absent or stale session.

Multi-object exports now remove their own temporary UUID from exported names and
retain the user's selection/state. Actual object states, transforms, coordinates,
colors, secondary structure and camera remain part of content identity. Existing
historical UUID-bearing payloads/fingerprints are not rewritten; retained operation
keys are the compatibility recovery mechanism, not an asserted cross-version
content-cache migration.
