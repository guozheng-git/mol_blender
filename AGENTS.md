# Blender Render contributor guide

Keep one rendering implementation in `blender_render`. Local, Apptainer/HPC, and Modal execution must consume the same exported data, preset schema, and renderer entry point. Compatibility entry points may adapt arguments but must not implement separate styles.

Rendering requirements:

- Export CIF first, with PDB only as an explicit supported fallback.
- Assign secondary structure in PyMOL before export. Preset 1 must use cartoon secondary structure and must not silently downgrade to a tube.
- Use native Molecular Nodes sticks for ligands, without atom spheres or legacy geometry backends.
- Preserve enabled selections, object transforms, atom colors, and the full PyMOL camera view, FOV, projection, and framing.
- Keep preset 1 as the default. Rough mode changes performance settings only.
- Consume every accepted preset field. Reject unsupported fields; do not retain dead knobs.
- Return and display the PNG in PyMOL, optionally save a Blender scene, and write a traceable manifest.
- Clean per-render temporary directories unless the caller explicitly requests debugging retention.

Service requirements:

- Bind private workers to loopback, authenticate every job, and access HPC workers through SSH.
- Use one persistent FIFO queue and one Blender worker per service. Support multi-client attach, cancellation, health checks, restart recovery, explicit stop, and 15-minute idle shutdown.
- Persist ownership and idempotency. An ambiguous paid execution must not be silently retried.
- Commit cost reservations before dispatch. Credits and operating budgets are lifetime ceilings and never refill on restart.
- Validate inputs before GPU work. Convert untrusted PSE files only in a short-lived sandbox without network or credentials.
- Keep the SQLite controller single-writer. Drain and stop the old controller before a deployment upgrade.
- Do not route private lab data through the shared service.

Verify relevant changes with `python -m pytest`. Installation checks must use a built wheel outside the checkout. Actual Mac, HPC, Modal, OAuth, Codex, and ChatGPT checks require executed evidence; mocks do not satisfy those release gates. Use only public structures for examples. Do not commit credentials, personal paths, research sessions, render caches, or generated environments. Preserve upstream notices and corresponding source availability.

Commit coherent milestones. Do not publish an unrestricted service or release until the release report passes every required gate. Publisher portal review is an external gate, not something a local manifest can establish.

Implementation planning: read [the v3 architecture plan](docs/plans/blender-render-v3.md) and [the execution record](docs/plans/blender-render-progress.md) before continuing PR #1. The historical baseline is reference context, not a checkout/reset instruction. Follow the active bounded milestone and preserve the security and executed-validation requirements above.
