# Local execution boundary

This branch reads trusted local structures and launches Blender locally. PyMOL
sessions may execute code during import. Do not load untrusted PSE files.
This applies equally to uploaded sessions and files restored from a workspace.
A matching checksum proves byte identity, not trust or absence of executable
content. Bundle restoration never opens PyMOL or Blender. Session loading APIs
are trusted-input APIs; they do not sandbox PSE execution.

The optional MCP adapter runs over stdio with the local user's filesystem access;
it is intended for a trusted local Claude/Codex session. The separate, opt-in
hosted delivery adapter requires an embedding service to supply authentication,
request identity, ownership checks and non-consuming, expiring download URLs.
It exposes completed-image retrieval only. There is no public upload route,
render service or deployment interface in this branch.

Runtime downloads are explicit setup actions and use the committed version and
checksum locks. Reference images are read locally. Rendering itself does not
upload structures to a rendering service. An agent host may send images to its
model when you use that host's image inspection tools.

Studio's Python and browser restorers cap each decoded file at 128 MiB, aggregate
decoded and encoded input at 256 MiB, each document at 20 MiB and file/part counts
at 64. Declared sizes, complete part sets, safe names and checksums are validated;
gzip inflation stops before exceeding its limit. Legacy unpack never overwrites.
Oversized sessions must be split or simplified before bundling.

Setup uses uv-managed, isolated environments and pinned requirements. It never
runs apt/sudo, user-site pip or ensurepip in the calling interpreter. Missing
system libraries are reported. System package installation occurs only in the
explicit Apptainer image build, not in runtime setup or render commands.
