# Third-party notices

The plugin's own scripts, page template and instructions are MIT-licensed, like the
repository's client code. `skills/molblender-studio/assets/wheels/` contains the renderer wheel
built from this repository (`MIT AND GPL-3.0-or-later`). Its license files and notices,
including the GPL text, are inside the wheel. See the repository's `THIRD_PARTY_NOTICES.md`
for the Blender, Molecular Nodes and Python runtime components that `setup` installs.

| Component | How it is used | License |
| --- | --- | --- |
| Mol* 5.12.0 stylesheet | Bundled as `assets/molstar-5.12.0.css` and inlined into viewer pages | MIT |
| Mol* 5.12.0 viewer script | Loaded by viewer pages from cdn.jsdelivr.net, not bundled | MIT |
| MolViewSpec | Scene format used by the viewer page | MIT (part of Mol*) |
| Open-source PyMOL (`pymol-open-source-whl`) | Installed by `setup` from PyPI | BSD-like, see upstream LICENSE |
| Blender `bpy` 5.0.1, Molecular Nodes 4.5.10 | Installed by `setup` from PyPI | GPL-3.0-or-later |
| Instrument Sans, JetBrains Mono | Loaded by viewer pages from Google Fonts | SIL Open Font License 1.1 |

## Mol* license

```
The MIT License

    Copyright (c) 2017 - now, Mol* contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

Structures fetched from the PDB or AlphaFold DB keep their upstream terms (PDB data is CC0;
AlphaFold DB predictions are CC BY 4.0).
