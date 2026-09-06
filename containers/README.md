# Linux runtime

Build from the repository root on Linux x86-64. The base image digest, Debian
snapshot date, Blender SHA256, Python package hashes, and CCD snapshot are pinned.
The image contains the server, Blender, Molecular Nodes, and an open-source PyMOL
converter. A Mac client does not need this image.

```sh
apptainer build blender-render-0.2.0b1-linux-x64.sif blender_render/blender_bridge.def
```

The validated IPD build uses unprivileged Apptainer with its configured fakeroot
support. A site must permit the container's network and PID isolation to use the
public PSE converter. Ordinary rendering needs `--nv` and a GPU allocation:

```sh
apptainer run --nv blender-render-0.2.0b1-linux-x64.sif serve \
  --token-file /private/path/token --root /shared/private/service
```

The same pinned runtime can be built as an OCI image on a Docker-enabled host:

```sh
docker build --platform linux/amd64 -f containers/Dockerfile \
  -t blender-render:0.2.0b1 .
```

The SIF build is the validated Linux distribution. Do not claim an OCI registry
tag was tested or published solely because the Dockerfile exists.

## Notices and sources

The image retains `/usr/share/doc/*/copyright` and records the exact Debian binary
and source versions in `/opt/notices/debian-packages.tsv`. Application/runtime
source archives and unmodified licenses are collected with:

```sh
python scripts/collect_sources.py corresponding-sources
apptainer exec blender-render-0.2.0b1-linux-x64.sif \
  cat /opt/notices/debian-packages.tsv > debian-packages.tsv
python scripts/collect_debian_sources.py debian-packages.tsv corresponding-sources/debian
```

The Debian collector resolves exact source versions through the official
snapshot archive, verifies its file hashes, and writes a SHA256 inventory. Keep
that bundle beside any distributed binary image, together with these build
scripts and the bridge source. See [third-party notices](../THIRD_PARTY_NOTICES.md).
