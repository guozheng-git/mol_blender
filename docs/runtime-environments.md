# Isolated local and Linux environments

For a first installation, use the [co-PyMOL](install-co-pymol.md) or
[Structure Studio](install-claude.md) guide. This page covers environment
boundaries, dependency maintenance and Linux image builds.

## Controller environment

The controller uses `.python-version` (Python 3.11.15) and `uv.lock`. Run
`uv sync --locked --extra dev --extra mcp` in a checkout. Add `--extra hosted`
only when working on the optional delivery adapter. uv itself is installed
explicitly; the project does not bootstrap it or modify global Python packages.

## PyMOL and Blender runtimes

Structure Studio setup creates two separate uv environments below
`STRUCTURE_STUDIO_HOME`: PyMOL and Blender's `bpy` runtime. Their complete,
hash-locked dependency sets are shipped under the skill's `references/`.
PyMOL 3.1.0.4 and the renderer both pin NumPy 1.26.4. These environments must
remain separate. The renderer wheel is installed with `--no-deps` after syncing
the renderer lock, so it cannot silently upgrade the runtime. Setup is explicit;
viewing, doctor and rendering never install packages or missing system libraries.

For development, use a repo-local installation:

```sh
export STRUCTURE_STUDIO_HOME="$PWD/.runtime/studio"
python plugins/structure-studio/skills/structure-studio/scripts/studio.py setup --only pymol
```

Full Studio setup requires a built plugin containing its renderer wheel; use the
staged plugin produced by the release builder. Alternatively configure an existing
Blender executable, dependency directory and PyMOL interpreter through
`blender-render setup`. This preserves a compatible native Blender installation.
The native Blender installer uses the existing version/hash lock with uv `--target`
for Blender's Python dependencies and never runs pip/ensurepip in the caller.

## Updating dependency pins (maintainers)

To refresh dependency locks deliberately, then rerun the affected runtime checks:

```sh
uv lock
uv pip compile --python-version 3.11 --universal --generate-hashes runtime/pymol.in \
  -o plugins/structure-studio/skills/structure-studio/references/pymol-requirements.txt
uv pip compile --python-version 3.11 --universal --generate-hashes runtime/render.in \
  -o plugins/structure-studio/skills/structure-studio/references/render-requirements.txt
```

## Linux and Apptainer

The Apptainer definition consumes the exact staged plugin from `dist/release`
and pins its Linux amd64 Debian base by digest. It builds new Linux environments
with the same Python and Python-package pins; it does not copy `.venv`,
`.runtime`, binaries or caches from a Mac.
System libraries and FFmpeg are installed only during this explicit image build.
Debian repository packages are not snapshot-pinned, so bit-identical OS images
are not claimed. Record `dpkg-query -W`, FFmpeg and GPU/driver versions with Linux
acceptance results; use a dated Debian snapshot if OS-level reproducibility is required.

```sh
python scripts/build_release.py --output dist/release
apptainer build renderer.sif containers/Apptainer.def
apptainer exec --cleanenv --nv --bind "$PWD:/work" renderer.sif \
  blender-render render /work/examples/1sps.cif --width 320 --height 240 \
  --samples 8 -o /work/renders/linux-smoke.png
```

`--nv` is for NVIDIA Linux hosts; omit it for CPU execution. The image build and
real Linux/CUDA rendering must be validated on the target Linux deployment.
Mac testing does not establish Linux/CUDA execution. Reuse the same image and
pinned sources for CPU/GPU tests, and record the exact SIF checksum alongside the
release manifest.

The [optional shared cluster service](shared-cluster.md) runs that same image for
each Slurm job, directly using its existing uv Python. It also supports binding an
already configured Linux Studio runtime read-only at its original absolute path.
Neither mode installs dependencies per job. `RELEASE_DIR` can select a freshly
built artifact directory: `apptainer build --build-arg RELEASE_DIR=dist/cluster-release
renderer.sif containers/Apptainer.def`.

Environment reuse is separate from a warm Blender process. Default local
renders and Slurm jobs start a fresh Blender process for each request.
The cluster gateway stays up, but each completed Slurm job releases its GPU
allocation. Explicitly select `--warm` in the CLI or `warm=1` in PyMOL to reuse
the [optional local GPU worker](local-worker.md). It uses the same configured
runtime without installation, including inside a single Linux Apptainer session.
