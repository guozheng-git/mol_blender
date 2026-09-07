# Retry and single-call workflow repair

Validated on 2026-09-07 against the owner-only private Modal deployment.

## Failure and correction

A ChatGPT render request submitted the same input six times with different
idempotency keys. Five completed; an operator cancelled the last duplicate.
The conversation reported that cancelled copy instead of the first successful
result. The original PNG, Blender scene, and manifest were recovered locally.
This was a workflow/submission defect, not evidence of an invalid structure.

The service now deduplicates owner-scoped input/effective-settings hashes before
reserving compute, even with changed retry keys. Aliases persist across input
deletion and service restart. A deliberate additional identical render requires
`rerender=true` and a new stable key. Cancellation reports its actual cause.

The new `render_structure` tool uploads and submits together. A recent file hash
lets the HTTP tool reuse an existing operation after input deletion without
another sandbox conversion. Status checks wait up to 25 seconds and return the
next action. Plugin guidance keeps one job ID through submission, waiting, and
delivery, with no repeated import or delegation of the same render.

The green plugin now explicitly references the registered private ChatGPT
connection through `.app.json`. A file-transfer permission prompt can use that
connection's name. Permission settings were not changed to conceal the loop.

## Executed checks

| Check | Result |
|---|---|
| Repository suite | 110 passed; four dependency deprecation warnings |
| Installed wheel outside checkout | 38 relevant tests passed |
| Concurrent changed-key retries | Ten submissions produced one reservation and one execution |
| Cleanup/restart | Retry aliases survive input deletion and reopened SQLite state |
| Distinct work | Changed pose, settings, owner, and explicit rerender remain separate |
| MCP in-process workflow | Single-call upload, retry, wait, and native image return passed |
| Installed Codex adapters | Both discovered ten tools and authenticated as the exempt testing owner |
| ChatGPT connection refresh | UI shows `render_structure`, updated status waiting, and retry descriptions |
| Live authenticated MCP SDK | Public RCSB 1CRN CIF, 256 pixels, eight samples, dried green paint, transparent PNG and Blender scene |
| Live duplicate guard | Three changed keys and repeated combined calls all returned one job; daily count increased by one |
| Live completion | 44.76 seconds through status completion; estimated cost delta $0.09545 |
| Live delivery/privacy | Native image returned; RGBA alpha range 0–255; PNG, scene, manifest downloaded; inputs absent and job delivered |

Live test job: `1078a1c890eb0f4034ceedfa774f4dd2`.

The live SDK test supplied a public fixture file record; it does not establish a
new ChatGPT attachment-picker or permission-dialog test. The refreshed schema
was inspected in ChatGPT. A fresh conversation is required to test the changed
agent workflow without the previous conversation's cached tools and duplicate
job history. The service and repository remain private. Rendering code and the
baseline Linux SIF were not changed by this workflow repair.
