# Blender Render: Python engine development, JSON execution bridge, preset selection, and backend plan

Revision: **3**. Makes the resolved JSON document the explicit bridge into Blender, separates developer extension from preset-anchored service execution, and adds fast preset selection, reference-image handling, and installed-host performance requirements.

Status: proposed implementation plan, not an implemented API or a new acceptance report. The interfaces and example behaviors below are design targets unless explicitly identified as existing.

Reference baseline: PR #1, commit `b1bb7d1c5d0d832b75c096eeea855b306815d79f`, inspected in the earlier review on September 6, 2026, Pacific time. This document revision does not re-check the current PR head. Current OpenAI host behavior must be verified against the linked official documentation and the installed product during implementation. Keep the repository and deployment private until separately authorized. No new render, deployment, publication, or merge is authorized by this document.

## 1. Product definition

Build one molecular renderer with two workflows and several thin interfaces:

- **Develop rendering behavior.** Let Codex inspect, modify, and extend the shared Python rendering engine in a trusted checkout—not only edit preset JSON. Implement new geometry, representations, materials/shader nodes, lighting, camera behavior, or other scene-building effects when needed; expose supported controls through validated JSON; preview and test the actual behavior; and save reusable presets. JSON-only experimentation and Python-level development are both first-class workflows.
- **Execute an installed design.** Start from the best compatible installed preset, apply validated JSON overrides, render on local, Slurm, or Modal compute, and receive a PNG. Trusted self-run execution may also accept a complete validated JSON document. Use the same execution path from CLI, Python, the PyMOL bridge, or MCP.

The central execution contract is explicit:

> **Every render is driven by a normalized molecular `StructureBundle` plus one fully resolved `RenderSpec` JSON document. The selected execution profile decides where this pair runs. The same Python Blender engine consumes it on local, Slurm, and Modal.**

The JSON is therefore the stable bridge from user intent and presets into Blender. A preset is a named source of JSON defaults. Overrides modify that JSON. The Python engine implements the behaviors named by the JSON. MCP, ChatGPT, Codex, PyMOL, Slurm, and Modal must not implement separate visual styles.

The developer suite is not another renderer. MCP is not a compute backend. PyMOL must not need MCP or ChatGPT to render. Modal must not become the implementation that other backends imitate.

Keep four axes separate:

1. **Input adapter:** local file, attached file, or live PyMOL scene.
2. **Rendering specification:** best-matching preset plus validated overrides, or a complete validated JSON document where policy permits.
3. **Execution profile:** local, Slurm, personal Modal, shared Modal, or a lab gateway.
4. **Trust policy:** developer checkout, trusted self-run service, or restricted hosted service.

Natural-language and image understanding belong in the ChatGPT/Codex/interface layer. Blender receives data, not a prompt. Production services execute installed capabilities; they do not accept arbitrary Python code in a render request.

## 2. Shared architecture

The common path is: prepare molecular input; resolve and validate a render specification; execute the same Python scene builder and renderer on the selected backend; return an artifact record.

| Boundary | Owns | Does not own |
|---|---|---|
| Developer workbench | Trusted Python/JSON edits, preview execution, behavioral tests, preset creation and promotion preparation | Another renderer, production authentication, or automatic hosted deployment |
| Rendering engine | Molecular scene construction, geometry, material/shader construction, lighting, camera application, PNG rendering and optional scene export | MCP, natural-language interpretation, OAuth, Slurm scheduling or Modal accounting |
| Execution service and backends | Job lifecycle, compatible runtime selection, process/allocation management, authorization, cancellation, storage and delivery | Backend-specific copies of visual behavior or preset semantics |
| Python/CLI, PyMOL and MCP interfaces | Input adaptation, shared-client invocation, and result presentation | Their own renderer, schema validator or preset-resolution rules |

These are responsibilities within the existing package, not a requirement for four packages or services. Keep ordinary Python functions and focused modules; introduce only the boundaries needed to remove duplication and test behavior.

### JSON is the execution bridge into Blender

Use one explicit flow for every interface and backend:

```text
natural-language request and optional reference PNG
                    |
                    v
        interface/agent derives a visual brief
                    |
                    v
      best compatible preset + validated overrides
                    |
                    v
          fully resolved RenderSpec JSON
                    |
molecular file or PyMOL scene -> normalized StructureBundle
                    |                    |
                    +---------+----------+
                              v
                    selected execution profile
                 local | Slurm | Modal | lab gateway
                              |
                              v
                    same Python Blender engine
                              |
                              v
                 PNG + optional .blend + diagnostics
```

The renderer entry point should receive the normalized `StructureBundle`, the exact resolved `RenderSpec` JSON, and bounded runtime/output context. It must not branch on the calling interface or on a marketing preset name. A preset identifier is provenance and an input to specification resolution—not a hidden switch that activates backend- or interface-specific code.

Before queue admission or GPU dispatch:

1. resolve the preset and overrides;
2. validate the resulting JSON structurally and semantically;
3. verify that the selected engine/runtime advertises every required capability;
4. snapshot and hash the exact resolved JSON;
5. bind that snapshot to the logical render operation.

The same immutable JSON snapshot must reach Blender on every backend. The manifest should record its schema version, preset source and revision, capability requirements, and specification hash. Blender-side Python must consume every accepted field or reject it; unknown, dead, or unsupported fields must never be silently ignored.

CLI, PyMOL, MCP, and HTTP adapters may translate their own input forms, but they must delegate specification resolution to the same library. Backend adapters may control launch, device, queue, and policy. They may not reinterpret materials, representations, selections, lighting, or camera behavior.

### StructureBundle

