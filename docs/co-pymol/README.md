# co-pymol

*Drive PyMOL in plain English — from Codex, Claude Code, Cursor, or your phone.*

**`co-pymol`** is a PyMOL plugin that turns PyMOL into an MCP server, so you can drive it in English from any MCP client (Codex, Claude Code, Cursor) instead of typing PyMOL commands by hand. On startup it spins up an MCP server — built on the official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) — inside PyMOL's own Python process, exposing the `pymol.cmd` API as tools, so you can:

- **Automate analysis and visualisation** with an agent instead of doing it by hand
- **Read confidence values** (pLDDT / ipTM / pTM / PAE) on an agent's window via a gemmi-backed metrics layer that parses mmCIF
- **Drop in your own helpers** — point the agent at a `.py` of custom PyMOL presets / analysis functions and ask it to use them
- **Work over SSHFS-mounted cluster paths** as usual
- **Remote-control from your phone** through an MCP client that supports remote sessions

## Codex demo

https://github.com/user-attachments/assets/bbbdbe32-bc59-4446-9bd1-3e4860af022e


An example session in Codex / Claude Code / Cursor:

```
> Load all the CIF files in /path/to/dir/w/predicted/structures/
[all the structures visible on PyMOL window]
Loaded all structures, sorted by mean pLDDT.

> Which one has the worst ipTM?
model_3 — ipTM 0.41 (others are 0.7+).

> Show me the low-confidence loops on structure_500.
[renders cartoon on PyMOL window, residues 142–168 highlighted, mean pLDDT 38]
```

## Requirements

