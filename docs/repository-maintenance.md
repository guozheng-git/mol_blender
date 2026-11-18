# Repository maintenance

The `CI` workflow checks pull requests and pushes to `main`, and runs weekly so
new advisories against unchanged locks are detected. Its final `CI` status is
successful only when the test/package, dependency-audit and secret-scan jobs
all succeed. Workflow permissions are read-only; fork pull requests do not need
repository secrets. Third-party actions are pinned to commit hashes.

## Administrator settings

Repository files do not themselves enable GitHub branch protection. An account
with repository **Administration: write** must apply the reviewed policy:

```sh
gh api --method PUT repos/magnusbauer/blender_render/branches/main/protection \
  --input .github/main-protection.json
```

The policy requires the `CI` status, an up-to-date branch, one approving review,
resolution of conversations, and reapproval after changes. It applies to admins
and disallows force pushes and deletion. Publish the workflow and confirm its
`CI` check runs before requiring that check for merges. Private repositories
also need a GitHub plan that supports branch protection.

GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)
is a public-repository feature. During a separately authorized public release,
an administrator should enable it and verify the report form:

```sh
gh api --method PUT repos/magnusbauer/blender_render/private-vulnerability-reporting
```

Until the form is available, use the private-channel fallback in `SECURITY.md`.

Verify the applied state rather than treating a successful local edit as setup:

```sh
gh api repos/magnusbauer/blender_render/branches/main/protection
gh api repos/magnusbauer/blender_render/private-vulnerability-reporting
```

Do not change repository visibility as part of these settings. Data-release
permission and historical refs require a separate review.

## Local checks

```sh
uv sync --locked --all-extras --group security
uv run --no-sync python scripts/sync_render_defaults.py --check
uv run --no-sync python scripts/audit_dependencies.py --output .validation/dependencies.json
uv run --no-sync python -m pytest tests --ignore=tests/co_pymol
uv run --no-sync python scripts/build_plugin.py --output .validation/package-check
```

Run co-PyMOL tests in a Python environment containing real PyMOL, with the
renderer tests' detached helpers using Python 3.11. Browser checks require an
explicit Chrome/Chromium path through `--browser=PATH`; native Blender/PyMOL
checks use `MOL_BLENDER_TEST_BLENDER`, `MOL_BLENDER_TEST_SITE` and
`MOLBLENDER_STUDIO_TEST_PYMOL`. The default CI suite does not establish Blender
GPU or Slurm acceptance. Report executed checks and limits in the pull request.

Refresh locks with the commands in [runtime environments](runtime-environments.md).
Keep scientific ABI pins stable unless separate runtime validation justifies
changing them. Update `mol_blender/runtime-sources.json` whenever a native
runtime version changes, preserving upstream source URLs, hashes and notices.
The advisory script fails on findings, unverified packages or service errors;
it does not ignore vulnerabilities or silently skip alternate lock versions.

The secret scanner's allowlist covers only exact public cache-hash lines and
specific documented/test examples. Do not add broad path, keyword or entropy
exclusions to make a finding disappear. Review and rotate a real credential
before considering history cleanup.
