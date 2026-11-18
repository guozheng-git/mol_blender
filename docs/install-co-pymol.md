# Install through co-PyMOL

Use this route to connect an agent to the user's live desktop PyMOL session.
Run commands from the repository root. Reuse an existing checkout; if cloning,
use the requested revision containing both `co_pymol/` and `blender_render/`.

## Connect PyMOL to one client

Locate PyMOL's own Python and the requested MCP client. On a standard macOS
installation, use the path below. For conda or another installation, use that
PyMOL environment's interpreter. Set `PYMOL_CLIENT` to exactly one of `codex`,
`claude` or `cursor`, based on the user's request or current client.

```sh
PYMOL_PYTHON=/Applications/PyMOL.app/Contents/bin/python
PYMOL_CLIENT=codex
"$PYMOL_PYTHON" -c 'import sys, pymol, pymol2; print(sys.executable); print(sys.version)'
"$PYMOL_PYTHON" -m pip show co-pymol
```

PyMOL's Python must be 3.10 or newer. A missing `co-pymol` distribution is normal;
if the old standalone distribution is present, uninstall only that distribution
with `"$PYMOL_PYTHON" -m pip uninstall -y co-pymol` before installing the merged
package. The current distribution is named `pymol-plugin-bridge`.

```sh
"$PYMOL_PYTHON" -m pip install --user -e ".[pymol]" --ignore-requires-python
"$PYMOL_PYTHON" -m co_pymol setup "$PYMOL_CLIENT"
```

Omit `--user` inside a dedicated conda/virtual environment that owns PyMOL.
`--ignore-requires-python` permits the PyMOL client to use Python 3.10; the
renderer still needs its separate Python 3.11 environment. Keep this checkout
at its installed path because the package is editable. There is no `co-pymol`
console executable in the merged distribution; use `python -m co_pymol`.

The setup command preserves other MCP entries, adds the startup hook to
`~/.pymolrc.py`, and registers the stdio proxy for the selected client. Codex and
Claude Code need their CLI on `PATH`; Cursor setup writes `~/.cursor/mcp.json`.
Leave the default host/port unchanged. These setup commands can be rerun.

## Enable Blender rendering when requested

Basic PyMOL control needs no Blender download. For local Blender renders, ensure
`uv` is on `PATH` and run the following in this checkout. `uv` uses the pinned
Python version in `.python-version`.

```sh
uv sync --locked --inexact
.venv/bin/blender-render doctor
```

`--inexact` preserves any development/MCP extras already installed in `.venv`.
Reuse a working renderer configuration. For a fresh native runtime on Apple
Silicon macOS or Linux x86_64, install and configure it with:

```sh
.venv/bin/blender-render setup --install-runtime --pymol-python "$PYMOL_PYTHON"
.venv/bin/blender-render doctor
```

Setup records the renderer's Python for co-PyMOL's detached worker. If the runtime
already exists but this interpreter was not registered, use `setup` without
`--install-runtime`. To configure existing Blender/dependency paths, follow
[the renderer setup instructions](../README.md#install-and-configure).

Preserve the chosen `BLENDER_RENDER_HOME`. co-PyMOL discovers this checkout's
`.runtime/config.json` first, then the configured/default user root. If the
desktop app does not inherit a custom environment variable, merge absolute
`render_home` and `render_python` paths into
`~/.config/co-pymol/blender.json`, preserving the other settings. For lab-only
rendering, follow [the shared cluster client setup](shared-cluster.md) instead
of downloading a local Blender runtime; setup alone does not submit a job.

## Verify and report

Preserve unsaved PyMOL work before a full quit/relaunch, and open a new session in
the selected client. PyMOL should print
`co-pymol: MCP server running on http://127.0.0.1:8766/sse`.

- Check only the selected client: `codex mcp list`, `claude mcp list`, or Cursor's
  MCP settings. It should have a `pymol` entry using `-m co_pymol proxy`.
- Call a real PyMOL tool, such as `run` with `print(cmd.get_version())`, from that
  client. If the current chat needs restarting to discover tools, report that
  remaining step instead of claiming the connection was verified.
- If rendering was requested, check `blender_presets` and complete a small
  preview through `blender_render`. Retain the PNG, manifest and resolved style;
  `doctor` and preset enumeration alone do not verify rendering.

Report the client, interpreter, renderer configuration (if configured), checks
that passed, and any remaining restart or connection issue. See the
[co-PyMOL guide](co-pymol/README.md) for upgrades and troubleshooting.
