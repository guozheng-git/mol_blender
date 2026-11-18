"""PR #3 delivery boundary for an embedding, authenticated MCP service.

No queues, render submission, downloads, arbitrary local paths or installs.
The provider must authorize each (owner, job_id) lookup before returning bytes.
Its URLs must be non-consuming: returning a reference is not a delivery receipt.
"""
import base64
from dataclasses import dataclass
import hashlib
import io
import re
from urllib.parse import urlsplit

MAX_PNG_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 64_000_000
PREVIEW_BYTES = 384 * 1024
VIEWER_URI = 'ui://mol-blender/hosted-delivery-v1.html'


@dataclass(frozen=True)
class CompletedPNG:
    """Verified original bytes plus an authorized, non-consuming HTTPS URL."""
    data: bytes
    url: str
    sha256: str
    expires_at: int


def _origin(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment):
        raise ValueError('Expected an absolute HTTPS artifact URL')
    return f'{parsed.scheme}://{parsed.netloc}'


def _verified_image(artifact):
    from PIL import Image
    if not isinstance(artifact, CompletedPNG):
        raise ValueError('Provider must return a completed PNG')
    if (not isinstance(artifact.data, bytes) or not 0 < len(artifact.data) <= MAX_PNG_BYTES
            or not re.fullmatch('[a-f0-9]{64}', artifact.sha256)
            or hashlib.sha256(artifact.data).hexdigest() != artifact.sha256):
        raise ValueError('Original PNG size or checksum mismatch')
    with Image.open(io.BytesIO(artifact.data)) as image:
        if image.format != 'PNG' or image.width * image.height > MAX_PIXELS:
            raise ValueError('Expected a bounded PNG')
        image.verify()
    with Image.open(io.BytesIO(artifact.data)) as image:
        preview = image.convert('RGBA')
    preview.thumbnail((1024, 1024))
    while True:
        buffer = io.BytesIO()
        preview.save(buffer, format='PNG')
        if buffer.tell() <= PREVIEW_BYTES:
            return buffer.getvalue()
        preview.thumbnail((max(1, preview.width * 3 // 4), max(1, preview.height * 3 // 4)))


def create_adapter(provider, *, auth, owner, file_origins):
    """Build optional tools around ``provider(owner(), job_id)``.

    ``auth`` must be an explicitly configured FastMCP AuthProvider. ``owner``
    derives the authenticated identity from the request, never tool arguments.
    ``provider`` must enforce ownership, expiry and completeness on every call.
    Only deploy this through the embedding service's authenticated transport.
    """
    import time
    from fastmcp import FastMCP
    from fastmcp.tools.tool import ToolResult
    from mcp import types
    from .native_handoff import native_png_handoff
    from .viewer import html_resource, inline_viewer_html

    if auth is None or not callable(owner) or not callable(provider):
        raise ValueError('Explicit authentication, owner and authorized result provider are required')
    origins = list(dict.fromkeys(file_origins))
    if not origins or any(_origin(url) != url for url in origins):
        raise ValueError('Configure explicit HTTPS artifact origins')
    domains = origins + ['https://*.oaiusercontent.com', 'https://chatgpt.com']
    metadata = {'ui': {'prefersBorder': False, 'csp': {
        'connectDomains': domains, 'resourceDomains': domains}},
        'openai/widgetCSP': {'connect_domains': domains, 'resource_domains': domains}}

    class DeliveryMCP(FastMCP):
        async def _read_resource_mcp(self, uri):
            if str(uri) == VIEWER_URI:
                return html_resource(uri, inline_viewer_html(), metadata)
            return await super()._read_resource_mcp(uri)

    server = DeliveryMCP('mol-blender Delivery', auth=auth, mask_error_details=True)
    @server.resource(VIEWER_URI, mime_type='text/html;profile=mcp-app')
    def inline_preview():
        return inline_viewer_html()
    hints = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True,
             'openWorldHint': True}
    ui_meta = {'ui': {'resourceUri': VIEWER_URI}, 'openai/outputTemplate': VIEWER_URI}

    def acquire(job_id):
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,80}', job_id):
            raise ValueError('Invalid job ID')
        identity = owner()
        if not isinstance(identity, str) or not identity:
            raise PermissionError('Authenticated identity required')
        artifact = provider(identity, job_id)
        preview = _verified_image(artifact)
        if type(artifact.expires_at) is not int or artifact.expires_at <= time.time():
            raise ValueError('Artifact expired; do not rerender')
        if _origin(artifact.url) not in origins:
            raise ValueError('Artifact origin is not configured')
        # Retain the viewer's explicit non-consuming URL convention from PR #3.
        from urllib.parse import parse_qs
        parsed = urlsplit(artifact.url)
        if not parsed.path.startswith('/download/') or parse_qs(parsed.query).get('delivery') != ['ack']:
            raise ValueError('Expected a non-consuming /download/ URL with delivery=ack')
        return artifact, preview

    @server.tool(annotations=hints)
    def get_render_png_file(job_id: str) -> ToolResult:
        """Hand off an existing PNG; import and permanent saving are unconfirmed."""
        artifact, _ = acquire(job_id)
        return native_png_handoff(url=artifact.url, filename=f'mol-blender-{job_id}.png',
            size=len(artifact.data), sha256=artifact.sha256, expires_at=artifact.expires_at)

    @server.tool(annotations=hints, meta=ui_meta)
    def render_result(job_id: str) -> ToolResult:
        """Read one completed PNG. Never renders, retries compute or confirms host storage."""
        artifact, preview = acquire(job_id)
        result = native_png_handoff(url=artifact.url, filename=f'mol-blender-{job_id}.png',
            size=len(artifact.data), sha256=artifact.sha256, expires_at=artifact.expires_at)
        pixels = types.ImageContent(type='image', mimeType='image/png',
                                    data=base64.b64encode(preview).decode())
        result.content.insert(0, pixels)
        result.structured_content['job_id'] = job_id
        result.meta = {'preview': {'mimeType': pixels.mimeType, 'data': pixels.data},
            'saveFiles': [{'jobId': job_id, 'name': 'render.png', 'mimeType': 'image/png',
                           'url': artifact.url}],
            'downloads': [{'jobId': job_id, 'name': 'render.png', 'url': artifact.url}]}
        return result

    @server.tool(annotations=hints, meta=ui_meta)
    def open_saved_image() -> ToolResult:
        """Open the host's image picker without contacting the render provider."""
        if not owner():
            raise PermissionError('Authenticated identity required')
        return ToolResult(content=['Select a saved image with Use saved image.'],
            structured_content={'status': 'select_library_image', 'space_save_confirmed': False},
            meta={'library': {'mode': 'reuse'}})

    return server