A normalized molecular input containing coordinates, stable object/chain mapping, selected state, secondary-structure annotations, colors, transforms, and camera when supplied. Retain the existing CIF-first exporter and supported PDB fallback. File input and a live PyMOL session must converge on this representation.

Keep stable scientific/rendering identity separate from transient export metadata. Random temporary object names, timestamps, download URLs, job IDs, and local filesystem locations must not make otherwise identical exports different. Coordinates, state, real selection, object transforms, colors, and camera changes must remain significant.

An MCP attachment object, a local path, and a live PyMOL session are different input adapters. They are not three bundle formats. Local paths are never interpreted as remote machine paths.

### RenderSpec

The fully resolved, versioned, backend-independent description of the image. It covers materials, representations, selections, lighting, world/background, camera policy, geometry effects, resolution, sampling, and explicitly requested artifacts.

Evolve the existing style schema rather than replacing every preset immediately. Preserve current JSON through a compatibility normalization step. Write a new schema version only for actual incompatible changes; do not relabel an incompatible shape as version 1.

Resolve a named preset and overrides to this exact same specification. A full style JSON is an alternative to a preset selector, not another hidden preset branch. Preserve the originating preset as provenance without accidentally applying its identity-specific validation to a different custom representation.

### ExecutionProfile

How to reach and run compute: local Blender executable/runtime; Slurm host, standard/configured launcher, resources and container; or an authenticated Modal endpoint. Profiles also identify the expected runtime and the deployment policy.

Do not embed SSH settings, credentials, queue names, Modal URLs, budgets, or machine-local output paths in shareable style JSON. Backend adaptation may choose a compatible device, but must not silently change a requested material, selection, or camera.

### RenderResult

A compact record of operation ID, execution and delivery state, resolved preset identity, dimensions, available artifact references, expiry, and the minimum diagnostics needed to explain success or failure. Full provenance is a diagnostic artifact rather than mandatory model-visible text.

PNG is the default artifact. Scene creation and transfer are explicit. Presentation errors are not render errors, and neither permits an automatic replacement render.

## 3. Presets, general JSON, and best-fit selection

Distinguish implementing a capability from configuring it:

| Layer | Purpose | Example design target |
|---|---|---|
| Python implementation | Defines how the engine constructs geometry, materials, shader nodes or other rendering behavior | Implement directional cross-hatching or a new ribbon-edge treatment |
| JSON schema and parameters | Declares and validates the controls consumed by that implementation | Hatch spacing/direction/contrast, edge radius, and molecular selections |
| JSON preset | Saves a reusable configuration of installed capabilities | An ink-illustration design combining those controls with colors and lighting |
| Preset decision | Records why a preset was chosen, which overrides were applied, and whether a new preset is recommended | Choose dried paint for a textured botanical reference, but flag missing cross-hatching |

A new preset does not automatically require new Python. Use JSON when supported controls already express the requested effect. When they do not, extend Python and its schema together rather than approximating unrelated behavior or accepting unused fields. A complete JSON specification must remain usable without registering a named preset on trusted self-run paths.

### Consumer execution is intentionally preset-anchored

The ordinary consumer path should start from an installed preset. This gives ChatGPT, Codex, PyMOL, and hosted services a safe and predictable baseline while preserving flexibility through validated JSON overrides.

Use these policy levels:

| Environment | Allowed rendering input |
|---|---|
| Official/shared hosted service | Approved preset plus explicitly allowed JSON overrides |
| Personal Modal or lab/self-run MCP | Installed preset plus overrides; optionally a complete validated JSON document |
| Local CLI/Python/PyMOL | Installed or user/project preset plus overrides; optionally a complete validated JSON document |
| Trusted developer workbench | JSON changes and Python engine/schema changes, followed by preview and tests |

No execution service accepts arbitrary uploaded Python, Blender scripts, or `.blend` scenes. New Python behavior must be developed in a trusted checkout and explicitly installed or deployed before an execution backend can use it.

A preset should contain:

- stable ID, revision, aliases, and concise visual description;
- supported molecular content and representations;
- material family, palette tendencies, lighting and background characteristics;
- camera policy and expected composition;
- editable JSON paths with units, bounds, and semantic descriptions;
- required engine capabilities and minimum compatible engine version;
- example/reference thumbnails or public fixtures where licensing permits;
- whether it is approved for shared hosted execution.

Use built-in, project, and user-owned catalogs without editing site-packages. Define collisions explicitly, preferably with namespaces such as `builtin/...`, `project/...`, and `user/...`. Execution of a selected preset must snapshot its resolved content so later edits do not change a queued job.

Document override rules. Merge object fields predictably. Replace arrays unless an explicit keyed edit operation is provided; never silently merge representation or light arrays by index. Reject unknown keys and invalid combinations before GPU dispatch. Resolve camera policy explicitly: preserve a saved pose by default, fit a structure without a saved pose, and honor an explicit override. Never discard a custom camera document silently.

### Choose the best compatible preset before rendering

Preset choice should be deliberate and inspectable rather than “default pastel unless the user names green.”

The primary selector lives in the interface/agent layer because ChatGPT and Codex can understand natural language and reference images. The server independently validates the selected preset, overrides, input compatibility, and installed capabilities; it does not need another LLM.

Use this selection sequence:

