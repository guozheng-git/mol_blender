# Modal deployment and budgets

The private beta uses GitHub login and admits only the owner's account. Public
admission is deferred. The renderer runs on one L4 GPU and scales to
zero between jobs. A single controller owns the SQLite database on a dedicated
Modal Volume containing only authentication and minimal job/accounting state.
Molecular data uses temporary container storage. Both controller and GPU worker
use Modal HTTP Servers, not Function arguments or return values. Do not deploy
a second controller against that Volume.

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

- Homepage: `https://WORKSPACE--APP-gateway.us-east.modal.direct`
- Authorization callback: `https://WORKSPACE--APP-gateway.us-east.modal.direct/auth/callback`
- Required identity permission: `read:user`. Repository access is unnecessary.

Provide `MODAL_WORKSPACE`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` through
your shell's secret-management mechanism. Do not commit them. For example:

```sh
blender-render deploy --personal blender-render-my-lab \
  --budget-usd 50 --credits-expire 2027-03-01 --staging-user YOUR_GITHUB_NUMERIC_ID
```

The personal service is restricted to that GitHub account. It has a separate
Volume, credentials, and thirty-minute job timeout. The
deployment command prints the HTTPS MCP URL and saves a compute profile. Connect
that URL in Codex or ChatGPT, or run `blender-render login --url URL --profile mine`.
An own-HPC profile is the option for jobs exceeding these limits.

The deployment also sets a dedicated, short widget origin for ChatGPT's preview
sandbox. `BLENDER_RENDER_WIDGET_ORIGIN` can override it. This is the widget's
identity; artifact URLs and CSP permissions still use the actual HTTP Server
endpoint. Keep the widget hostname short enough for ChatGPT's derived DNS label.

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
enough for a conservative 24-hour shutdown buffer. New undelivered files expire
after fifteen minutes.

The daily limit is ten completed renders per user; queued/running jobs reserve
slots, while failed/cancelled jobs release them. There is no upload-count or
daily GPU-minute quota. The private beta's owner (`github:11480757`, verified
through OAuth) has no daily testing quota. This exemption never bypasses budget
reservations, credit expiry, the 4096-pixel maximum, or job timeouts. Output
defaults to 2048 pixels. Check `blender-render quota --profile hosted`.

Inputs are deleted after their final active render finishes, fails, or is
cancelled. Unsubmitted uploads expire after five minutes. Files are deleted
after download, or fifteen minutes after completion if not downloaded. The GPU
Blender process is stopped after each render. The GPU Server requires a Modal
proxy token before requests can start a container, plus a separate worker token;
it receives no OAuth credentials. There is no archive or public gallery.

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
