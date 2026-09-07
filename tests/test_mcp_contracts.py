import asyncio
import json
import pytest
from types import SimpleNamespace
from PIL import Image as PillowImage
from fastmcp import Client as MCPClient
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from blender_render.mcp_server import create_stdio, create_http
from blender_render.api import Downloads


def test_native_preview_and_stdio_tools(monkeypatch, tmp_path):
    import blender_render.client
    path = tmp_path / 'preview.png'
    PillowImage.new('RGBA',(8,8),(20,100,40,128)).save(path)
    class Client:
        def download(self, identifier, output):
            assert identifier == 'job'
            return {'outputs':{'render.png':str(path)},'manifest':{'dss_ran':True}}
    def configured(profile):
        # OAuth's synchronous adapter starts its own asyncio loop. Tool calls
        # must run that adapter outside the MCP server's event loop.
        with pytest.raises(RuntimeError, match='no running event loop'):
            asyncio.get_running_loop()
        return Client()
    monkeypatch.setattr(blender_render.client,'configured_client',configured)
    async def run():
        async with MCPClient(create_stdio()) as client:
            tools = {t.name:t for t in await client.list_tools()}
            assert set(tools) == {'list_presets','list_structures','delete_structure','import_structure','render_structure','submit_render','render_status','render_result','cancel_render','available_quota'}
            assert tools['submit_render'].annotations.idempotentHint
            assert tools['render_structure'].annotations.idempotentHint
            result = await client.call_tool('render_result',{'job_id':'job','output_path':str(path)})
            assert any(item.type == 'image' and item.mimeType == 'image/png' for item in result.content)
            assert any(item.type == 'text' and 'dss_ran' in item.text for item in result.content)
    asyncio.run(run())


def test_chatgpt_declared_file_contract():
    auth = StaticTokenVerifier(tokens={})
    service = SimpleNamespace()
    server = create_http(service,auth=auth,downloads=Downloads('x'*48,''),converter=None)
    async def run():
        async with MCPClient(server) as client:
            tools = {t.name:t for t in await client.list_tools()}
            imp = tools['import_structure']
            assert imp.meta['openai/fileParams'] == ['file']
            file_schema = imp.inputSchema['properties']['file']
            assert {'download_url','file_id'} <= set(file_schema['required'])
            assert {'download_url','file_id','mime_type','file_name'} == set(file_schema['properties'])
            assert file_schema['properties']['file_name']['type'] == 'string'
            assert tools['render_structure'].meta['openai/fileParams'] == ['file']
            assert tools['render_structure'].annotations.idempotentHint
            assert tools['render_status'].annotations.readOnlyHint
            assert tools['render_status'].inputSchema['properties']['wait_seconds']['maximum'] == 25
            assert tools['render_result'].outputSchema
            uri=tools['render_result'].meta['ui']['resourceUri']
            resource=await client.read_resource(uri)
            assert resource[0].mimeType == 'text/html;profile=mcp-app'
            assert resource[0].meta['ui']['csp']['connectDomains'] == []
    asyncio.run(run())


def test_preview_origin_is_separate_from_artifact_network_permissions():
    endpoint = 'https://almostscheidplatz--blender-render-beta-gateway.us-east.modal.direct'
    widget = 'https://almostscheidplatz--blender-render-beta.modal.run'
    server = create_http(SimpleNamespace(), auth=StaticTokenVerifier(tokens={}),
        downloads=Downloads('x'*48, endpoint), converter=None, widget_origin=widget)
    async def run():
        async with MCPClient(server) as client:
            resource = (await client.read_resource('ui://blender-render/result-v1.html'))[0]
            assert resource.meta['ui']['domain'] == widget
            assert resource.meta['ui']['csp']['resourceDomains'] == [endpoint]
            assert resource.meta['openai/widgetCSP']['redirect_domains'] == [endpoint]
    asyncio.run(run())