- **PyMOL** (a normal desktop install — the plugin installs into PyMOL's bundled Python, not your system Python)
- **An MCP client** — Codex, Claude Code, or Cursor 3.12.17 or newer. Older
  Cursor versions may not refresh the tool list when PyMOL starts after Cursor.
- **macOS** — that's all I've tested on :/ Linux / conda / non-standard installs should work in principle (the recipe is just "install into PyMOL's bundled Python") but these haven't been tested.

## Installing

This integration ships with Blender Render. Follow the repository's
[installation guide](../../README.md) for the separate renderer runtime and
[AGENTS.md](../../AGENTS.md) for development constraints.

**1. Clone and install**

```bash
git clone https://github.com/magnusbauer/blender_render.git
cd blender_render
/Applications/PyMOL.app/Contents/bin/python -m pip install --user -e ".[pymol]" --ignore-requires-python
```

Run these commands from this repository's root. `--ignore-requires-python`
allows the PyMOL-side package to run in bundled Python 3.10; the renderer still
uses its separate Python 3.11 environment. Use `python -m co_pymol` with PyMOL's
interpreter for the CLI: this distribution does not install a `co-pymol`
executable.

**2. Set up the MCP client you use**

Choose exactly one:

```bash
# Codex
/Applications/PyMOL.app/Contents/bin/python -m co_pymol setup codex

# Cursor
/Applications/PyMOL.app/Contents/bin/python -m co_pymol setup cursor

# Claude Code
/Applications/PyMOL.app/Contents/bin/python -m co_pymol setup claude
```

Each command installs the shared PyMOL startup hook and configures only the
selected client. The other clients do not need to be installed. The Codex and
Claude choices require their existing CLI on `PATH`; the Cursor choice writes
`~/.cursor/mcp.json` directly. These commands configure co-pymol for a client —
they do not install Codex, Cursor, or Claude Code themselves. All are safe to
re-run.

Lab rendering uses the shared renderer's cluster connection; see the
[cluster guide](../shared-cluster.md). Client setup does not connect to a
server or allocate compute.

The client setup is global, so `pymol` is available from every session and
there is no need to `cd` into this repo. It uses the recommended bundled stdio
proxy (`python -m co_pymol proxy`), which forwards to PyMOL and survives PyMOL
quitting/restarting. After one successful connection, tool calls made while
PyMOL is down return a clear message. On a cold start, the tools appear when
PyMOL first connects.

Codex supports local stdio and Streamable HTTP servers, not co-pymol's SSE
endpoint, so Codex must use the proxy. Cursor and Claude Code can use direct SSE
instead, but their connections drop whenever PyMOL restarts. To switch after
normal setup, use:

```bash
# Claude Code: replace its user-scoped proxy entry with the direct SSE URL
claude mcp remove pymol --scope user
claude mcp add --transport sse --scope user pymol http://127.0.0.1:8766/sse
```

For Cursor, edit only its `pymol` entry in `~/.cursor/mcp.json`, preserving any
other servers:

```json
{
  "mcpServers": {
    "pymol": {
      "url": "http://127.0.0.1:8766/sse"
    }
  }
}
```

The `--host` and `--port` options on setup commands change where the client-side
proxy connects; they do not change the address started by the automatic PyMOL
hook, which is currently fixed at `127.0.0.1:8766`. Leave those options off for
the normal installation.

**3. Restart PyMOL and your selected client**

The PyMOL console should print:

```
co-pymol: MCP server running on http://127.0.0.1:8766/sse
```

If you don't see that line, `~/.pymolrc.py` isn't being loaded. The file must be in your home directory (`echo $HOME` to check), and you need a full PyMOL quit + relaunch, not just a window close.

By default the server binds `127.0.0.1:8766` (loopback), so PyMOL and the MCP
client must run on the same machine. The proxy supports starting the client
before PyMOL; that reverse-order flow requires Cursor 3.12.17 or newer.

Confirm the selected client has the entry: run `codex mcp list` or
`claude mcp list`, or open Cursor Settings → MCP. Codex's MCP configuration is
shared by the ChatGPT desktop app, Codex CLI, and Codex IDE extension on the
same host. See the
[official Codex MCP documentation](https://developers.openai.com/codex/mcp/).

**4. Confirm the agent is talking to PyMOL**

Ask the agent something like *"are you connected to PyMOL? what version is
loaded?"* — if it calls a `pymol` tool (e.g. `get_version`) and reports a real
answer, you're wired up. If it cannot see any `pymol` tools, re-check step 2 and
make sure you opened a new client session after setup.

## Upgrading

Update this checkout, then reinstall its PyMOL extra whenever dependencies or
package metadata change. If migrating from the standalone `co-pymol`
distribution, uninstall that distribution first so two packages do not own the
same `co_pymol` modules. The merged distribution is `pymol-plugin-bridge`.

```bash
/Applications/PyMOL.app/Contents/bin/python -m pip install --user -e ".[pymol]" --ignore-requires-python
```

Then re-run the setup command from step 2 for the client you use. If its entry
still points at the older `-m co_pymol.proxy`, it won't start.

## Experimenting!

1. Open PyMOL (the MCP server auto-starts).
2. Open Codex, Claude Code (`claude` in a terminal), or Cursor with MCP enabled.
3. Talk to it:
   - "Load all CIF files in `<dir>`, sorted by ipTM"
   - "Color by pLDDT, then render a ray-traced PNG"
   - "Align model_0 onto model_1; what's the RMSD?"
   - "Look at `~/scripts/my_pymol_helpers.py` — apply the publication-style view to all objects"

Want sample data? **[Click here](https://500.kim/resources/pizza-and-pymol.zip)** to download a few sample CIF files (AF3 predictions, antibodies, multi-domain proteins) to play with.

For Blender pictures of the current PyMOL view, including remote lab rendering,
see the [PyMOL agent setup](../../README.md#pymol-agent-co_pymol) and
[shared cluster guide](../shared-cluster.md).
Render names are optional: “Render this view” creates a folder such as
`mellow-otter`, while “Name it binding-site” uses your name. “Show my recent
Blender renders” discovers saved runs even in a new chat.
Preset browsing returns short text descriptions by default. “Submit this view
with all presets in the background” captures one scene and returns a batch
receipt; rendering and downloads continue while you use PyMOL and the chat.

Metrics interpret B-factors as pLDDT only when prediction metadata or a
matching confidence/PAE sidecar identifies them. Unannotated B-factors remain
unscored. Low-confidence reports retain author chain IDs, residue numbers and
insertion codes, and split regions at chain boundaries or numbering gaps.

## Uninstalling

Reverses the install steps. There's no `uninstall` subcommand, so remove the
client entry and the two startup-hook lines as described below.

**1. Unwire your MCP client**

Codex:

```bash
codex mcp remove pymol
```

Cursor: edit `~/.cursor/mcp.json` and delete the `"pymol"` entry under `mcpServers` (leave any other servers intact). Quit Cursor (`Cmd+Q`) and reopen.

Claude Code:

```bash
claude mcp remove pymol --scope user
```

**2. Remove the PyMOL startup hook**

Delete these two lines from `~/.pymolrc.py`:

```text
# co-pymol: auto-start MCP server on PyMOL launch
from co_pymol import __init_plugin__; __init_plugin__()
```

If that was the only thing in the file, you can delete `~/.pymolrc.py` entirely.

**3. Uninstall the package**

```bash
/Applications/PyMOL.app/Contents/bin/python -m pip uninstall pymol-plugin-bridge
```

That removes both packages from this Python environment. To disable only the
PyMOL agent, keep the shared distribution and remove its startup hook and client
entry instead.

**4. Restart PyMOL**

A full quit + relaunch removes the `MCP server running on...` line. Saved render
folders, sessions, receipts and preferences remain available until you remove
them yourself.

## Notes

- **`run()` security** — executes arbitrary Python locally with full imports,
  file I/O, and PyMOL access. Only connect trusted MCP clients, and keep the
  server on loopback unless you have separately secured the network path.
- **Dev setup (optional)** — install `.[pymol,dev]` into PyMOL's Python (using
  `--ignore-requires-python` for Python 3.10), then run
  `python -m pytest tests/co_pymol` with that interpreter. See
  [AGENTS.md](../../AGENTS.md). The live export test uses an independent headless
  PyMOL instance and runs whenever `pymol2` is available.
