# Local integration proposal

This branch is a local review candidate, not a deployment or release approval.
No GitHub branches, pull requests, services or installed production plugins are
modified by this integration. All source refs below were fetched on 2026-11-01.

## Integration order

| Step | Exact input | Decision |
| --- | --- | --- |
| Base | `main` / `1690762888aff97cb3020348ce7befc9a6363da9` | Keep the current local renderer architecture. |
| PR #4 | `8d6123b0d13ea1a84515c86d61c2939fdeae4ed2` | Merge Structure Studio, preserving its history. |
| PR #5 | `2e7c45043ba88416d12c89835fe76a621f10fccf` | Merge animation recipes after #4. |
| PR #2 | `d428d5d7c6fec4d96ecf68cb55a2cfd657944184` | Audit only; local functionality was ported in `1f345bf`. |
| PR #3 | `0b090b307a7b2008cf987cf6c634d99ecf3c5017` | Select image delivery/reuse into an optional adapter; do not merge the old service stack. |

PR #4 and #5 merged without conflicts. Tests run after each merge with real
PyMOL checks enabled: **239 passed** after #4; **304 passed** after #5.
These are integration checkpoints; subsequent changes require fresh validation.

## PR #2 remaining differences

Compared the four PR commits against their actual base `2a252c4`, then compared
the shared animation modules with current `main` and its explicit port `1f345bf`.

| Area | Audit result and action |
| --- | --- |
| `animation.py`, `animation_blender.py`, `pymol_movie.py` | The local port includes normalization, bounded frames, FFmpeg encoding, checked frame archives and PyMOL caching/export. Before #5, the last two files are byte-identical to #2. Local `animation.py` additionally handles `animation=none` and refuses output overwrite. Keep the local implementations and #5 extensions. |
| `blender_custom_render.py`, `core.py`, `engine.py`, `presets.py`, `rendering.py`, `spec.py`, schema | Shared rendering and hashed animation controls are already integrated. #5 adds camera paths, molecular states and cutaway lids. Retain these newer capabilities. |
| CLI, MCP and PyMOL adapters | Local CLI/MCP/bridge already forward animation options, verify requested artifacts and retain archives for later export. Do not replace them with remote profiles. |
| `compatibility.py` | The local port omitted animation capability names from diagnostic sanitization. Align its allowlist with the shared engine, including #5 capabilities. |
| `rocking.py`, `rocking_blender.py`, `docs/rocking-animation.md`, `tests/test_rocking.py` | Obsolete preset-specific compatibility entry point. Equivalent rock/360 behavior is in the normal CLI and shared engine. Do not restore a second command/render path. |
| `api.py`, `artifacts.py`, `delivery.py`, `execution.py`, `gateway.py`, `jobs.py`, `modal_app.py`, `remote_client.py`, `service.py`, `worker_http.py` | Queue, ownership, quotas, remote transport and hosted lifecycle code belong to the removed service architecture. No local functionality is gained by restoring these. Deferred to a separate service project if needed. |
| `containers/runtime-install.sh` | Replaced by the pinned local runtime. Linux must rebuild its environment from pins inside Apptainer. Never copy a Mac venv. |
| `scripts/validate_animation.py`, recovery/interactive tests | Old worker and remote-consumer fixtures are incompatible with local main. Current local tests cover normalization, output validation, extraction and encoding. Preserve the reusable real GUI acceptance script `scripts/validate_pymol_movie.py` for a remaining release gate. |
| Notices, dependencies, animation docs, progress record | Current notices already credit FFmpeg and the local docs describe actual commands. Keep historical hosted evidence out of current acceptance claims. |

## PR #3 boundary

The PR is stacked on `codex/chatgpt-inline-results`, not current main. Its full
ancestry includes #2, remote queues, Modal/Slurm workers, authentication, consumer
records and old interactive installation code. Its seven direct commits are
primarily image handoff, viewer reuse and diagnostics. A wholesale merge would
reintroduce a second execution architecture.

The optional adapter should accept only already-authorized completed artifacts
from its embedding service. It must preserve checksums and original bytes, keep
rendering separate from delivery, and report host import and durable saving as
unconfirmed. Pixel availability in the next model step, Library/Space persistence,
inline placement and real host reuse are acceptance gates, not consequences of
passing simulated-host tests. Do not launch paid hosted renders for this audit.

Implemented under `blender_render.hosted`: original native handoff and viewer
code with a new authenticated, read-only `CompletedPNG` provider boundary.
The source adapter and imported Chromium simulations pass **55 tests**, including
original-URL revocation, saved-byte reuse and rejection of invalid artifacts.
See [hosted-adapter.md](hosted-adapter.md) for the embedding contract and limits.

## Release gates

Bundle hardening is implemented in Python and the viewer: 128 MiB decoded per
file, 256 MiB aggregate decoded/encoded input, 20 MiB per document and at most
64 files/parts. Inflation stops at the declared size before checksum validation.
Malformed, truncated, concatenated, duplicate and incomplete inputs are rejected
before restoration writes. Legacy unpack also refuses overwrites. The focused
suite passes **51 tests**, including browser JavaScript decoding via Node.

Installer audit: removed automatic apt/sudo and user/global-pip fallbacks. Both
Studio environments now sync full hash-pinned requirements with uv. The proposed
3.2.0.2 PyMOL wheel failed real coordinate access on Mac with its declared NumPy 2
dependency; pinned 3.1.0.4/NumPy 1.26.4 passes both real PyMOL integration checks.
The setup probe now checks coordinates rather than only imports. A pinned-base
Apptainer definition rebuilds Linux environments from the same lockfiles.

Packaging now stages wheels/catalogs/ZIPs outside source. `build_release.py`
refuses dirty source and builds a `git archive` of the exact HEAD, recording
revision, tree, engine identity and artifact hashes. Generated wheels are no
longer committed inside source; this avoids a circular source/artifact identity.

- Bound Python and browser decompression before allocating decoded bundles;
  validate all parts, metadata, names, aggregate sizes and checksums.
- Installation is explicit and isolated. No automatic apt/sudo, user-site pip,
  global interpreter modification or setup merely to view a structure.
- PyMOL sessions are executable trusted inputs. A checksum proves byte identity,
  not safety. Restoring a bundle must not open or execute its contents.
- Validate source and a clean installed wheel, real still/animation rendering,
  playback, session/scene reopening and bundle restoration. Record actual
  results and distinguish simulations from hosted-client acceptance.
- Freeze and version the source, then build the wheel and both plugin ZIPs from
  that exact revision in a separate staging directory. Record source SHA and
  artifact hashes. Generated packages must not change the frozen source.
- Use uv-managed environments locally and rebuild from the same pins inside
  Apptainer on Linux. Linux/CUDA execution remains a separate gate from Mac tests.
