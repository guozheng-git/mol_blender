import asyncio
import base64
from dataclasses import replace
import hashlib
import io
import time

import pytest
from PIL import Image

from mol_blender.hosted.adapter import CompletedPNG, VIEWER_URI, create_adapter


@pytest.fixture
def artifact():
    output = io.BytesIO()
    Image.new('RGBA', (20, 10), (12, 40, 80, 100)).save(output, format='PNG')
    data = output.getvalue()
    return CompletedPNG(data, 'https://render.example.org/download/job?delivery=ack',
                        hashlib.sha256(data).hexdigest(), int(time.time()) + 300)


def server(provider):
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    return create_adapter(provider, auth=StaticTokenVerifier({'test': {'client_id': 'alice', 'scopes': []}}),
                          owner=lambda: 'alice', file_origins=['https://render.example.org'])


def test_read_only_delivery_and_reuse(artifact):
    from fastmcp import Client
    calls = []
    def provider(owner, job):
        calls.append((owner, job))
        assert owner == 'alice' and job == 'job'
        return artifact
    async def check():
        async with Client(server(provider)) as client:
            assert {t.name for t in await client.list_tools()} == {
                'render_result', 'get_render_png_file', 'open_saved_image'}
            result = await client.call_tool('render_result', {'job_id': 'job'})
            assert base64.b64decode(result.content[0].data) == artifact.data
            assert result.structured_content['file']['sha256'] == artifact.sha256
            assert result.structured_content['space_save_confirmed'] is False
            resource = (await client.read_resource(VIEWER_URI))[0]
            assert resource.meta['ui']['csp']['connectDomains'][0] == 'https://render.example.org'
            assert 'viewer-options' in resource.text
            await client.call_tool('open_saved_image', {})
            assert calls == [('alice', 'job')]  # Reuse must not contact the original provider.
    asyncio.run(check())


@pytest.mark.parametrize('change', [
    {'sha256': '0' * 64}, {'expires_at': 0},
    {'url': 'https://other.example.org/download/job?delivery=ack'},
    {'url': 'https://render.example.org/download/job'},
    {'url': 'file:///tmp/render.png'},
])
def test_invalid_artifacts_do_not_return_delivery(artifact, change):
    from fastmcp import Client
    async def check():
        async with Client(server(lambda owner, job: replace(artifact, **change))) as client:
            result = await client.call_tool('render_result', {'job_id': 'job'}, raise_on_error=False)
            assert result.is_error and not any(c.type == 'image' for c in result.content)
    asyncio.run(check())


def test_provider_denial_is_not_bypassed():
    from fastmcp import Client
    def denied(owner, job):
        raise PermissionError('Not your render')
    async def check():
        async with Client(server(denied)) as client:
            result = await client.call_tool('get_render_png_file', {'job_id': 'job'}, raise_on_error=False)
            assert result.is_error
    asyncio.run(check())


def test_no_unauthenticated_adapter():
    with pytest.raises(ValueError, match='authentication'):
        create_adapter(lambda *_: None, auth=None, owner=lambda: 'alice', file_origins=[])
