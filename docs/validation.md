# Platform coverage and validation

This summary covers the **0.4.0rc4** code line and installation documentation as
of **2026-11-18**. Results below distinguish earlier runtime checks from later
regression and packaging checks. They do not imply every workflow was rerun on
the latest documentation commit.

## Platform coverage

- **Apple Silicon macOS:** recorded checks include fresh native runtime setup,
  local PNG rendering, MP4/GIF encoding and frame export, saved-scene reopening,
  warm-worker reuse, and desktop editing. These checks were recorded between
  September 28 and November 15; later automated suites do not replace visual
  checks of changed rendering behavior.
- **Linux x86_64 with Slurm and Apptainer:** earlier 0.4.0 release-candidate
  acceptance ran real CPU and NVIDIA CUDA jobs, including stills, animations,
  cancellation and verified downloads. GPU manifests recorded L40S and A100
  devices. Each deployment still needs acceptance on its own scheduler, image
  and driver combination; gateway connectivity or `cluster doctor` alone does
  not establish GPU execution.
- **Desktop co-PyMOL:** macOS is the documented desktop installation path. Setup
  regression tests exercise client registration in isolation. A live connection
  is verified only by calling a PyMOL tool from the selected client. Linux,
  conda and other desktop layouts need their own installation checks.
- **Structure Studio in Claude Code:** the November 18 installation check built
  a complete plugin, installed it with temporary client configuration, verified
  its bundled wheel and generated a workspace from public 1STP outside the
  checkout. This did not install a full rendering runtime or display the
  workspace in a browser.

The pinned render stack uses Blender **5.0.1**, Molecular Nodes **4.5.10**,
Python **3.11.15**, NumPy **1.26.4** and Biotite **1.6.0**. Desktop co-PyMOL can
run in PyMOL's Python **3.10+**; renderer execution uses its separate Python 3.11
environment. See [runtime environments](runtime-environments.md) and the
[installation guides](README.md#get-started).

## Recorded regression checks

- **November 17, renderer/Studio integration:** 695 tests passed and one
  wheel-parity test was initially skipped. That test passed after a fresh build;
  85 selected tests also passed against the installed wheel outside the checkout.
  Native Blender, headless PyMOL and browser checks were enabled. These were
  regression checks, not a new live Slurm or desktop-client acceptance run.
- **November 18, installation guidance at `48490c0`:** 68 targeted tests passed:
  12 co-PyMOL setup tests, 53 connection/job tests and three Studio packaging
  checks. The connection suite uses fake SSH and mocked job behavior, so this
  result does not establish a live cluster connection. Both plugin ZIPs passed
  integrity checks, and the documented Claude Code install was exercised with
  temporary client configuration. No Claude web/Desktop upload or render ran.

## Documentation and packaging checks

The November 18 launch-doc cleanup passed five focused source/package tests
(engine inventory, built-wheel parity, preset catalog and plugin manifests) and
two engine tests against the installed wheel outside the checkout. The wheel was
built from the source distribution. All 14 intended public docs were present;
three temporary private/draft files placed in the build copy were excluded.
License and dependency-notice files retained their original bytes.

Checked 139 relative links and heading anchors, 67 shell blocks, skill metadata
and Git whitespace. This pass changed documentation, example labels and package
selection; it did not run a new render, live cluster connection or host upload.

## Remaining acceptance limits

- A local CLI render, MCP image response or simulated browser host does not prove
  image visibility or durable Library/Space storage in ChatGPT or Claude. The
  [hosted adapter](hosted-adapter.md) requires acceptance in the actual host.
- Installing a plugin in Claude Code does not install it in Claude web/Desktop.
  Validate upload, runtime readiness and the requested workflow in that target
  environment separately.
- The native runtime installer targets Apple Silicon macOS and Linux x86_64.
  Windows, Intel macOS and Linux ARM are not covered by this validation record.
- CPU fallback is possible unless the request requires a GPU. Read each render's
  device manifest; a selected preset or allocated GPU alone is insufficient.
- Presets with chain, ligand or pocket selections need adaptation to new inputs.
  Small acceptance renders establish execution, not publication-quality visual
  matching. Saved PSE and Blender scenes are trusted executable inputs.

## Recording future checks

Keep this page a concise summary for the current release: scope, package/source
revision, platform, actual result and any limits. Update the relevant summary
instead of appending development sessions, merge history or deployment diaries.
Keep detailed commands, logs, manifests, screenshots and site-specific evidence
in ignored `.validation/` or private storage outside the public repository.
Never include credentials, hostnames, account names, private paths, job IDs or
unpublished research details in public summaries.

For changed rendering behavior, record actual runtime and visual checks; mocks
cannot establish Blender or host integration. For packaging changes, build the
source distribution and wheel, inspect the packaged files and run relevant
checks against the installed wheel outside the checkout. Public documentation
must be listed in `MANIFEST.in` as well as the [documentation index](README.md).