1. **Extract hard requirements.** Structure content, representation, transparency/background, saved-pose handling, requested scene output, resolution limits, and any explicitly named preset.
2. **Create a visual brief.** Material/finish, palette, edge treatment, surface versus cartoon balance, ligand treatment, lighting, camera/composition, and level of detail.
3. **Filter incompatible presets.** Remove presets whose required capabilities are unavailable, whose content policy is incompatible, or whose editable envelope cannot express a hard requirement.
4. **Rank the remaining presets.** Prefer, in order: an explicit preset name or alias; representation/content compatibility; material and geometry match; palette/background/lighting match; camera/composition match; and the fewest overrides.
5. **Apply the smallest validated override set.** Do not rewrite most of a preset merely to retain its name.
6. **Record the decision.** Include selected preset/revision, fit band, reasons, overrides, unsupported requested features, and whether a dedicated preset should be created.

A compact `PresetDecision` should resemble:

```json
{
  "preset_id": "builtin/dried_paint_green",
  "preset_revision": "1",
  "fit": "high",
  "reasons": ["textured matte paint", "rounded ribbon edges", "transparent background"],
  "overrides": {
    "geometry.secondary_structure_palette.sheet": "#D7A9E3"
  },
  "unsupported_requirements": [],
  "new_preset_recommended": false
}
```

The exact field names may evolve, but the semantics must be shared by CLI/PyMOL/MCP result summaries and tests.

Use three fit bands:

- **High fit:** use the preset and minimal overrides.
- **Medium fit:** use the closest preset only as an explicitly labeled approximation; prefer a rough preview before an expensive final render when practical.
- **Low fit:** do not pretend that many overrides make it the same design. Set `new_preset_recommended=true`, identify the closest base preset and missing capabilities, and route the request to the developer workflow when the user wants a faithful reusable look.

A palette change, resolution change, or supported light adjustment is normally an override. A new shader model, geometry language, representation, composition rule, texture-generation behavior, or recurring bundle of extensive changes is normally a new preset and may require a new Python capability.

### Keep preset choice fast

Do not require `list_presets` and `available_quota` before every normal render. The server enforces admission and limits. The plugin should ship a compact summary of approved built-ins in its skill/tool metadata and cache the full catalog/schema by version or digest. Query the catalog only when it changed, the request is ambiguous, a user names an unknown preset, or a reference image requires broader comparison.

Preset ranking and JSON resolution must occur without starting Blender, allocating Slurm, or waking a Modal GPU. Return the chosen preset and resolved-specification digest with the initial operation response so retries and status calls never need to choose again.

### Reference PNGs and deciding that a new preset is needed

A user may attach a molecular structure and a PNG showing the desired visual look. Treat these as two different inputs:

- the molecular file supplies the `StructureBundle`;
- the reference PNG informs the visual brief and preset decision.

ChatGPT or Codex should inspect the reference image before the render tool call and extract observable properties such as representation, palette, background, material finish, edge profile, lighting direction/softness, depth cues, camera framing, and ligand emphasis. It should not infer hidden Blender implementation details as facts.

By default, do **not** send the raw reference PNG to the Modal/Slurm render worker. Send only the chosen preset, validated overrides, and optionally the compact visual brief/decision for provenance. This reduces latency and data transfer. A future engine capability that explicitly uses an image as a texture is a separate declared file input and security policy.

When the image falls outside the current preset envelope:

1. state that the closest current preset is only an approximation;
2. set `new_preset_recommended=true`;
3. list the important unmatched features;
4. identify the best base preset, if any;
5. recommend creating a dedicated preset in the developer workbench;
6. render an approximation only when the user explicitly wants the closest available result or a low-cost exploratory preview.

In the developer workbench, the reference PNG can guide JSON tuning or Python implementation. The resulting reusable design must still become reviewed Python capabilities plus validated JSON; the reference image itself is not executable configuration and is not a substitute for tests.

## 4. Developer suite: Python engine development and preset authoring

### Explicit requirement

Let Codex inspect, modify, and extend the shared Python rendering engine, including geometry generation and Blender material/shader construction. Expose new capabilities through a validated JSON schema, preview their actual effects, add behavioral tests, and package useful configurations as reusable presets. Support both JSON-only experimentation and Python-level development; do not restrict style authoring to capabilities already present in existing presets.

“Renderer code” means the actual Python that builds and renders the scene. A tool that merely lists presets, edits their colors, or validates their JSON does not satisfy this requirement.

### Minimal scope

Begin with an ordinary checkout, a developer skill, a CLI preview runner, public molecular fixtures, a small test/comparison runner, and preset inspect/validate/save operations. Do not build a web editor, a notebook platform, a node-graph language, or an additional controller first.

A development workspace can contain Python changes in the checkout, draft JSON, fixtures or references to them, comparison outputs, and a manifest. It does not need a new elaborate project configuration format. Keep molecular research inputs and credentials outside committed examples.

The developer skill should map requests to the existing owning modules. Material work should extend the shared material/shader construction; geometry work should extend the shared geometry or representation code. Do not create a new renderer script for each named preset or put a new effect directly in an MCP tool.

### Parameter-only iteration

Inspect/export an existing preset and its supported controls. Change JSON, validate, render a preview, inspect the image and resolved specification, then save a named user/project preset. Restart the client and reuse it.

Use this path for behavior the engine already supports. A preview-quality setting may reduce resolution and samples, but must preserve representation, material semantics, camera, and selection. Reject unsupported parameters rather than silently ignoring them.

### Behavior-extending iteration

When existing controls cannot express the request, use this sequence:

