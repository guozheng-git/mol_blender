# Third-party software

The root MIT license covers the independently authored client, export, preset-authoring,
and plugin code. The Blender Python API scripts listed below are
GPL-3.0-or-later, as marked in their file headers. The wheel contains both
components and declares `MIT AND GPL-3.0-or-later`.

GPL scripts: `blender_custom_render.py`, `blender_runtime.py`, `geometry.py`,
`paint.py`, `reference_effects.py`, `animation_blender.py`, `developer_blender.py`,
`runtime_initialize.py`, `runtime_probe.py`, `warm_blender.py`, `cycles_device.py`,
`scene/capture.py`, `editor_controls.py`, `editor_blender.py`, and `editor_render.py`,
all under `blender_render/`.
The full GPL text is in `LICENSES/GPL-3.0-or-later.txt`. This separation follows
[Blender's published guidance for Python scripts](https://www.blender.org/support/faq/).
It does not relicense upstream dependencies, bundled node assets, or their
modifications. All render-side source remains available in the release.

| Component | License | Source |
| --- | --- | --- |
| Blender 5.0.1 | GPL-3.0-or-later | https://projects.blender.org/blender/blender |
| Molecular Nodes 4.5.10 and its node assets | GPL-3.0-or-later | https://github.com/BradyAJohnston/MolecularNodes |
| Open-source PyMOL | BSD-like, see upstream LICENSE | https://github.com/schrodinger/pymol-open-source |
| Biotite | BSD-3-Clause | https://github.com/biotite-dev/biotite |
| Biotraj 1.2.2 | LGPL-2.1-or-later | https://github.com/biotite-dev/biotraj |
| MDAnalysis 2.10.0 | LGPL-3.0-or-later; bundled contributions retain their notices | https://github.com/MDAnalysis/mdanalysis |
| GridDataFormats 1.1.0 | LGPL-3.0-or-later | https://github.com/MDAnalysis/GridDataFormats |
| Gemmi 0.7.5 | MPL-2.0 | https://github.com/project-gemmi/gemmi |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| tqdm | MPL-2.0 AND MIT | https://github.com/tqdm/tqdm |
| FastMCP | Apache-2.0 | https://github.com/jlowin/fastmcp |

Blender and Molecular Nodes retain their upstream licenses. Do not describe the
combined runtime as MIT-only. Rendered images are not relicensed by this notice.
A saved Blender scene can contain Molecular Nodes node groups, whose assets retain
their upstream license. All render-side source remains available in this repository.

`blender_render/runtime-sources.json` records runtime source versions, URLs and
checksums. `scripts/collect_sources.py` downloads and checks those archives and
collects their unmodified license files. `docs/dependency-source-manifest.json`
retains the upstream release's historical notice inventory, including dependencies
no longer used by this local branch. Runtime setup is an explicit user action.

Example structures are obtained from the RCSB Protein Data Bank and carry their
accession and citation in the example manifest. User-uploaded structures and
reference images are not included in the public source distribution.
