"""Small public landing and policy pages, available without signing in."""
from html import escape

REPOSITORY = "https://github.com/magnusbauer/blender_render"

CONTENT = {
    "home": ("Blender Render", """
<p>Ray-traced molecular images from your PyMOL session, with its saved pose,
painted materials, and a transparent background.</p>
<p>Use your Mac, your lab's cluster, or the hosted service. Connect through Codex
or ChatGPT, preview the result, and download a PNG or an editable Blender scene.</p>
<p>This is a private validation beta. The repository and hosted compute are
restricted to the owner; public publication and publisher verification are deferred.</p>
<p>The hosted beta uses GitHub sign-in. Its initial limits are five renders and
twenty GPU minutes per day, one active job per person, and a maximum image
dimension of 4096 pixels. Availability is limited by a fixed credit allowance.</p>
<p><a href="REPO">Installation and source code</a></p>
<p>The MCP endpoint is <code>/mcp</code>. Directory publication is subject to
OpenAI review; a custom connection is available for release validation.</p>"""),
    "privacy": ("Privacy and retention", """
<p>Blender Render is operated by Magnus Bauer. The hosted service receives the
molecular file you select, saved camera and color metadata, render settings,
and your GitHub numeric identity. GitHub login requests public identity access
only, without repository or email permissions.</p>
<p>Files are used to produce your requested image. PSE conversion runs in an
isolated environment without network access or service credentials. Hosted
compute and storage use Modal. Credentials are encrypted at rest. Jobs and
results are checked against their owner.</p>
<p>Inputs expire after 24 hours; outputs expire 24 hours after completion.
Temporary conversion and rendering files are removed after each operation.
Download links expire after fifteen minutes. Anyone given a valid download
link can use it until expiry. Downloaded copies remain under your control.</p>
<p>Minimal identity, job, timestamp, status, idempotency, and cost records remain
to enforce quotas and prevent duplicate spending. There is no public gallery.
The service does not use uploaded structures to train models.</p>
<p>Local rendering stays on your computer. Your own-HPC service follows your
lab's policy and does not send structures through the shared Modal service.</p>
<p>Your use of ChatGPT, Codex, GitHub, and Modal is also governed by those
providers' policies. See the <a href="REPO/blob/main/docs/privacy.md">full policy</a>
and <a href="REPO/issues">contact the operator</a> for access or deletion requests.
Do not post confidential files or credentials in issues.</p>"""),
    "terms": ("Beta terms", """
<p>You must have permission to process the molecular structures you upload.
Use the service for molecular still-image rendering. Uploaded scripts and
Blender scenes are unsupported.</p>
<p>This beta is provided without a service-level guarantee. Rendering can fail,
jobs can be cancelled, and admission can stop when credits expire or the fixed
project budget is exhausted. Keep your original structures and download results
you want to retain before they expire.</p>
<p>Rendered figures are visualizations. Verify scientific interpretations
against the original coordinates and experimental evidence.</p>
<p>The bridge code and dependencies retain their respective licenses.
See <a href="REPO/blob/main/THIRD_PARTY_NOTICES.md">dependency notices</a> and
<a href="REPO/blob/main/docs/terms.md">full terms</a>.</p>"""),
}


def page(name):
    title, content = CONTENT[name]
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>' + escape(title) + '</title><style>'
        'body{font:17px/1.65 system-ui,sans-serif;max-width:760px;margin:64px auto;padding:0 24px;color:#20372a;background:#fafcf8}'
        'h1{font-size:2.4rem;line-height:1.2}a{color:#27613e}nav{display:flex;gap:24px;border-top:1px solid #cbd6c8;padding-top:20px;margin-top:40px}'
        '</style><main><h1>' + escape(title) + '</h1>' + content.replace('REPO/blob/main/', 'REPO/blob/codex/public-release/').replace('REPO',REPOSITORY) +
        '</main><nav><a href="/">Blender Render</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav></html>')