1. **Identify the missing capability.** Locate the relevant Python implementation and state what must change in the scene, shader graph, or geometry. Separate a genuinely missing effect from a discoverability or configuration problem.
2. **Implement it in the shared engine.** Modify an existing function or add a focused module/function. Use a small explicit dispatch map when necessary; do not add a parallel rendering path for the new preset.
3. **Expose its controls in JSON.** Define types, units, bounds, defaults, and valid combinations in the shared schema. Wire each accepted field to its consumer. Update reference/capability checks without forcing all existing presets onto the new behavior.
4. **Exercise the real Blender path.** Render from the selected checkout, inspect the image and constructed scene, and adjust Python and JSON together. An accepted document or a completed process alone is not proof that a parameter affects the output.
5. **Add focused tests.** Verify parameter consumption and scene/shader/geometry behavior, then use a rendered comparison appropriate to the effect. Confirm that unchanged pastel and green-paint specifications still work.
6. **Save and document the design.** Save validated JSON as a reusable preset, identify its required engine capability/version, and describe its editable controls. Keep its implementation in shared Python, not in the preset file.

For example, a proposed ink-style design may need a new cross-hatching shader. Codex should implement that shader in Python and expose spacing, direction, contrast, and selection controls. The preset then chooses values and combines the effect with geometry and lighting. Simply renaming a pastel preset or changing its colors would not demonstrate that capability.

Use ordinary Python code and tests. JSON may name an installed material, representation or effect and configure it; render JSON must not contain Python source, arbitrary import paths, `eval` expressions or downloadable scripts. This restriction applies to submitted render specifications, not to Codex editing and running trusted repository code during development.

Experiments in the Blender UI are allowed in the trusted workspace, and an explicitly requested `.blend` is useful for inspection. A reusable design must eventually be represented by code plus validated JSON rather than undocumented changes existing only in one Blender scene.

### Reference-image-driven development

When a reference PNG is supplied during development:

1. derive and save a concise `VisualBrief` from observable image properties;
2. compare it against the preset catalog and capability registry;
3. decide whether JSON-only tuning is sufficient or Python behavior is missing;
4. render a rough local preview from the edited checkout;
5. compare the intended visual properties, not only aggregate pixel difference;
6. iterate on Python and JSON together where needed;
7. save a new preset when the look is useful, coherent, and too far from the base preset to remain a small override;
8. record the base preset, implemented capabilities, reference provenance, and known approximation limits.

The developer skill should explicitly say “create a new preset” when the requested look would require many coupled overrides, introduces a new material/geometry concept, or is expected to be reused. It should not accumulate a growing collection of one-off override blobs that are difficult to name, test, and reproduce.

Use public or user-authorized reference images in tests. Do not commit private reference images or assume that visual similarity proves scientific correctness.

### Ensure edits actually reach Blender

The preview runner must use the selected checkout, not a stale wheel, a cached installed consumer plugin, an unchanged SIF, or a warm Blender process that imported older code. It should launch the shared rendering transaction directly without requiring MCP, OAuth or a cloud account.

Use a fresh Blender process by default for development previews. A future warm-development option must invalidate on relevant source/runtime changes; do not rely on blanket module hot reload. Production warm workers can remain a separate execution optimization.

Snapshot source/specification per run or otherwise guarantee one coherent code revision. Record renderer version, source-tree digest including relevant uncommitted Python and assets, specification/preset hash, dependency/runtime identity, and actual module source location in local developer diagnostics. Do not copy the entire workspace, credentials or unrelated molecular inputs into a code snapshot.

Code identity must participate in development cache identity. The same JSON after a Python change is not an identical render. Explicitly installing or mounting a chosen development snapshot on private Slurm can be supported, but the worker must report what it actually loaded. Remote execution must not silently substitute a previously deployed image.

Acceptance must include two independent checks:

- **Python-change check:** after establishing a working implementation, keep the input and JSON fixed, deliberately change its Python behavior, and demonstrate the intended scene/output change and updated source digest.
- **JSON-control check:** keep Python fixed, change a newly exposed parameter, and demonstrate the corresponding scene/output change. Unsupported fields or unsupported worker capabilities fail clearly.

Control camera, seed and other settings where applicable. Inspect the intended shader/geometry property and use suitable image comparisons; a nonzero pixel difference caused only by sampling noise does not prove the feature works.

### Promotion to other backends

A parameter-only design can run wherever the installed engine already supports it, subject to deployment policy. A design needing new Python requires the updated engine, schema and any required assets/dependencies on every backend expected to execute it.

Promote deliberately: local experiment -> code/schema/tests and preset -> versioned compatible engine/preset -> explicit installation or deployment. Existing dependencies should remain pinned unless the feature genuinely requires a change; validate any new dependency on the supported runtime targets.

For local execution, select the intended checkout or install the updated package. For Slurm, use an explicitly identified compatible package/source snapshot and runtime image. For Modal, explicitly build/deploy the updated code and compatible runtime. Saving a JSON preset does not install its Python implementation remotely.

Check requested capabilities against the configured deployment before paid GPU dispatch when possible, and verify actual loaded runtime identity at the worker. A stopped backend's advertised capabilities must not be presented as a fresh live probe. On mismatch, report the required update; do not silently substitute a different style or launch a deployment.

Promotion to the official hosted catalog is a separate maintainer action. A user's own Modal deployment and private Slurm installation may have different review policies, but neither needs a separate renderer. Keep the repository and shared deployment private until separately authorized.

## 5. One execution implementation for local, Slurm, and Modal

Retain the existing job service and backend implementations. Repair their boundaries rather than adding a generic orchestration framework.

Every backend receives the same resolved bundle/specification and executes the same rendering transaction. Authentication, process launch, queue/resource policy, storage and delivery may differ. Rendering semantics may not.

For Slurm, provide a standard supported launch path or an explicitly configured launcher. Treat IPD's `qlogin` convention as a profile-specific option rather than a universal Slurm requirement. Separate session attachment from allocation.

Only authorized render/start operations may create a worker or allocation. Status, quota, catalog lookup and diagnostics must not allocate compute. Where stopped remote compute prevents checking state, return that limitation rather than silently replacing it.

