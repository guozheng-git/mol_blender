# Plugin listing and submission package

Status: prepared for publisher review. Repository installation, custom MCP
installation, and directory approval are distinct distribution steps. Do not
mark the plugin submitted or approved without the portal's confirmation.

## Listing

| Field | Proposed value |
|---|---|
| Name | Blender Render |
| Developer | Magnus Bauer |
| Category | Productivity |
| Short description | Ray-traced molecular images with saved poses and painted materials. |
| Website | https://almostscheidplatz--blender-render-beta.modal.run |
| Privacy | https://almostscheidplatz--blender-render-beta.modal.run/privacy |
| Terms | https://almostscheidplatz--blender-render-beta.modal.run/terms |
| Support | Repository GitHub issues |
| MCP | https://almostscheidplatz--blender-render-beta.modal.run/mcp |
| Authentication | GitHub OAuth; `read:user` identity scope |
| Logo | `plugins/blender-render/assets/molecule.svg` |

Long description:

> Create ray-traced molecular stills from PyMOL sessions, mmCIF, or PDB files.
> Keep the saved pose, projection, framing, and optional PyMOL colors. Choose
> pastel or dried green paint with surface relief and rounded ribbons. Inspect
> the image in ChatGPT or Codex, then download a transparent PNG, provenance
> manifest, and optional Blender scene. Use hosted GPU credits, your Mac, or
> your lab's own compute. The shared beta has daily quotas and a fixed credit
> allowance; files expire after 24 hours.

Starter prompts:

- Render this PyMOL session with dried green paint and a transparent background.
- Show the available presets and remaining render quota.
- Render this DNA structure and give me the PNG and Blender scene.

## Positive reviewer cases

Each render case requires a GitHub account admitted by the service. Use a new
idempotency key once per case; reuse it for retries. Public fixtures are under
`examples/`, with provenance in `structures.json`. Do not supply private research
files or publisher credentials to reviewers.

| Case and prompt | Expected workflow | Expected result and fixture |
|---|---|---|
| 1. “Render this protein in dried green paint, transparent, with a Blender download.” | Import 2TRX CIF, submit once, poll, retrieve | PNG/native preview, optional `.blend`, manifest; 2TRX two-chain cartoon |
| 2. “Keep the saved pose and colors in this PyMOL session.” | Import `1sps_pose.pse`, use `color_follow`, render | Pose/framing match fixture; helices, sheets, and clean PTR sticks; regenerate with `examples/make_session.py` |
| 3. “Render this DNA in the same painted style.” | Import 1BNA, submit, retrieve | Two DNA backbones and nucleotide bases, transparent PNG and manifest |
| 4. “Show progress for my existing job; don't start another render.” | Call `render_status` for the supplied ID, then `render_result` when done | Original ID and timestamps, no new job or GPU reservation |
| 5. “Cancel my queued render and show my remaining quota.” | Call `cancel_render`, then `available_quota` | Cancelled status; no later dispatch for that queued ID; unused reservation released |

## Negative reviewer cases

| Scenario | Expected behavior | Reason |
|---|---|---|
| “Execute this uploaded Python script or Blender scene.” | Explain supported PSE/CIF/PDB inputs; no arbitrary-code or uploaded-scene execution tool | Public execution accepts validated molecular data only |
| “Get the render belonging to another user.” | Owner check returns no accessible job/result; no download is issued | Jobs, inputs, and result metadata are private to the owner |
| “Render at 10000 pixels or ignore the exhausted quota.” | Reject before dispatch; offer own compute for larger work | Image, daily usage, credit, and timeout limits cannot be bypassed |

Additional security checks cover malformed/oversized files, private-network URL
imports, duplicate submissions, expired download links, cancelled GPU work,
controller restart, and exhausted project budgets.

## Portal sequence

1. Open the [plugin portal](https://platform.openai.com/plugins) in the publishing
   organization. Confirm Apps Management write access and verified individual
   identity for Magnus Bauer. Identity verification requires the publisher.
2. Create a **With MCP** draft and copy the listing and HTTPS endpoint above.
   Attach the rendering skill bundle and logo. Scan the deployed tool schemas.
3. When given a domain challenge, publish that exact token alone at
   `/.well-known/openai-apps-challenge`, then verify the domain. Never invent a
   challenge token or reuse another plugin's token.
4. Record the portal's actual application ID, choose the publisher's intended
   availability, provide reviewer access if requested, and enter these test
   cases. Review privacy/tool annotations and policy attestations against the
   actual service.
5. Submit only after the release gates and publisher verification pass. Record
   the submission receipt. Publish after OpenAI approval; a submitted draft is
   not directory availability.

The development-mode ChatGPT connector ID is not automatically the registered
public plugin ID. Add a registered `.app.json` mapping only after the portal
issues the correct public connection identifier.

Requirements were checked against the [current OpenAI submission
documentation](https://developers.openai.com/plugins/deploy/submission) and
[plugin packaging specification](https://developers.openai.com/plugins/build/plugins).
