# Local branch validation

Executed on Apple Silicon macOS, 2026-09-28. Package version: 0.3.0.

## Automated checks

- Source suite: **147 passed**, with two FastMCP/Authlib deprecation warnings.
- Built wheel installed into a separate environment: **147 passed**. Tests were
  copied into a temporary directory outside the checkout and run with Python `-I`.
  Module paths confirmed imports from the installed wheel. Two inherited test
  fixtures were updated to locate JSON relative to the imported package instead
  of assuming the current directory was the source checkout.
- All **54 packaged code/assets** matched source bytes; every packaged file digest
  matched `engine-manifest.json`.
- Skill frontmatter/name validation passed; the Claude skill symlink resolves to
  the canonical Codex skill. Internal module imports and documentation links checked.
- `git diff --check` passed.

Principal commands:

```sh
python -m pytest -q
python -m build --wheel
# Install dist/pymol_plugin_bridge-0.3.0-py3-none-any.whl into an isolated environment.
# From a separate temporary directory containing a copy of tests/:
/path/to/installed/python -I -m pytest tests -q
```

## Executed local rendering

The runtime verified Blender **5.0.1**, Molecular Nodes **4.5.10**, Python **3.11**,
NumPy **1.26.4**, Biotite **1.6.0**, and the committed CCD SHA-256. Validation used a
separate `BLENDER_RENDER_HOME` under ignored `.validation/`; existing user runtime
configuration and agent MCP registrations were not changed. A temporary conda-forge
PyMOL 3.1 environment supplied the headless file-export interpreter.

- Rendered the public `examples/1sps_pose.pse` through CLI export and the default
  pastel preset at 256 × 256, 8 samples, with a saved Blender scene.
- Ran `bridge_render` inside a real `pymol2.PyMOL()` instance on that public session.
  Export, configured CLI interpreter, Blender subprocess and `cmd.load_png` all
  completed. Both manifests report `dss_ran=true`, `polymer_backend=mn_cartoon`,
  `ligand_backend=mn_native_sticks` and `ligand_atom_spheres=false`.
- Rendered dried green paint using a public CIF bundle and inspected the PNG.
- Used that generated green image as a skill-workflow smoke-test reference: recorded
  visible observations, generated and validated a full candidate JSON, saved
  `user/reference-smoke`, reloaded it in a new process and rendered it. The inspected
  PNG retained the same visible composition and green material. This is workflow
  validation with a known generated example, not proof of arbitrary image matching.
- Started the **installed wheel** as a real stdio MCP subprocess with Python `-I`.
  Listed all four tools, rendered the public PSE with the generated style, then
  called `read_render`. Decoded MCP image bytes matched the saved PNG exactly.

Example reproduction after runtime configuration:

```sh
blender-render render examples/1sps_pose.pse --width 256 --height 256 --samples 8 \
  --save-blend -o renders/pastel.png
blender-render export examples/1sps_pose.pse -o renders/bundle.json
```

Local smoke artifacts/logs are in ignored `.validation/`: `pastel-03.png`,
`pymol-bridge.png`, `green.png`, `reference-reloaded.png`, `wheel-mcp.png`, the sibling
`.render/` directories, `candidate.json`, `decision.json`, `installed-tests-final.log`
and `stdio-render.log`. They contain local runtime paths and are not committed.

## Limits and observed failures

An initial existing CCD copy failed the pinned checksum. A matching existing copy
was selected; the lock was unchanged. An initial manually assembled default-preset
bundle failed the required DSS check. Repeating with real PyMOL export passed.
These failures were not counted as successful renders.

Previews were intentionally small with low sample counts; they are not publication
quality comparisons. PyMOL was exercised headlessly, not visually in a GUI window.
Actual Claude/Codex client registration, skill discovery and in-chat image display
were not exercised. Stdio protocol and image-byte delivery were tested independently.
No remote renderer, deployment, paid compute, push or publication was used.

## Fresh Mac installation

Executed 2026-09-28 with a new, repo-local `BLENDER_RENDER_HOME=.runtime`.
The managed installer downloaded and checksum-verified the Blender 5.0.1 Apple
Silicon DMG, mounted it, copied a new Blender.app into `.runtime/runtime/`, detached
the disk image, and installed the hash-pinned Python wheels into a new
`.runtime/runtime/site/`. The installed Biotite CCD matched the existing lock
without copying a database from another Blender installation. No installer code
changes or manual runtime repairs were needed.

A separate PyMOL environment was installed with Pixi under `.runtime/pymol/` and
configured as the file-export interpreter. The existing project `.venv` was reused
as the CLI environment. Download/package caches could be reused, but all managed
render-runtime destinations were new. This verifies a fresh runtime installation
on this Apple Silicon host, not every supported OS or a pristine operating system.

```sh
export BLENDER_RENDER_HOME="$PWD/.runtime"
blender-render setup --install-runtime
pixi init .runtime/pymol --channel conda-forge
pixi add --manifest-path .runtime/pymol/pixi.toml python=3.11 pymol-open-source
blender-render setup --pymol-python "$PWD/.runtime/pymol/.pixi/envs/default/bin/python"
blender-render render examples/1sps_pose.pse --width 512 --height 512 --samples 32 \
  --save-blend -o renders/fresh-install.png
```

The render completed on the Apple M4 Metal GPU, and its PNG was visually inspected.
Manifest checks confirmed the Blender executable and the Molecular Nodes, NumPy
and Biotite module paths all came from this checkout's `.runtime/`. Runtime versions
matched the lock; DSS assignment, native cartoons and sphere-free ligand sticks
passed. The PNG and Blender scene both exist. PNG SHA-256:
`fe6b2d171a231f87107a02b4cedd00f10f8d22962c653cc3a03ea799ab761e52`.

The local `.runtime/activate` helper selects this checkout's CLI environment and
configuration when sourced. Logs are `.runtime/install.log`,
`.runtime/pymol-install.log` and `.runtime/render-smoke.log`. Installation files,
configuration, logs and renders remain ignored by Git. The source and runtime
installer are unchanged; only this evidence and the README were updated.