Return a durable operation handle before long conversion, cold start or Slurm allocation. Keep the existing main job states where practical and expose a progress stage such as validation, conversion, waiting for compute, scene build or rendering. Do not make a normal MCP call wait indefinitely for `qlogin`.

Use operation identity separately from content reuse. A transport retry resolves to the same operation. An explicit additional render gets its own operation. A lab gateway must map a distinct outer operation to a distinct intended child operation, while reusing that child's ID for retries. Include compatible runtime/specification identity in content-reuse decisions, and never reuse a result whose artifacts are unavailable.

Before broad rollout, fix the reviewed duplicate multi-object fingerprints, nested gateway result reuse, late worker lock release, missing artifacts after controller replacement, and ignored selection on raw-upload profiles. Preserve conservative handling of ambiguous paid execution.

### Fast and smooth execution requirements

Treat responsiveness as part of correctness:

- **One normal submission call.** `render_structure` should receive the molecular input plus the already selected preset and overrides, validate them, commit one logical operation, and return its handle without waiting for the full render.
- **No unconditional preflight chatter.** Do not require separate catalog and quota calls for known presets. Return actionable admission errors from submission and use catalog lookup only when selection needs it.
- **No compute from read-only tools.** Preset inspection, capability lookup, quota, diagnostics, status, and result recovery must not start a local service, Slurm allocation, Modal GPU, conversion, or new render.
- **Resolve once.** Bind the exact preset decision and JSON snapshot to the job. Status/result calls never recalculate the style or choose another preset.
- **Convert once and deduplicate safely.** Reuse a validated conversion and the same logical operation on transport retry, while preserving distinct user-requested renders.
- **Keep deployment images ready.** Build Blender/Molecular Nodes/Python dependencies when installing or deploying, never during an ordinary render. Modal jobs should use a prebuilt compatible image; Slurm should use an identified installed package/container.
- **Use warm workers carefully.** Reuse a warm Blender worker for production throughput only when its loaded engine identity is known. Development previews default to a fresh process so Python edits cannot be stale.
- **Use rough previews deliberately.** A rough preview may lower samples/resolution but must preserve representation, camera, materials, and selection. It is useful for medium-fit references or developer iteration; it must not silently replace the requested final quality.
- **Keep responses compact.** Initial submission returns operation ID, stage, selected preset, fit band, spec digest, backend/profile, and next action. Result delivery prioritizes the PNG and optional requested scene; full manifests remain diagnostic artifacts.
- **Measure stages.** Record sanitized timings for input transfer, conversion, queue/cold start, scene build, render, and result delivery so “slow” can be located without guessing or submitting again.

The normal conversational sequence remains:

```text
render_structure -> render_status (bounded wait, repeat as needed) -> render_result
```

A single user request must cause one logical submission. A hidden preview, interrupted reply, tool retry, or agent handoff is not permission to upload or render again.

## 6. Interfaces and host behavior

### Shared surface matrix

| Surface | Main role | Connection and compute | Style selection |
|---|---|---|---|
| Codex in a trusted checkout | Develop Python/JSON and run tests; also execute renders | Direct Python/CLI or local STDIO MCP; may target local, Slurm, or a configured remote HTTP service | Can inspect the full catalog, reference PNG, source code, and create new presets |
| Codex as a consumer | Execute installed designs | STDIO or remote HTTP MCP, depending on host/configuration | Chooses best installed preset and overrides; reports low-fit requests |
| ChatGPT | Consumer experience for attached structures and reference images | Remote MCP-backed plugin/service; private compute requires a reachable authenticated gateway or supported secure tunnel | Uses natural language/vision to choose the best approved preset and minimal overrides |
| PyMOL bridge | Direct rendering from the current molecular scene | Calls the shared client directly and selects any configured local/Slurm/Modal profile | Exact preset/alias, deterministic local matching, or full JSON where policy permits |
| CLI/Python | Automation and expert execution | Calls the same client/engine directly | Exact preset/alias, overrides, or full JSON where policy permits |

Current OpenAI documentation distinguishes local Codex MCP connections from remote MCP-backed tools used by ChatGPT. Treat these as separately installed and separately tested host paths; do not assume that a local Codex configuration automatically becomes a ChatGPT web connection.

### CLI and Python

Provide one supported client facade used by CLI and PyMOL and by the local MCP adapter. For development, allow direct invocation of the shared local render transaction without requiring an MCP server, OAuth, or a cloud account. This must remain the same renderer, not a second implementation.

Public API boundaries should be small: prepare input, resolve/validate style, submit/render, status/cancel, collect result. Do not make every existing private helper a public plugin API.

CLI/Python should expose:

- exact preset or alias;
- validated override document;
- complete `RenderSpec` JSON on trusted paths;
- execution profile;
- rough/final quality;
- PNG output by default and explicit scene output;
- optional reference-image analysis in the developer workflow, not in the Blender worker.

### PyMOL bridge

The bridge exports the current selected scene, calls the common client with the chosen profile and style, receives the result, and displays the PNG. It does not call MCP and does not contain backend-specific rendering logic.

Preserve enabled objects, selected state, transforms, colors, secondary structure, and camera according to the explicit policy. Honor selections on every backend. Keep PyMOL operations on the appropriate application thread; long remote waiting must not make the UI unusable. Preserve a script-friendly blocking option while supporting nonblocking interactive use.

Support preset execution and custom JSON through the same bridge. Legacy mode names can translate to profiles with documented migration warnings; do not retain parallel behavior behind them.

The intended direct use is conceptually:

