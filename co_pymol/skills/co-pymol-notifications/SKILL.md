---
name: co-pymol-notifications
description: Enable, test, disable or troubleshoot co-pymol Blender render completion notifications, including macOS permission, missing alerts, incorrect click targets and older render watchers. Use during tool installation or when the user asks for render alerts; does not send email, chat messages or phone push notifications.
---

# Blender completion notifications

Make local completion alerts work without making the user manage app paths or
poll the chat. Normal `co-pymol setup <client>` installs this skill and prepares
macOS notifications. Installation of this guidance alone is not a request to
send alerts; follow the user's tool setup or notification request.

## Supported experience

The bundled helper is **Blender Renders**, a local macOS app. The installer
builds it using Apple's installed Command Line Tools, normally at
`~/Applications/Blender Renders.app`. It reuses a previously configured app
path when migrating. There is no server-side push service: the local render
helper posts after downloading and verifying the PNG. The desktop must be
awake/available for local delivery; remote render completion alone is not enough.
Linux/Windows desktop notifications and mobile/chat push are not implemented.
Explain that boundary if relevant, rather than promising those channels.

Use the existing PyMOL Python and the CLI from the same installation as the
running tool. Examples below use `co-pymol`; the equivalent is
`/path/to/pymol-python -m co_pymol` followed by the same arguments.

## Setup and verify

1. Read `co-pymol notifications status`. This checks local preferences and OS
   authorization without posting a notification or allocating compute.
2. For an enable/repair request, run `co-pymol notifications setup`. It installs
   or updates the helper at its stable path and opens its permission flow.
   Have the user choose **Allow** in the macOS prompt. If permission was denied,
   direct them to **System Settings > Notifications > Blender Renders**; retrying
   permission requests cannot override their OS choice. Do not reset notification
   databases or grant permission by changing unrelated system settings.
3. After permission is granted, setup posts a small readiness alert. When needed,
   run `co-pymol notifications test` once. It uses no image, remote login or GPU
   job. A successful result means macOS accepted the request, not that a banner
   was visibly displayed. Check Notification Center or ask the user whether they
   saw it before calling visual delivery verified. Do not repeatedly post tests.
4. Future downloads notify automatically; the user does not need to add
   `notification_app` to each Blender config. A render alert's normal click
   reveals its image in Finder; **Open Image** opens it in the default app.
   A missing image falls back to its saved folder. Setup/test alerts have no
   image target and should simply dismiss when clicked.

An ordinary repeat of `setup <client>` preserves an explicit notification opt-out
and does not repeatedly open permission setup. `setup <client> --no-notifications`
skips notification onboarding, preserving current preferences. Explicit
`notifications setup` enables notifications again; `notifications disable`
disables future completion alerts without cancelling renders or deleting images.

## Diagnose the failed layer

- **`not_installed` / build error:** confirm macOS and installed Apple Command
  Line Tools. The package includes the Objective-C source and build code, so a
  source checkout or copied script is not required. If development tools are
  missing, explain the `xcode-select --install` step and let the user complete
  its OS installation flow. A notification setup failure leaves MCP usable.
  On Apple Silicon, the installer launches native build tools even when PyMOL's
  Python runs under Rosetta. A `libxcrun` architecture mismatch calls for updating
  this installer, not installing a second copy of the Command Line Tools.
- **`permission_pending` / `unavailable`:** check the returned app path and error.
  If the agent runs outside the logged-in desktop session, have the user run
  `notifications setup` in their desktop terminal. Do not report permission as
  granted just because `open` succeeded. Recheck status after the user acts.
- **Authorization is denied:** use the system notification setting; do not
  recompile or create fresh app identities to trigger repeated prompts.
- **Authorized but no banner:** inspect `alerts_enabled`,
  `notification_center_enabled` and `sounds_enabled`, then check the user's Focus
  mode and notification presentation settings. Preserve those choices. An
  accepted notification can be quiet; `sent: true` is not a visibility receipt.
- **No alert for a render:** locate it with `blender_renders` / `blender_status`.
  A queued, rendering or transfer-pending job has no completion alert yet.
  Inspect that run's `state.json` `notification` result and downloaded image.
  Failed notification delivery must leave a verified render `completed`.
  Recover an interrupted download with `blender_result`, never a new render.
- **Wrong or missing click target:** check the path retained by that specific
  notification and whether the PNG/folder still exists. Keep the helper at its
  configured stable path. Moving the app or render files can invalidate older
  alerts; reading a saved result does not rewrite old notifications.

Current preferences live at `~/.config/co-pymol/notifications.json`. They contain
only enablement, the app path and onboarding state. Keep other fields when
repairing them; preserve a malformed file before a targeted correction. Current
preferences are read when a job completes, so they apply to pending jobs using
the updated worker as well as new ones. Workers already running before a code
upgrade retain their older behavior; let them finish or recover retained work
after they stop instead of killing or resubmitting a live job.
An explicit global disable takes precedence over the legacy
`notification_app` path in a render request.

## Avoid duplicate alerts and unnecessary work

New runs use their existing detached download helper. Do not add a separate
watcher or chat polling loop. After render submission, acknowledge the receipt
and end the turn, leaving delivery to that helper. Reading a completed result
does not resend its alert, and turning notifications on does not replay all
previously completed renders. Never rerender merely to test notifications.

Older installations may have a separate `watch_render_notifications.py` process
with `watch.lock`, `pid` and `notified-renders.json`. Inspect its exact command,
batch and state directory before retiring it or changing it. Preserve its sent
history, do not delete locks to force a second watcher, and never signal a saved
PID without verifying it still belongs to that specific watcher. Use one owner
for each run's alert delivery. Legacy AppleScript alerts cannot be upgraded
retroactively into the native clickable alerts.

Conclude with the actual state: helper installed, permission pending/allowed,
test accepted/visibly confirmed, or a precise remaining blocker. Describe the
one next user action if OS permission or desktop access is required. Do not claim
that a setup test verifies the full GPU/render/download pipeline.

For OS behavior, consult Apple's [notification permission guidance](https://developer.apple.com/documentation/usernotifications/asking-permission-to-use-notifications)
and [local notification scheduling documentation](https://developer.apple.com/documentation/usernotifications/scheduling-a-notification-locally-from-your-app).
