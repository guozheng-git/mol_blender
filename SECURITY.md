# Security policy

## Reporting a vulnerability

Use [GitHub's private vulnerability report form](https://github.com/magnusbauer/blender_render/security/advisories/new).
Include the affected revision/package versions, operating system, enabled client
or service, a minimal reproduction with synthetic or public structures, and the
expected security boundary. Do not attach credentials, private structures or
executable sessions containing research data.

The form requires repository-side private vulnerability reporting to be enabled.
If it is unavailable, contact a maintainer through an existing private channel,
or [open an issue](https://github.com/magnusbauer/blender_render/issues/new)
asking only for a private reporting channel. Keep vulnerability details, proof
of concept and sensitive data out of public issues and pull requests. There is
no guaranteed response-time SLA. Maintainers coordinate fixes and public
disclosure through a private advisory when that facility is available.

Security fixes target the latest maintained revision of `main`. Older release
candidates and historical branches do not receive a separate backport guarantee.

## Local clients and trusted files

Local rendering is the default. It reads trusted structures and launches Blender
with the current user's access. PyMOL PSE sessions can execute code during import;
never load untrusted sessions, including uploaded or restored files. A checksum
proves byte identity, not trust. Bundle restoration does not itself open PyMOL or
Blender, and session-loading APIs do not sandbox executable content.

The renderer's optional MCP adapter uses stdio. co-PyMOL's live desktop server
uses loopback SSE at `127.0.0.1:8766`, normally accessed through its stdio proxy.
Its PyMOL tools can execute commands with the desktop user's privileges. These
are trusted local assistant interfaces, not multi-user sandboxes. Do not expose
co-PyMOL's unauthenticated listener on a public interface or forward it to
untrusted clients. The optional Blender editor bridge similarly uses private
local IPC and bounded controls; it is not a cluster or Internet API.

## Optional cluster service

Explicitly selecting a cluster backend uploads an exported, data-only scene and
render options to the configured gateway. The gateway stores jobs and submits
the shared renderer through Slurm/Apptainer. It rejects PSE uploads, arbitrary
client paths and executable client commands. Token authentication and per-owner
job access are the default; credentials and the spool belong in private storage.

HTTPS with certificate verification is required except for the supported
loopback connection. The service binds to loopback by default. An administrator
may explicitly choose `authentication: none` for a trusted shared network;
that setting gives every client access to the same queue and results. Never use
it for an untrusted or public deployment. See the
[cluster guide](docs/shared-cluster.md) for deployment boundaries and limits.
Installation alone does not start a gateway or submit a job.

## Optional hosted image delivery

The opt-in hosted adapter exposes completed-image retrieval, not render submission.
Its embedding service must supply authentication, authenticated request identity,
ownership checks and non-consuming, expiring HTTPS download URLs. An agent host
may send returned images to its model when using image-inspection tools, and
host-managed storage has its own access and retention policy.

## Runtime and artifact handling

Runtime downloads are explicit setup actions using committed versions and hashes.
Setup creates isolated uv environments; it does not run apt/sudo, user-site pip
or ensurepip in the calling interpreter. Missing system libraries are reported.
System package installation occurs only during the explicit container image build.

Studio restorers cap decoded files at 128 MiB, aggregate decoded and encoded input
at 256 MiB, documents at 20 MiB, and file/part counts at 64. They validate names,
declared sizes, complete part sets and checksums, and bound gzip inflation.
Legacy unpack does not overwrite existing files. These limits do not establish
trust in a restored PSE. Saved Blender scenes are opened with embedded script
execution disabled; camera capture is explicit.

CI audits every locked package version, including runtime and platform variants,
against published advisories. See [repository maintenance](docs/repository-maintenance.md)
for check commands, required branch settings and the dependency refresh process.
