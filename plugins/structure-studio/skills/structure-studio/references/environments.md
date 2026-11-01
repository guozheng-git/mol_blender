# Environments and setup

`studio.py setup` installs into `STRUCTURE_STUDIO_HOME` (default `~/.cache/structure-studio`):

| Path | Contents |
| --- | --- |
| `pymol/` | venv with `pymol-open-source-whl==3.1.0.4` (about 15 MB). Uses uv-managed Python 3.11.15 in its own environment |
| `render/` | Python 3.11 venv with `bpy==5.0.1`, Molecular Nodes 4.5.10, NumPy 1.26.4, Biotite 1.6.0 and the bundled renderer wheel (about 1.2 GB) |
| `bin/blender` | A small shim that runs the renderer's Blender scripts through the `bpy` module |
| `blender-render/` | Renderer configuration (`BLENDER_RENDER_HOME`) |
| `state.json` | What is installed; `studio.py doctor` prints it |

`setup --background` starts the install detached and records it in `setup.lock` (its log is `setup.log`). `doctor` reports `setup_running`. `session`, `workspace FILE.pse` and `render` wait until what they need is ready rather than failing. "Render ready" also requires the installed renderer to match the plugin's bundled wheel, so after a plugin update `setup` reinstalls just the renderer.

Versions are pinned to the renderer's `runtime.lock.json`. The renderer verifies the Blender, Molecular Nodes, NumPy, Biotite and chemical-component-dictionary versions on every render.

## Platform limits

- `bpy` 5.0.1 wheels exist only for Linux x86_64 (glibc 2.28+), Apple Silicon macOS and Windows. Setup stops with a clear message on Linux ARM or Intel Macs. The viewer and PyMOL sessions still work there.
- Rendering uses Metal on Apple Silicon, CUDA/OptiX when an NVIDIA GPU is visible, and otherwise CPU.
- Setup never runs apt, sudo or user/system pip. Missing Linux libraries are reported for an administrator to install, or are installed only while explicitly building the Apptainer image. Install uv explicitly before setup.

## Network

- Installing needs PyPI (`pypi.org`, `files.pythonhosted.org`). If no Python 3.11 is present, uv downloads one from GitHub (`github.com`, `objects.githubusercontent.com`). claude.ai's default "package managers" egress allows these.
- Fetching structures needs `files.rcsb.org`, `www.ebi.ac.uk` or `files.pdbj.org` for PDB entries and `alphafold.ebi.ac.uk` for AlphaFold DB. claude.ai blocks these unless the user adds them under allowed domains or enables all domains. Otherwise ask for an uploaded file.
- The viewer page loads Mol* from `cdn.jsdelivr.net` and fonts from Google Fonts in the viewer's browser, not in the sandbox.

## claude.ai

A fresh sandbox may lack a runtime. Run `setup` only after the user authorizes installation; inspecting or viewing structures must not trigger it. The viewer needs no setup. Long commands can hit the tool's time limit, so run renders in the background and poll if needed. Present deliverables from `/mnt/user-data/outputs`.

## Claude Code

Installs persist between sessions. To reuse an existing renderer checkout that is already configured (for example with real Blender.app and GPU), run:

```bash
python3 "$S" setup --use-existing /path/to/blender_render/.venv/bin/blender-render \
  --blender-render-home /path/to/blender_render/.runtime
python3 "$S" setup --only pymol
```

`setup` without `--use-existing` always installs the managed `bpy` runtime. Delete `~/.cache/structure-studio` to start over.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Install uv explicitly` | Install uv with your approved toolchain and add it to PATH; setup has no pip bootstrap fallback. |
| `uv venv` cannot find or download Python 3.11 | GitHub downloads are blocked; install Python 3.11 another way or allow GitHub. |
| `needs system libraries … could not be installed` | Install the printed Debian packages, then rerun `setup`. |
| `Development runtime does not match runtime.lock.json` | A different `bpy`/Molecular Nodes version leaked into the venv; delete `render/` and rerun `setup`. |
| Render times out | Use `--rough`, a smaller `--width/--height`, fewer `--samples`, or run it in the background with a larger `--timeout`. |
| Chain-specific preset looks uniform | Expected on structures without those chain IDs; choose a general preset. |

Both Python environments use complete, hash-pinned requirements (`pymol-requirements.txt` and `render-requirements.txt`). Their direct inputs live in `runtime/*.in` in the source repository. Rebuild from these same pins on Linux; never copy a Mac venv. Setup is an explicit mutation, while doctor, workspace viewing and rendering do not install anything.
