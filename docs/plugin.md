# Codex and ChatGPT

The repository contains an installable plugin in `plugins/blender-render`, a rendering skill, and a marketplace entry in `.agents/plugins/marketplace.json`.

## Shared service

Install the repository marketplace in Codex, then install Blender Render. The endpoint is restricted to the owner's test account. Public publication and identity verification are deferred; repository access requires the owner's permission.

```sh
codex plugin marketplace add https://github.com/magnusbauer/blender_render
codex plugin add blender-render@personal
```

The included MCP URL is `https://almostscheidplatz--blender-render-beta-gateway.us-east.modal.direct/mcp`. Authenticate with GitHub. The service requests public identity access (`read:user`), not repository access.

In ChatGPT developer settings, add the same HTTPS MCP endpoint using OAuth. The green local plugin includes an `.app.json` dependency on the owner's registered connection, named **Blender Render Private Beta**. That connection supplies the hosted tools; its name can appear in ChatGPT's permission prompt even when the green plugin was selected. Selecting a plugin does not itself authorize file transfer. Other deployments must replace this app ID with their own registered connection ID. The plugin directory listing is a separate review process; repository installation and a tested custom MCP connection do not imply directory approval. File-enabled tools declare `openai/fileParams` and accept ChatGPT's `download_url` and `file_id` object. Do not paste a private local filesystem path into the hosted service.

## Your Mac or HPC from Codex

Install the wheel's `mcp` extra and configure the desired compute profile. Add the stdio adapter:

```sh
codex mcp add blender-render-local -- /absolute/path/to/blender-render mcp --profile local
codex mcp add blender-render-ipd -- /absolute/path/to/blender-render mcp --profile ipd
```

The local adapter accepts a user-selected absolute `.pse`, `.cif`, or `.pdb` path and uploads the validated export to that profile. A private HPC profile never routes through the shared Modal endpoint. To use only the local adapter, disable the shared plugin's MCP connection in Codex and retain its rendering skill, or use the local configuration example below when packaging a lab plugin:

```json
{
  "mcpServers": {
    "blender-render": {
      "command": "/absolute/path/to/blender-render",
      "args": ["mcp", "--profile", "ipd"]
    }
  }
}
```

For local files with hosted compute, sign in through the CLI and attach its file
adapter. This uploads the selected file to the hosted sandbox; Blender and PyMOL
do not need to be installed locally:

```sh
blender-render login --profile hosted
codex mcp add blender-render-files -- /absolute/path/to/blender-render mcp --profile hosted
```

The shared HTTPS tool uses ChatGPT's attachment object. The stdio adapter uses a
local path. Choose the adapter that matches where the input file is available.

Use `render_structure` to upload and submit in one tool call. Keep its job ID, use `render_status` (a read-only wait of up to 25 seconds) until done, then immediately call `render_result`. Do not repeat upload or submission to check progress. Identical retries reuse the job without another GPU reservation, including when an agent changes its retry key. Explicit additional identical renders use the compatibility import/submit tools with `rerender=true` and a new stable key.

The ten tools are `render_structure`, `import_structure`, `list_structures`, `delete_structure`, `list_presets`, `submit_render`, `render_status`, `render_result`, `cancel_render`, and `available_quota`. The separate import/submit tools remain for existing clients. `list_structures` shows pending imports, not a saved library. `delete_structure` removes an unused import early. Inputs are automatically deleted after rendering. The result includes a native PNG preview and PNG, optional `.blend`, and manifest downloads. Refresh the ChatGPT connection's tools after an upgrade and use a new conversation so cached tool descriptions do not repeat the old workflow.

ChatGPT receives a lightweight native PNG preview, an inline preview URL, and direct PNG,
Blender scene, and manifest links. The assistant should include **View PNG preview** and download links in its final
reply. Some ChatGPT surfaces hide remote Markdown images even when the URL is
correct, so the explicit preview link is required. Inline display is not yet
reliable across ChatGPT surfaces. Delivery does not depend on a custom MCP Apps card: a ChatGPT
conversation mounted that card but stayed on "Loading the completed render"
after the render had finished. The legacy resource remains available for
old clients, but current tool discovery does not attach it to results. Request
the result promptly: each downloaded file is deleted, and undelivered files
expire fifteen minutes after completion.

The native preview is at most 1024 pixels on its longest edge and 384 KiB;
the downloadable PNG keeps the requested resolution and transparency. Results
use ordinary download links instead of MCP resource attachments, avoiding an
automatic import of all output files before ChatGPT can finish its reply.

For ChatGPT with private compute, use your lab's authenticated HTTPS gateway as described in [HPC setup](hpc.md). ChatGPT cannot call a laptop's loopback stdio service directly.
