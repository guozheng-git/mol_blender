# Install molblender-studio in Claude

Use this route for molblender-studio's file-based molecular workspaces, PyMOL
sessions and Blender renders. For control of an existing desktop PyMOL session,
use [co-PyMOL](install-co-pymol.md).

You can follow these steps yourself or ask your agent to run them. Run the build
commands from the repository root with `uv` on `PATH`, using the revision you
intend to install. The source marketplace does not contain the renderer wheel,
so registering this repository directly is insufficient for a
complete install. Keep generated packages outside tracked source directories.

## Build a complete package

```sh
uv sync --locked --inexact
STUDIO_PACKAGE="$PWD/dist/molblender-studio-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python scripts/build_plugin.py --output "$STUDIO_PACKAGE"
```

`--inexact` preserves existing extras in `.venv`. The output directory must be
new. It contains the staged `plugin/molblender-studio/`, its bundled wheel,
`molblender-studio-plugin.zip`,
and `molblender-studio-skill.zip`. Reuse these outputs for the selected Claude
surface. For a release tied to a clean committed revision, use
`scripts/build_release.py` and `scripts/verify_release.py` instead; an installation
does not require committing or publishing your working tree.

## Claude Code: install from the staged package

First inspect `claude plugin marketplace list --json` and
`claude plugin list --json`. If molblender-studio is already installed, inspect
its loaded directory and update that installation rather than creating a second
copy. The commands below are for a new install.

Create a local marketplace pointing at the complete staged plugin:

```sh
.venv/bin/python - "$STUDIO_PACKAGE" <<'PY'
import json
import sys
from pathlib import Path

stage = Path(sys.argv[1])
market = json.loads(Path(".claude-plugin/marketplace.json").read_text())
entry = next(p for p in market["plugins"] if p["name"] == "molblender-studio")
entry["source"] = "./plugin/molblender-studio"
market["plugins"] = [entry]
directory = stage / ".claude-plugin"
directory.mkdir(exist_ok=True)
(directory / "marketplace.json").write_text(json.dumps(market, indent=2) + "\n")
PY
claude plugin validate "$STUDIO_PACKAGE/plugin/molblender-studio"
claude plugin validate "$STUDIO_PACKAGE"
claude plugin marketplace add "$STUDIO_PACKAGE"
claude plugin install molblender-studio@mol-blender --scope user
claude plugin list --json
```

Keep the staged directory: it is the registered local marketplace source. Use
`project` or `local` scope instead of `user` if that is where you want it installed.
For updates, refresh the package at the registered source, then use
`claude plugin marketplace update mol-blender` and
`claude plugin update molblender-studio@mol-blender --scope user`, matching the
existing installation's scope. Preserve other marketplaces and plugins.