```python
blender_render preset="dried_paint_green", profile="local"
blender_render preset="dried_paint_green", profile="ipd"
blender_render preset="dried_paint_green", profile="modal"
blender_render style_json="/path/to/render.json", profile="ipd"
```

These are target semantics, not proof that the current commands already satisfy them. Changing `profile` must change compute only—not representation, selection, camera, or result semantics.

When no preset is specified in direct PyMOL/CLI use, choose the documented default. A short style description may resolve through local aliases/tags, but reference-image semantic matching requires a vision-capable interface such as ChatGPT/Codex or the developer workbench.

### MCP and consumer plugins

The common workflow remains `render_structure -> render_status -> render_result`, with cancellation, catalog/capability inspection, and owner-scoped job recovery available when needed.

The ChatGPT/Codex skill should perform best-preset selection before calling `render_structure`:

1. honor an explicit preset;
2. inspect the molecular input summary and user request;
3. inspect a supplied reference PNG when present;
4. choose the best compatible approved preset;
5. produce minimal validated overrides;
6. mark low-fit requests as candidates for a new preset;
7. pass an explicit preset ID/revision and overrides to the tool.

This keeps execution deterministic. The MCP server validates the decision and records it, but it does not run another LLM or reinterpret the image.

Keep genuine input differences explicit: local STDIO accepts a user-selected local file; hosted MCP accepts the host’s declared attachment object. Share option resolution, job semantics, structured errors, and compact result summaries.

A self-run server can enable complete validated JSON and user presets. Public hosted execution exposes approved presets and allowed overrides. Neither accepts uploaded renderer Python or untrusted Blender scenes. Untrusted PSE conversion remains isolated from credentials and network.

Provide explicit hosted and local/private installation configurations from shared assets and skills. A local-only configuration must not require the owner’s registered hosted connection. A self-hosted HTTP server can back local or Slurm compute; Modal hosting is an execution/deployment choice, not a different MCP product.

### Suggested MCP request/result shape

Keep the normal tool surface small. The exact schema can evolve, but it should represent these semantics:

```json
{
  "molecular_file": "<host-declared attachment or local path>",
  "preset": {
    "id": "builtin/dried_paint_green",
    "revision": "1"
  },
  "overrides": {
    "renderer.transparent_background": true
  },
  "quality": "final",
  "save_blend": false,
  "request_key": "<stable logical operation key>"
}
```

The result summary should include:

```json
{
  "job_id": "...",
  "status": "queued",
  "stage": "waiting_for_compute",
  "profile": "modal",
  "preset_decision": {
    "preset_id": "builtin/dried_paint_green",
    "fit": "high",
    "new_preset_recommended": false
  },
  "render_spec_sha256": "...",
  "next_action": "render_status"
}
```

Do not send the full style manifest or raw reference image back and forth on every status call.

### Smooth ChatGPT/Codex behavior

- Put concise built-in preset descriptions and aliases in the consumer skill so common requests need no catalog call.
- Cache catalog/schema metadata by version, but refresh after plugin/server upgrades.
- Keep tool descriptions action-oriented and non-overlapping so the model prefers one submission path.
- Return stable stage-specific errors: attachment unavailable, format unknown, specification invalid, capability unavailable, admission refused, compute unavailable, render failed, artifact expired, and presentation failed.
- A connector or presentation failure must not be mislabeled as a malformed molecular structure.
- Test exact installed plugin versions in fresh conversations after tool metadata changes.
- Preserve ordinary user approval for sending files and starting paid/remote work; do not hide repeated calls behind broader permissions.
- When fit is low, the conversational response should say that a new preset is recommended, identify the closest current preset, and distinguish an approximation from a faithful implementation.

Keep the developer skill separate from the consumer rendering skill. The consumer skill executes and selects installed designs; the developer skill may edit a trusted repository and add Python/schema/preset behavior. Shipping an ordinary consumer plugin must not silently enable development-time code execution.

## 7. Delivery and privacy decisions

Separate render completion from artifact availability and presentation. A successful render with lost files is not a reusable completed result. Reconcile missing files after restart and protect unexpired results during planned replacement.

Implement result-only recovery without re-uploading the molecular input. A durable request key and minimal owner-scoped recent-operation records are sufficient; retaining coordinates solely for recovery is unnecessary.

For CLI/stdio, prepare writable destinations before download, save atomically, verify checksums, and retain a small service/owner/job-scoped local receipt. Repeated result calls must reconstruct already verified outputs and resume missing ones. A failed second artifact must not make the first artifact disappear from the client result.

Do not quietly change the existing short hosted retention policy. Deletion on GET versus explicit delivery acknowledgement is a separately documented policy/API decision. A server cannot infer that a browser successfully saved a file just because it sent an HTTP response. Preview retrieval must not consume the downloadable original.

The recommended default is PNG-only generation and delivery. If a scene is requested before submission, save and return it. If a user asks afterward and none was saved, explain that it is not available; any regeneration is a newly authorized operation. Seamless later scene retrieval would require temporarily retaining a scene, which must be an explicit privacy/storage decision rather than a hidden default.

Local developer preview outputs remain under the developer's selected workspace and retention choices, not the shared service's automatic cleanup policy.

## 8. Incremental implementation sequence

### Milestone A: baseline, JSON bridge, and shared contracts

Pin the baseline; reproduce existing relevant tests; preserve representative pastel/green-paint images and source/runtime manifests. Keep current public fixture coverage and both default PNG-only and explicit scene cases.

Make `StructureBundle + resolved RenderSpec JSON` the explicit renderer contract. Extract one shared style resolver and a small metadata-rich preset catalog. Consolidate schema validation. Repair custom-style identity, camera precedence, and override behavior. Snapshot/hash the resolved JSON and capability requirements. Preserve existing style JSON compatibility. Keep behavior changes distinct from mechanical moves.

