# Optional completed-render delivery

Install the `hosted` extra to use `blender_render.hosted.adapter.create_adapter`.
The ordinary CLI, local stdio MCP, PyMOL bridge and renderer do not import this
adapter or start an HTTP listener.

Selected from PR #3 (`0b090b3`): native PNG verification descriptors, the MCP Apps
viewer, explicit image messaging, optional Library upload and saved-image picker,
plus the simulated-host browser tests. The adapter adds a small integration
boundary instead of restoring the legacy service, job store or render workers.

The embedding service must provide:

- A FastMCP authentication provider and an `owner()` callback deriving the current
  identity from the authenticated request, never from model-supplied arguments.
- A `provider(owner, job_id)` that authorizes ownership, verifies completeness and
  returns `CompletedPNG(data, url, sha256, expires_at)` for a finished render.
- Explicit HTTPS file origins and a non-consuming `/download/…?delivery=ack` URL.
  The service must enforce that URL's authorization and expiry and retain bytes
  through competing host/widget reads. Merely producing a result is not an ACK.

Only `render_result`, `get_render_png_file` and `open_saved_image` are exposed.
The adapter checks PNG bytes, dimensions, digest, expiry and allowed URL origin.
The original PNG is handed off unchanged; only the inline preview may be reduced.
The provider is never called when opening the saved-image picker. There is no
render submission, retry, remote installation or path-reading tool here.

This is an integration library, not a deployable hosted renderer. The caller
must review its authentication/ownership implementation before serving it.
No deployment is included in `main_merged`.

The viewer requests model context and Library storage through host APIs.
An RPC response or uploaded file ID does not prove next-step pixel access or
durable Space storage. Browser simulations exercise these requests, save/reopen
and reuse after revoking the original URL; real ChatGPT/Claude acceptance remains
required. The explicit image-message button can require host confirmation.
