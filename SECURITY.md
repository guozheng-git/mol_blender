# Local execution boundary

This branch reads trusted local structures and launches Blender locally. PyMOL
sessions may execute code during import. Do not load untrusted PSE files.

The optional MCP adapter runs over stdio with the local user's filesystem access;
it is intended for a trusted local Claude/Codex session. There is no HTTP server,
remote authentication, public upload route or deployment interface.

Runtime downloads are explicit setup actions and use the committed version and
checksum locks. Reference images are read locally. Rendering itself does not
upload structures to a rendering service. An agent host may send images to its
model when you use that host's image inspection tools.