Acceptance: every adapter reaches the same renderer contract; a full JSON and its equivalent preset-plus-overrides resolve identically; a custom surface-only style is not subject to pastel-only cartoon gates; explicit camera changes survive resolution; unsupported fields fail before dispatch; both baseline presets remain valid.

### Milestone B: a trustworthy developer and reference-image loop

Add the developer CLI/skill with explicit permission to modify trusted repository Python as well as JSON, fresh source-aware preview execution, schema/preset inspection, named user-preset persistence, and `VisualBrief` extraction from an authorized reference PNG. Separate scene-building and rendering entry points only as far as needed for focused testing and scene export. Implement one genuinely missing behavior in shared Python and expose its controls through the schema.

Acceptance: one JSON-only style variation and one genuinely Python-extending variation; a fixed-input/fixed-JSON Python-change test; a fixed-Python/new-parameter test; inspected scene/output effects; restart/reuse of the saved preset; verified source identity with no stale cached output; one reference image correctly classified as an existing-preset override and one low-fit reference explicitly routed to creation of a new preset. Existing styles still pass. A JSON editor, schema-only change, recolored preset, or unsupported claim of exact visual matching is insufficient. No hosted deployment is required to complete this milestone.

### Milestone C: execution, latency, and result reliability

Fix the reviewed lifecycle, identity, selection, cancellation and artifact-recovery defects. Generalize Slurm launch configuration. Add side-effect-free profile diagnostics and runtime capability checks. Establish one logical-operation-to-worker mapping at nested gateways. Remove unconditional catalog/quota preflights, keep runtime images prebuilt, and instrument stage timings.

Acceptance: same input/resolved-JSON snapshot across local/Slurm/Modal where runtime-compatible; one normal submission returns a durable operation without waiting for the complete render; retries do not duplicate compute; explicit repeats work; status/catalog/quota never allocate; partial delivery resumes; controller replacement does not advertise nonexistent results; stage timings identify conversion, queue/cold-start, scene-build, render, and delivery latency.

B and C may proceed in parallel after A, provided they consume the same contracts and do not independently invent new job or preset formats.

### Milestone D: thin PyMOL and MCP adapters with best-fit selection

Wire the shared client/specification into the bridge and MCP. Provide clear hosted/local installation configurations, compact catalog metadata, model-assisted best-preset selection, structured stage errors, bounded status waiting, and job recovery. Preserve explicit authorization and private-data routing. Keep reference PNG analysis in the vision-capable interface unless an engine capability explicitly requires the file.

Acceptance: PyMOL preset and full-JSON execution on each configured backend; ordinary MCP rendering follows one submission/status/result path; common natural-language requests choose the expected preset without a catalog preflight; reference PNGs produce an inspectable preset decision; low-fit images trigger a new-preset recommendation; ordinary rendering requires no knowledge of upload IDs, SQL, cluster launchers, or renderer internals; local authoring remains optional.

### Milestone E: promotion, packaging and installed-product validation

Build/install the actual wheel and required runtime artifacts. Verify the preset catalog and engine capabilities reported by each backend. Refresh/reinstall the actual plugin version under test and use a fresh conversation.

Acceptance: a developer-authored preset using the new Python capability is reused from CLI, PyMOL and self-run MCP, then explicitly installed on compatible Slurm compute and deployed to a personal/approved Modal environment. Verify the loaded engine identity and identical resolved-specification hash, not just matching preset names. An older worker rejects the unsupported capability without dispatching a substitute render. Real attachment-to-final-PNG tests must pass on each claimed ChatGPT/Codex host, including a natural-language preset choice, a reference-PNG choice, PNG-only default delivery, and explicit scene delivery. Record call count and backend dispatch count so a smooth UI does not conceal duplicate work. No claim of official directory approval until the external publication process is complete.

Implement these as small coherent commits or dependent PRs. Do not combine a mass file reorganization, new visual features, auth migration and a public deployment in one change.

## 9. Acceptance matrix and diagnostics

Exercise both shipped presets against representative CIF/PDB/PSE fixtures on each backend. Include protein-only, multichain, nucleic acid, ligand/PTM, transformed multi-object sessions, and a non-default selection. Test saved-pose preservation separately from explicit pose overrides.

Add preset-selection and reference-image cases:

- explicit pastel and explicit green-paint requests;
- an unnamed request whose observable properties clearly match each preset;
- supported overrides that should remain attached to the selected preset;
- a medium-fit reference labeled as an approximation;
- a low-fit reference that sets `new_preset_recommended=true`;
- a developer task that converts the low-fit reference into a tested reusable preset;
- proof that the raw reference PNG is not sent to the render worker unless explicitly required by an installed capability.

Add workflow/call-count cases:

- a common default render uses one submission call without mandatory quota/catalog preflight;
- an interrupted response resumes the same job and resolved-specification hash;
- status, catalog, quota, and diagnostics start no compute;
- preset choice remains stable across retries and agent handoff;
- ChatGPT/Codex installed-host tests end with a visible PNG or explicitly documented preview/download fallback, without automatic rerender.

Separate four types of evidence:

1. Unit/contract tests prove validation, normalization, lifecycle and adapter semantics.
2. Renderer tests prove both that Python implementation changes reach Blender and that exposed schema values are consumed to generate the intended scene, shader graph or geometry. Preserve regression coverage for established capabilities.
3. Real backend tests prove the supported runtime and transport execute the shared renderer; visual comparisons allow documented cross-device tolerances rather than requiring bit-identical pixels.
4. Installed-host tests prove attachments, permissions, job recovery and final visible delivery.

