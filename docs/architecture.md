# Architecture

All compute paths use `blender_render` and the same schema, renderer entry point,
presets, and pinned Molecular Nodes API. Compatibility modules forward to this
implementation; they do not maintain another renderer.

| Boundary | Implementation | Responsibility |
|---|---|---|
| PyMOL | `export.py`, `pymol_bridge_plugin.py` | CIF-first export, DSS, enabled objects, transforms, colors, camera |
| Client | `cli.py`, `client.py`, `oauth_client.py` | Configuration, authenticated upload, async jobs, downloads |
| Validation | `core.py`, `uploads.py` | Preset schema, structure parsing, bounds, URL validation |
| Control | `jobs.py`, `service.py`, `api.py`, `budget.py` | Durable FIFO, ownership, quotas, reservations, cleanup |
| Rendering | `rendering.py`, `blender_custom_render.py`, `paint.py`, `geometry.py` | One Blender transaction and provenance manifest |
| Local runtime | `runtime*.py`, `render_worker_manager.py` | Managed install, loopback service, Blender process |
| HPC | `hpc.py`, `hpc_remote.py`, `hpc_proxy.py` | Allocation, attach, health checks, SSH transport |
| Modal | `modal_app.py`, `deployment.py` | Isolated conversion, L4 execution, serialized controller |
| Lab HTTPS | `gateway.py`, `auth.py` | Lab-owned OAuth gateway and isolated Apptainer conversion |
| Plugin | `mcp_server.py`, `preview.html`, `plugins/` | Tool contracts, file adapters, image previews, downloads |

## Render transaction

PyMOL exports into one temporary directory. It records the saved eighteen-value
view, FOV, projection, viewport, atom colors, secondary structure, enabled object
names, and transformed coordinates. One flattened CIF preserves the selection;
PDB is an explicit parsing fallback. Polymer cartoon failure is reported rather
than silently changing its representation to a tube.

The renderer resolves the preset before execution, loads the structure through
Molecular Nodes, applies content-specific styles, and reconstructs the camera.
Protein, nucleic acid, ligand, ion, and water selections share the same style
schema. Native sticks do not introduce atom spheres. Painted cartoons preserve
nucleotide instances when baking rounded edges; unset instance colors transfer
from source atom colors. Rough mode changes render performance only.

The transaction writes a PNG, optional Blender scene, and manifest. The manifest
records input provenance, hash, DSS status, selected structure format, geometry
backends, resolved preset, runtime versions, GPU device, camera, timings, and
artifact checksums. Temporary files are deleted by default. Job artifacts are
retained according to service policy.

## Job API version 1

| Request | Result |
|---|---|
| `POST /v1/structures` | Validate an exported payload and return an owned input ID |
| `POST /v1/files/{pse,cif,pdb}` | Convert a raw molecular file in the configured sandbox |
| `POST /v1/jobs` | Commit reservation and return a job ID immediately |
| `GET /v1/jobs/{id}` | State, queue position, timestamps, cancellation state |
| `POST /v1/jobs/{id}/cancel` | Cancel queued or running work |
| `GET /v1/jobs/{id}/result` | Manifest and download metadata |
| `GET /v1/jobs/{id}/artifacts/{name}` | Owner-authenticated artifact |
| `GET /v1/quota` | Remaining user quota and project compute allowance |
| `GET /health` | Readiness, version, queue depth |

Private health calls require authentication. Public health discloses only
aggregate readiness. Legacy `/render`, `/status/{id}`, `/result/{id}`, and `/stop`
remain authenticated compatibility routes. Administrative drain/resume/stop
requires a separate operator token; it cannot impersonate a public user.

The database serializes submission and preserves queue order, owner, timestamps,
idempotency key, progress state, reservations, and remote execution ID. A restart
fails an interrupted execution conservatively and resumes pending jobs. Only one
Blender render runs at a time per service. A second PyMOL client attaches to the
same queue. Cancellation propagates to Blender or the remote function.

## Security and deployment boundaries

PSE is an executable session format. Public conversion runs in a short-lived
sandbox without network, identity tokens, mounted filesystems, or secrets. The
renderer receives validated structures and camera data, never uploaded Python or
Blender scenes. Upload size, atom count, numeric values, image dimensions, and
runtime are bounded. URL imports allow public HTTPS destinations and reject
private addresses and redirects.

GitHub OAuth requests minimal identity access. Persistent encrypted credentials
survive upgrades. Every job and result is checked against its owner. Signed
download links are short-lived bearer capabilities scoped to one owner's
artifact; sharing a link grants access until its expiry.

Own-HPC structures travel over SSH to the lab's service. A ChatGPT connection to
own-HPC uses the lab's authenticated HTTPS gateway. Private cluster jobs do not
pass through the public Modal deployment.
