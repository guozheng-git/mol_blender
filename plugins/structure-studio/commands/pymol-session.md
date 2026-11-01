---
description: Get a PyMOL session (.pse) and figure for a structure or an earlier render
argument-hint: <structure, workspace or render> [overview | surface | site | secondary | confidence]
---

Use the structure-studio skill for: $ARGUMENTS

- For an earlier render's session, use Recipe D steps 1–4.
- For the current workspace view, read the scene (Recipe B step 1), then run `studio.py session STRUCTURE --scene … --camera … --png`.
- Otherwise run `studio.py session FILE --style … --png`.

Show the PNG and hand over the `.pse`.