A direct API response with an image field is not an in-chat display test. Report visible inline PNG and usable preview/download fallback separately. An empty/stuck card is a failed presentation check even when Blender completed successfully.

Include optional attachment metadata omission, expired attachment URLs, a lost submission response, a lost result response, interrupted download, cancellation, a stopped allocation, runtime mismatch and unavailable tools. Distinguish these stages in compact error codes; never tell the user their structure is malformed merely because the connector or image display failed.

Record code/runtime/preset versions, sanitized stage transitions, operation and worker IDs, dispatch counts, and final deliverable availability. Do not log molecular payloads, private filenames, authentication tokens or signed download URLs in hosted logs.

## 10. Refactoring map and non-goals

Preserve `export.py`, the existing geometry/material implementations, worker manager, queue, and backend transport code as starting points. Introduce a focused specification resolver and preset catalog; make `core.py` and compatibility entry points delegate as responsibilities move. Keep MCP and PyMOL as adapters, not owners of preset logic. Remove duplicated validators and unused legacy widget code only with explicit compatibility coverage.

Keep new visual implementations with the existing engine responsibilities: material/shader construction alongside the material/paint code, geometry effects alongside the geometry/representation code, and general scene orchestration in the shared renderer. Do not require a file tree migration before the first developer loop works. A preset selects capabilities through data; it must not trigger hard-coded special cases in CLI, MCP, PyMOL or a backend based on its marketing name.

Do not create a second renderer, another queue/controller, a general-purpose orchestration framework, a Python-in-JSON interpreter, an LLM inside the renderer, or a mandatory remote service for local development. Do not promise support for arbitrary new local code on a remote worker that has not installed it. Do not treat more presets or longer skill warnings as substitutes for reliable lifecycle and delivery.

## 11. Codex implementation brief

Refactor the existing Blender Render implementation incrementally around one Python rendering engine and one validated JSON execution bridge. The engine input is a normalized `StructureBundle` plus one fully resolved `RenderSpec` JSON snapshot. Local, Slurm, and Modal must execute the same snapshot through the same Python scene builder. Preserve pastel and dried-paint green as regression baselines. Do not implement another renderer or orchestration framework.

Deliver a developer workflow where Codex can edit trusted Python and JSON, render from the edited checkout, inspect the result, test actual behavior, and save a reusable preset. Prove it with a new Python-level capability and two tests: code changes affect output with fixed JSON, and new JSON controls affect output with fixed code. Record the actual loaded source/runtime identity.

Keep consumer execution preset-anchored. ChatGPT/Codex should choose the best compatible installed preset from natural language and optional reference PNG, apply the smallest validated override set, and pass an explicit preset revision plus resolved JSON into the render operation. Hosted Modal execution may restrict users to approved presets and override paths. Trusted local/private execution may accept complete validated JSON. No consumer service accepts arbitrary Python code.

Implement an inspectable preset decision with high/medium/low fit. Medium-fit results are labeled approximations. Low-fit requests must state that a new preset is recommended, name the closest base preset, identify missing capabilities, and route faithful reusable work to the developer workflow. A reference PNG informs selection and development; it is not executable configuration and should not be uploaded to the render worker by default.

Keep consumer interfaces thin: CLI/Python, the live PyMOL bridge, and MCP all delegate to the shared resolver/client. PyMOL renders directly through any configured local, Slurm, or Modal profile without requiring MCP. ChatGPT uses a remote MCP-backed service; Codex may use local STDIO or remote HTTP where supported. Test these host paths independently.

Optimize the common path for one submission, bounded status waits, and one result retrieval. Do not require catalog/quota preflights for known presets. Read-only operations must not allocate compute. Resolve and hash the style once, use prebuilt runtimes, return a durable job handle before long work, record stage timings, and keep result summaries compact. PNG is the default; generate/return a scene only when explicitly requested.

Repair operation identity, read-only allocation side effects, selection parity, controller replacement, worker cleanup, and result recovery before claiming reliable plugin execution. New Python must be explicitly installed or deployed before a remote backend can use it. Never treat an unavailable result, low preset fit, hidden preview, or interrupted reply as permission to submit a replacement render.

Work in the milestone order above. For each milestone, report changed code, tests actually executed, manifests/images inspected, preset-selection decisions, call/dispatch counts, and remaining unverified paths. Do not claim live backend or installed-host acceptance from mocks alone. Do not publish, deploy to the shared service, change privacy policy, or consume paid compute without appropriate authorization.

## Reference points

Retained from the earlier plan as baseline references and host-contract pointers. OpenAI MCP/plugin behavior was rechecked against the official pages on September 7, 2026, but exact availability and host behavior remain version- and workspace-dependent. Confirm platform-dependent behavior against the installed supported host during implementation; the architecture above is a proposal.

- PR baseline: https://github.com/magnusbauer/blender_render/pull/1
- Current architecture: https://github.com/magnusbauer/blender_render/blob/b1bb7d1c5d0d832b75c096eeea855b306815d79f/docs/architecture.md
- First review: https://github.com/magnusbauer/blender_render/pull/1#pullrequestreview-5128035604
- Supplemental review: https://github.com/magnusbauer/blender_render/pull/1#pullrequestreview-5128140309
- Codex MCP and local/web distinctions: https://developers.openai.com/codex/mcp
- Plugin packaging and installed-copy behavior: https://developers.openai.com/plugins/build/plugins
- File inputs and tool/result schema contract: https://developers.openai.com/plugins/reference
- ChatGPT developer mode and remote/private MCP guidance: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt
- OpenAI MCP host distinctions: https://developers.openai.com/codex/mcp
