# Modal deployment and budgets

The private beta uses GitHub login and admits only the owner's account. Public
admission is deferred. The renderer runs on one L4 GPU and scales to
zero between jobs. A single controller owns the SQLite database on a dedicated
Modal Volume. Do not deploy a second controller against that Volume.

## Use the hosted service from files on your computer

Install the release wheel with its `mcp` extra in Python 3.11, then:

```sh
blender-render login --profile hosted
blender-render render pose.pse --profile hosted --preset dried_paint_green \
  --transparent --color-follow --save-blend -o figure.png
```

Login opens your browser once. Tokens are encrypted in the user configuration
directory and refresh automatically. A later need to sign in is reported rather
than opening a browser during an unattended render. PSE conversion runs in a
short-lived Modal sandbox without network access, mounted volumes, OAuth
credentials, or renderer credentials. A local Blender installation is unnecessary
when using the hosted file-upload profile.

## Deploy in your own account

Install the wheel's `modal` extra and authenticate the Modal CLI with your own
account. Choose a unique app name and an explicit lifetime budget covered by
your available credits. Keep your workspace spending limit enabled.

Create a GitHub OAuth app with these URLs, replacing `WORKSPACE` and `APP`:

- Homepage: `https://WORKSPACE--APP.modal.run`
- Authorization callback: `https://WORKSPACE--APP.modal.run/auth/callback`
- Required identity permission: `read:user`. Repository access is unnecessary.

Provide `MODAL_WORKSPACE`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` through
your shell's secret-management mechanism. Do not commit them. For example:

```sh
blender-render deploy --personal blender-render-my-lab \
  --budget-usd 50 --credits-expire 2027-03-01 --staging-user YOUR_GITHUB_NUMERIC_ID
```

The personal service is restricted to that GitHub account. It has a separate
Volume, credentials, thirty-minute job timeout, and larger daily quota. The
deployment command prints the HTTPS MCP URL and saves a compute profile. Connect
that URL in Codex or ChatGPT, or run `blender-render login --url URL --profile mine`.
An own-HPC profile is the option for jobs exceeding these limits.

## Operate the shared beta

The initial lifetime project ceiling is $530: up to $50 for development,
$30 for service/storage/shutdown, and $450 for public rendering. Development
reserves $40 for compute and $10 for build/control/storage. No recurring credit
grant or service restart replenishes these ledgers. No payment method is added.

Admission reserves worst-case GPU time, CPU, memory, startup, and conversion
costs before dispatch. Reservations and job ownership are committed to the
Volume before starting GPU work. A lost execution is conservatively charged its
reservation and is never automatically dispatched again. Reusing an idempotency
key returns the original job, even after a controller restart.

The controller prepays each hour from the operating allowance. Two dollars
remain outside that allowance for the final retention period. On exhaustion or
near credit expiry it closes admission, stops compute, preserves unexpired
downloads, removes expired data, and stops the Modal app. The shutdown deadline
is durable. The shared grant expires March 31, 2027; admission closes early
enough for the final 24-hour retention period.

Daily per-user limits are one active job, five renders, twenty GPU minutes,
2048-pixel default output, 4096-pixel maximum dimension, and a ten-minute job
timeout. Check quota with the plugin or `blender-render quota --profile hosted`.
Expiring download links last fifteen minutes; input and output files last
twenty-four hours. There is no public gallery.

## Upgrades and incident shutdown

Use `blender-render deploy`, not a direct `modal deploy`, for upgrades. It drains
the controller, waits for active work, stops the old deployment, then recreates
it. Credentials and reservations remain on the existing Volume. An unhealthy
public service blocks the upgrade rather than risking concurrent SQLite writers.

Restricted validation uses `blender-render deploy --staging-user ID`.
Unrestricted admission requires an executed release-validation report containing
all required passing checks. The CLI does not treat a deployment as validation.

To stop a deployment explicitly, use `modal app stop APP_ID --yes` for the
verified application ID. Do not delete a Volume containing unexpired user data.
Review actual Modal billing, image-build costs, and stored bytes against the
conservative project ledger before each public release. Billing can lag usage.

The Python service dependencies, Blender distribution, Molecular Nodes package,
and CCD data are pinned separately. See the runtime locks, container build
instructions, and third-party source bundle for reproducibility and licensing.
