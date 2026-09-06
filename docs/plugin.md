# Codex and ChatGPT

The repository contains an installable plugin in `plugins/blender-render`, a rendering skill, and a marketplace entry in `.agents/plugins/marketplace.json`.

## Shared service

Install the repository marketplace in Codex, then install Blender Render. The endpoint is restricted to the owner's test account. Public publication and identity verification are deferred; repository access requires the owner's permission.

```sh
codex plugin marketplace add https://github.com/magnusbauer/blender_render
codex plugin add blender-render@personal
```

The included MCP URL is `https://almostscheidplatz--blender-render-beta.modal.run/mcp`. Authenticate with GitHub. The service requests public identity access (`read:user`), not repository access.

In ChatGPT developer settings, add the same HTTPS MCP endpoint using OAuth. The plugin directory listing is a separate review process; repository installation and a tested custom MCP connection do not imply directory approval. File-enabled tools declare `openai/fileParams` and accept ChatGPT's `download_url` and `file_id` object. Do not paste a private local filesystem path into the hosted service.

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

Available tools are `import_structure`, `list_presets`, `submit_render`, `render_status`, `render_result`, `cancel_render`, and `available_quota`. Submit once with an idempotency key, poll that job, and retrieve the result. The result includes a native PNG preview and the PNG, optional `.blend`, and manifest downloads.

ChatGPT additionally displays an MCP Apps preview card with download buttons.
Its checkerboard indicates transparency; it is not baked into the PNG. Request
the result again to refresh expired download links while the files are retained.

For ChatGPT with private compute, use your lab's authenticated HTTPS gateway as described in [HPC setup](hpc.md). ChatGPT cannot call a laptop's loopback stdio service directly.
