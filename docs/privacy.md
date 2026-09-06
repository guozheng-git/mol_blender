# Privacy and retention

Blender Render is operated by Magnus Bauer. Support and deletion requests can be opened at [the repository](https://github.com/magnusbauer/blender_render/issues). Do not attach confidential structures or credentials to a public issue.

The shared service receives the structure you select, its saved PyMOL camera and enabled-object/color metadata when present, render settings, and your GitHub numeric identity. GitHub authentication requests the `read:user` scope. The service does not request access to your repositories or email. GitHub credentials and OAuth state are encrypted at rest with a persistent operator-controlled key.

Inputs are used to convert and render your requested molecular image. PSE files are converted in a short-lived sandbox with no network, secrets, or mounted service storage. Only validated structure and camera data proceeds to the renderer. The service does not execute uploaded Blender scenes, provide an arbitrary-code tool, or publish a gallery.

Shared computation and storage run in the operator's Modal workspace. Inputs and artifacts are owner-isolated. Download URLs carry expiring access tokens, last 15 minutes, and should be treated as private links. Anyone you give a valid link to can download that artifact until the link expires. Fresh links can be issued only to the owning account while its artifact remains available.

Uploaded structures expire after 24 hours; render artifacts expire 24 hours after completion. Expired files are removed by the cleanup worker. Temporary converter and renderer files are removed after each operation. Locally downloaded copies remain under your control. The service reserves operating funds for the final retention/cleanup period when compute admission closes.

Minimal job IDs, timestamps, status, pseudonymous ownership, idempotency keys, and cost/quota records remain to prevent duplicate spending and enforce the lifetime budget. Operational logs are for diagnostics; public API errors omit raw uploaded content and internal paths. OAuth records expire with their tokens or can be revoked through GitHub. The service does not use uploaded structures to train models.

Your ChatGPT, Codex, GitHub, and Modal use is also governed by those providers' policies. Local rendering stays on your Mac. An HPC profile sends data to the configured lab, whose operator controls its storage and privacy policy; it does not transit the shared Modal service.

Contact the operator through the repository for access, deletion, or security concerns. This beta has no service-level guarantee and may stop accepting jobs when its credits or operating allowance are exhausted.
