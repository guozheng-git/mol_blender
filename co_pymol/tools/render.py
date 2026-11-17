"""Rendering MCP tools."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from co_pymol.tools.errors import error_wrapper
from co_pymol.scene.session import pymol_session
from co_pymol.scene.preview import apply_plddt_palette, render_image


def register_render_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    @error_wrapper
    def color_by_plddt(selection: str = "all") -> str:
        """Color by pLDDT (B-factor 0–100) with discrete AlphaFold bins.

        Blue ≥90, cyan 70–<90, yellow 50–<70, orange <50.
        """
        with pymol_session() as cmd:
            apply_plddt_palette(cmd, selection)
            return f"Colored {selection} by pLDDT"

    @mcp.tool(structured_output=False)
    @error_wrapper
    def render(width: int = 800, height: int = 600, ray: bool = True) -> Image | str:
        """Render current view as an image. ray=True for high quality (slower)."""
        with pymol_session() as cmd:
            return Image(data=render_image(cmd, width, height, ray=ray), format="png")

    @mcp.tool(structured_output=False)
    @error_wrapper
    def snapshot(width: int = 800, height: int = 600) -> Image | str:
        """Quick snapshot without ray tracing. Faster, lower quality."""
        with pymol_session() as cmd:
            return Image(data=render_image(cmd, width, height, ray=False), format="png")
