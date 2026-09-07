# Private beta privacy update — September 6, 2026

The repository and deployment remain private. Live GitHub OAuth admission is
restricted to numeric user ID 11480757. Publisher verification and submission
remain deferred. This report supersedes the initial beta's quotas, retention,
transport, and plugin-tool counts in [release validation](release-validation.md).

## Executed checks

| Area | Evidence |
|---|---|
| Repository tests | 104 passed, with four upstream deprecation warnings. Tests cover ten completed renders per day, pending-slot reservation, failed/cancelled slot release, unlimited owner testing, lifetime-budget refusal, concurrent import deduplication, ownership, cleanup, cancellation, and idempotent retries after input deletion. |
| Installed wheel | 32 copied tests passed in a Python 3.11 environment outside the repository. Imports resolved from site-packages. The HTTP worker, paint preset, schema, runtime locks, and preview are packaged. |
| Old data deletion | All ten earlier uploads were removed. With explicit owner approval, the API deleted 24 old PNG/Blender/manifest files from eight completed jobs, totaling 19,258,723 bytes. The controller subsequently reported zero persistent input files, artifact files, retained results, and completed-job render options. Local downloads were preserved. |
| HTTP Modal worker | Public 1SPS PSE and 1BNA CIF rendered on the L4 through Modal HTTP Servers. Blender 5.0.1/Molecular Nodes 4.5.10 and CCD validation passed after installation of server dependencies. GPU containers scale to zero. |
| Download lifecycle | The actual 768 × 576 PSE render downloaded PNG, Blender scene, and manifest. Its job became `delivered`, its result was cleared, and its input and artifact directory were absent. Unit tests also cover partial-range delivery and expiry. |
| Codex adapters | The installed HTTPS plugin and local-file stdio adapter each discovered nine tools. Both authenticated as the testing owner and returned no daily quota and an empty input list. |
| ChatGPT | The new “Blender Render Private Beta” connection authenticated through GitHub. A public 1BNA file object rendered successfully, and subsequent tools confirmed input deletion and the owner exemption. After plugin refresh and conversation reload, the native card displayed the transparent DNA image and all three download buttons. |
| ChatGPT downloads | Clicking the PNG, Blender scene, and manifest buttons downloaded all three files. The result then returned HTTP 410 with job state `delivered` and no pending input. The PNG is RGBA, 768 × 576, alpha range 0–255. The downloaded scene opened in Blender 5.0.1 with automatic scripts disabled, Cycles selected, transparency enabled, and dependency license text embedded. |
| Explicit deletion | Owner-authenticated result deletion removes legacy files and SQLite provenance together. Tests verify unknown/other owners cannot delete results, active work is protected, signed downloads are revoked, repeated deletion is harmless, and deleting results cannot refund quota or compute costs. |

The current MCP endpoint is
`https://almostscheidplatz--blender-render-beta-gateway.us-east.modal.direct/mcp`.
The older Function endpoint is stopped. The short `.modal.run` widget origin is
only the ChatGPT sandbox identity; molecular uploads and downloads use the HTTP
Server endpoint.

## Privacy and budget boundaries

New molecular inputs and results use temporary container files rather than the
persistent state Volume. Inputs are removed after their final active job;
unused uploads expire after five minutes. Downloaded files are removed after
delivery, and undelivered files expire after fifteen minutes. Restarts can lose
pending inputs and results. No server archive or structure library is kept.
Minimal authentication, job, idempotency, quota, and cost records remain.

[Modal documents](https://modal.com/docs/guide/security#data-retention) that HTTP
Server payloads are proxied without storage. Prior Function invocations can
remain in Modal's provider-managed records for up to seven days; this deployment
change does not retroactively erase them. ChatGPT and user-controlled local
downloads have their own retention policies.

The ordinary allowance is ten completed renders per UTC day. Owner testing has
no daily quota, but still reserves real compute against the original $530
lifetime ceiling. No payment method, replenishment, or public access was added.

## Unchanged release gates

The initial Mac/A4000/L4 image comparison remains baseline renderer evidence.
The earlier SIF and corresponding dependency-source bundles remain available as
that initial milestone; the new wheel, source archive, plugin, and deployed
Modal service contain this privacy update. A rebuilt SIF is not claimed here.
The ChatGPT local attachment picker, a second personal Modal account, lab TLS
hosting, OCI registry publication, and publisher verification remain outside the
executed checks. See the original report for those gates.
