# Private beta validation — 0.2.0b1

The repository, release artifacts, and test deployment remain private. The hosted
MCP endpoint permits only the owner's GitHub account. Public publication,
publisher identity verification, and portal submission are deferred by the owner.
This report records executed evidence; it is not approval for unrestricted admission.

## Executed checks

| Area | Result |
|---|---|
| Automated suite | 90 tests passed, including ownership, idempotency, FIFO, cancellation, restart recovery, malformed/oversized input, download expiry, quota and budget refusal, encrypted credentials, and preset contracts. Five upstream deprecation warnings remain. |
| Installed wheel | Installed with its declared extras in a Python 3.11 environment outside the checkout. Fifteen copied contract tests passed there. Six presets, schema, runtime locks, preview HTML, and license assets resolve from the installed package. |
| Clean Mac runtime | Managed Blender 5.0.1, Molecular Nodes 4.5.10, Python 3.11, and CCD installation verified. Blender uses the Apple M4 Metal device. |
| Real PyMOL command | `run .../pymol_bridge_plugin.py; blender_render` rendered public 1SPS from the installed wheel, with no render arguments. The result is 1536 × 1152 and took 11.021 seconds. PyMOL startup plugins were disabled for the isolated test. Script-path and injected-command-context regressions found by this check are fixed and covered by tests. |
| Export fidelity | Actual PyMOL export covered 3,772 atoms, two transformed objects, exclusion of a disabled third object, insertion codes, atom colors, and saved camera state. Maximum coordinate discrepancy was 0.00000853 Å. |
| Mac / HPC / Modal figures | Executed renders of 2TRX protein/multichain, 1BNA DNA, 1EHZ RNA, and a reproducible 1SPS PyMOL session containing PTR. Reviewed pose, framing, alpha, secondary structure, ligand sticks, painted relief, and rounded edges. |
| IPD worker | A4000 CUDA renders through SLURM and Apptainer from the Mac. Two clients attached to one warm FIFO service. Running cancellation, forced allocation loss, automatic replacement, and explicit stop passed. Test allocations were stopped. |
| Linux container | The final versioned SIF built successfully on IPD and passed its Blender/Molecular Nodes/CCD test. The Debian package snapshot and Python runtime dependencies are pinned. |
| Isolated conversion | Actual Modal sandbox: network blocked, controller credentials and state volume absent, malformed session refused, sandbox terminated. Actual Apptainer converter imported public DNA with network/home isolation. Tests reject symlink, FIFO, and invalid JSON converter outputs. |
| Hosted file formats | Raw PSE, CIF, and PDB imports passed. The saved 1SPS PSE matched its local render. |
| GitHub OAuth | Actual login succeeded with public identity permission only. Encrypted credentials and the same completed job survived deployment restarts. |
| Codex plugin | Installed and reinstalled through the personal marketplace. Seven tools discovered with OAuth. Imported a public structure, rendered on L4, retrieved a native image and downloads. Repeated submission with the same key returned the same job without extra GPU work. The local-file adapter also uploaded a PSE from disk and downloaded an existing result. Its OAuth event-loop regression is fixed and covered by a test. |
| ChatGPT development connection | GitHub login and tool discovery passed. A declared file object using a public structure URL produced an L4 render. The native preview card displayed transparency and working download buttons. The downloaded `.blend` opened in Blender 5.0.1. |
| Blender downloads | Codex and ChatGPT downloads opened with automatic scripts disabled. Latest scenes also contain dependency license text. |
| Quota and budget | The live service refused a sixth daily render with HTTP 429 `daily_quota`. Automated tests prove insufficient budget prevents backend dispatch and persistent reservations cannot refill on restart. A separate Modal validation app successfully stopped itself using its container identity. |
| Policy endpoints | Root, privacy, and terms pages return HTTP 200. Public health exposes only readiness/version/counts; private-worker health and every job API require authentication. |

## Measured image agreement

All figures below are 1024 × 768 transparent PNGs. Values compare opaque pixels
on a 0–255 RGB scale; silhouette overlap uses alpha greater than 127.

| Public structure | Minimum silhouette overlap | Maximum mean RGB difference |
|---|---:|---:|
| 2TRX | 100% | 0.0799 |
| 1BNA | 99.999211% | 0.0494 |
| 1EHZ | 100% | 0.0835 |
| 1SPS saved pose / PTR | 99.998832% | 0.0787 |

Camera matrices differ by at most 0.0000001193, consistent with float32 rounding;
projection, scale, and output dimensions agree. Matching pixels do not establish
scientific correctness of the underlying structures. Full sanitized measurements
and checksums are in [visual-validation.json](visual-validation.json).

Examples: [protein](../examples/rendered/2trx_dried_paint.png),
[DNA](../examples/rendered/1bna_dried_paint.png),
[RNA](../examples/rendered/1ehz_dried_paint.png),
[saved protein/ligand pose](../examples/rendered/1sps_dried_paint.png).

## Remaining public-release gates

- **History:** deleted molecular sessions and research artifacts still exist in
  the private Git history. Do not make this repository public. A clean-history
  publication needs a separate owner decision.
- **ChatGPT attachment picker:** actual browser attachment upload remains
  unverified because the browser extension refused local file upload. The
  declared file-object contract and public URL import passed, as did the Codex
  local-file adapter and direct raw PSE upload. Do not represent those as an
  attachment-picker test.
- **Publisher portal:** individual identity verification and submission were
  skipped at the owner's request. No public plugin draft or directory approval
  is claimed. Listing text, policies, and positive/negative cases are prepared.
- **Additional hosting:** a second personal Modal account and a lab-owned TLS
  domain were not provisioned. Their configurable implementations are included;
  the executed remote paths are the owner's Modal workspace and IPD account.
- **OCI distribution:** the Apptainer SIF is the validated Linux artifact.
  The Dockerfile is supplied, but an OCI image was not pushed to a registry.

The release gate remains closed while these public checks are incomplete.
Local, private HPC, and the restricted hosted beta can be reviewed independently.

## Budget evidence

Modal's billing page reported $0.07 used and $529.93 remaining when checked on
September 6, 2026; billing may lag subsequent validation. The application's later
compute ledger conservatively reserved/charged $0.476294 for five completed
hosted renders. These are different measures, not interchangeable balances.
The project ceiling remains $530, the existing workspace cap remains enabled,
and no payment method or automatic refill was added. The grant expires March 31,
2027. Development, operation, and public-rendering allowances are documented in
[Modal operation](modal.md).
