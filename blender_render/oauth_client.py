"""Browser OAuth for the CLI and local-file Codex adapter.

Only the explicit login command can open a browser. Tokens refresh through
FastMCP and are encrypted at rest in a directory readable only by this user.
"""
from __future__ import annotations
import asyncio
import hashlib
import secrets
from .config import config_root
from .runtime import file_lock, write_private


def access_token(url: str, *, interactive=False) -> str:
    from fastmcp import Client
    from fastmcp.client.auth import OAuth
    from key_value.aio.stores.disk import DiskStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
    from .client import ClientError
    if not url.startswith("https://"):
        raise ClientError("OAuth profiles require an HTTPS endpoint.")
    root = config_root() / "oauth" / hashlib.sha256(url.encode()).hexdigest()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    with file_lock(root / "login.lock"):
        key = root / "encryption-key"
        if not key.exists():
            write_private(key, secrets.token_urlsafe(48))
        class ProfileOAuth(OAuth):
            async def redirect_handler(self, authorization_url):
                if not interactive:
                    raise ClientError("Sign in with blender-render login --url " + url)
                return await super().redirect_handler(authorization_url)
        async def connect():
            async with DiskStore(directory=root / "tokens") as disk:
                encrypted = FernetEncryptionWrapper(disk, source_material=key.read_text(), salt="blender-render-client-v1")
                auth = ProfileOAuth(url.rstrip("/") + "/mcp", client_name="Blender Render CLI", token_storage=encrypted)
                async with Client(url.rstrip("/") + "/mcp", auth=auth) as client:
                    await client.list_tools()
                    tokens = await auth.token_storage_adapter.get_tokens()
                    if not tokens:
                        raise ClientError("OAuth did not return an access token.")
                    return tokens.access_token
        return asyncio.run(connect())
