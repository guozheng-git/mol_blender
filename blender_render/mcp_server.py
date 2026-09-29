"""Optional stdio MCP adapter for local Claude/Codex sessions."""
from pathlib import Path
from .config import config_root


def create_stdio():
    from fastmcp import FastMCP
    from fastmcp.utilities.types import Image
    from .authoring import list_presets, inspect_preset
    from .local import render_file
    mcp = FastMCP('Blender Render Local')

    @mcp.tool()
    def presets() -> list[dict]:
        """List built-in and saved local styles without launching Blender."""
        return list_presets(config_root() / 'presets')

    @mcp.tool()
    def inspect_style(preset: str) -> dict:
        """Read the complete JSON style for a built-in or user/NAME preset."""
        return inspect_preset(preset, config_root() / 'presets')

    @mcp.tool()
    def render_structure(input_path: str, output_path: str, preset: str = 'preset_1_interface',
                         style_json: str | None = None, rough: bool = False,
                         save_blend: bool = False) -> dict:
        """Render a trusted local PSE/CIF/PDB/bundle with configured local Blender.

        output_path must be new. Returns PNG and manifest paths after completion.
        Use read_render on the returned PNG to inspect its pixels.
        """
        import json
        options = {'style_doc': json.loads(Path(style_json).expanduser().read_text())} if style_json else {'preset': preset}
        options.update(rough=rough, save_blend=save_blend)
        return render_file(input_path, output=output_path, options=options)

    @mcp.tool()
    def read_render(path: str) -> Image:
        """Read a local render or reference PNG as native MCP image content."""
        from PIL import Image as PILImage
        path = Path(path).expanduser().resolve()
        with PILImage.open(path) as png:
            if png.format != 'PNG':
                raise ValueError('Expected a PNG')
            png.verify()
        return Image(data=path.read_bytes(), format='png')

    return mcp