From the list result, verify `molblender-studio@mol-blender` is enabled and has
no load errors. Use `readFromFolder` when present, otherwise `installPath`, as the
loaded plugin directory. Check it contains a wheel under
`skills/molblender-studio/assets/wheels/`. Set `STUDIO_SCRIPT` to its absolute
`skills/molblender-studio/scripts/studio.py` path. This verifies the installed
copy, not just the build directory. Open a new Claude Code session to load it.
The [official CLI reference](https://code.claude.com/docs/en/plugins/cli-reference)
documents registration, scopes, updates and the JSON listing fields.

## Claude web/Desktop: upload the package

Upload `molblender-studio-plugin.zip` through Claude's custom plugin upload in
**Customize → Plugins**. If using the custom skill upload instead, upload
`molblender-studio-skill.zip` through **Customize → Skills**. Choose the archive
matching that surface; the plugin ZIP includes the commands and plugin manifest,
while the skill ZIP has `SKILL.md` at the skill folder's root. See Claude's
[plugin instructions](https://support.claude.com/en/articles/13837440-use-plugins-in-claude)
and [skill instructions](https://support.claude.com/en/articles/12512180-use-skills-in-claude).

After loading the plugin/skill, ask Claude to locate its `scripts/studio.py` in
its execution environment and use that absolute path as `STUDIO_SCRIPT`.
Installing in local Claude Code does not upload the plugin to your Claude account.
If an agent cannot access the upload UI, it should provide the built ZIP's path
and the remaining upload step; installation is only complete after the upload
and verification in the target environment.

## Set up the runtime in the target environment

Run these with `STUDIO_SCRIPT` pointing to the installed script on the machine
or sandbox where Claude will run it:

```sh
python3 "$STUDIO_SCRIPT" doctor
```

The viewer needs no runtime installation. For a requested full render setup,
ensure `uv` is on that environment's `PATH`, then run:

```sh
python3 "$STUDIO_SCRIPT" setup --background
python3 "$STUDIO_SCRIPT" doctor
```

Setup downloads the pinned PyMOL and Blender runtimes into
`MOLBLENDER_STUDIO_HOME` (default `~/.cache/molblender-studio`). Use `setup --only
pymol` for PyMOL-only work. Check `setup_running` and the returned `setup_log`;
later session/render commands wait for an active setup. Full setup is ready when
`pymol_ready` and `render_ready` are true. `render_installed` alone is insufficient
because the installed renderer must match the bundled wheel.

If a working local renderer already exists, reuse it with `setup --use-existing
/absolute/path/to/mol-blender --mol-blender-home /absolute/config/root`
instead of installing another Blender runtime; run `setup --only pymol` if
Studio's PyMOL environment is missing. Keep Linux environments at their original
absolute paths. Platform support, prerequisites and setup troubleshooting are in
[the environment guide](../plugins/molblender_studio/skills/molblender-studio/references/environments.md).

Verify the requested workflow from the installed plugin: generate a workspace
for viewing, or complete a small render when rendering was requested. Report
the Claude surface, loaded plugin path, runtime readiness, actual checks and any
remaining upload/restart step. Registration and `doctor` do not prove a render
completed.

## Upgrade from Structure Studio

Rebuild and reinstall the plugin as `molblender-studio@mol-blender`, then point
`STUDIO_SCRIPT` at the newly installed `scripts/studio.py`. Keep existing runtime
and configuration directories at their original absolute paths: virtualenvs,
managed Python interpreters, Blender shims and saved configuration contain those
paths. Renaming or moving the cache directory is not a supported migration.

To reuse the previous default Studio home in place:

```sh
export MOLBLENDER_STUDIO_HOME="$HOME/.cache/structure-studio"
```

Use your original absolute directory if you customized it. Old environment
variable names are no longer read; set the new variable in every environment
that launches Studio. Changing it alone does not update the renderer or its
saved registration.

For an **external renderer**, upgrade its existing checkout or installation
first (`uv sync --locked --inexact` for a checkout), then register its new
executable and original configuration directory explicitly:

```sh
python3 "$STUDIO_SCRIPT" setup --use-existing /absolute/path/to/.venv/bin/mol-blender \
  --mol-blender-home /absolute/path/to/original/config/root
python3 "$STUDIO_SCRIPT" doctor
```

This preserves Studio's PyMOL configuration. A previous external-renderer state
cannot be repaired by plain `setup` or `setup --background`; those commands now
report the re-registration command instead of crashing.

For a **Studio-managed renderer**, keep the same Studio home and run
`python3 "$STUDIO_SCRIPT" setup --background`, then check `doctor`. Setup reuses
the environments in place, installs the renamed renderer and writes its new
registration. It creates a new `mol-blender/` renderer configuration directory;
copy any user presets from the old `blender-render/presets/` into
`mol-blender/presets/` separately.

For terminal renderer commands, set `MOL_BLENDER_HOME` to your original
configuration directory instead of moving it. If you used separate lab profiles,
set `MOL_BLENDER_CLUSTER_HOME` to their original root as well. Verify readiness
and a small render before removing any old installation.
