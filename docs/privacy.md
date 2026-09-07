# Privacy and retention

Blender Render is operated by Magnus Bauer. Support and deletion requests can be opened at [the repository](https://github.com/magnusbauer/blender_render/issues). Do not attach confidential structures or credentials to a public issue.

The shared service receives the structure you select, its saved PyMOL camera and enabled-object/color metadata when present, render settings, and your GitHub numeric identity. GitHub authentication requests the `read:user` scope. The service does not request access to your repositories or email. GitHub credentials and OAuth state are encrypted at rest with a persistent operator-controlled key.

Inputs are used to convert and render your requested molecular image. PSE files are converted in a short-lived sandbox with no network, secrets, or mounted service storage. Only validated structure and camera data proceeds to the renderer. The service does not execute uploaded Blender scenes, provide an arbitrary-code tool, or publish a gallery.

Shared computation runs in the operator's Modal workspace. The controller and GPU worker use Modal HTTP Servers, whose request and response payloads are proxied without storage, rather than Modal Function calls. See [Modal's data-retention policy](https://modal.com/docs/guide/security#data-retention). Inputs and artifacts are owner-isolated. Private download links are valid only while the temporary artifact exists.

There is no retained structure library. Inputs live in the controller's temporary filesystem while their jobs are queued or running, and are deleted after completion, failure, or cancellation of the final job using them. An upload that is never submitted expires after five minutes. Each rendered file is deleted after its download response is sent; any undelivered files expire fifteen minutes after completion. A preview does not consume the download. Expired data is removed by a separate cleanup worker, including during long renders. Temporary converter and renderer files are removed after each operation, and the GPU Blender process exits after each job. Container restarts can lose pending inputs and undelivered results; privacy takes priority over replaying them. Downloaded copies remain under your control.

The persistent Modal Volume contains authentication and minimal job/accounting state, not new molecular inputs, rendered images, Blender scenes, or provenance manifests. The service does not create data snapshots or archives. Earlier beta Function invocations remain subject to Modal's provider-side retention of up to seven days; switching transports cannot retroactively erase those provider records. Existing completed downloads keep their original expiry unless explicitly removed.

Minimal job IDs, timestamps, status, pseudonymous ownership, idempotency keys, and cost/quota records remain to prevent duplicate spending and enforce the lifetime budget. Operational logs are for diagnostics; public API errors omit raw uploaded content and internal paths. OAuth records expire with their tokens or can be revoked through GitHub. The service does not use uploaded structures to train models.

Owners may also delete a completed result immediately through authenticated `DELETE /v1/jobs/{job_id}/result`. This removes its remaining files and saved provenance and invalidates existing download links. Deleting a result does not reset usage or refund compute already performed.

Your ChatGPT, Codex, GitHub, and Modal use is also governed by those providers' policies. Local rendering stays on your Mac. An HPC profile sends data to the configured lab, whose operator controls its storage and privacy policy; it does not transit the shared Modal service.

Contact the operator through the repository for access, deletion, or security concerns. This beta has no service-level guarantee and may stop accepting jobs when its credits or operating allowance are exhausted.
