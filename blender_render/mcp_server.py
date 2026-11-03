"""Optional stdio MCP adapter for local Claude/Codex sessions."""
from pathlib import Path
from .presets import DEFAULT_PRESET
from .config import config_root


def create_stdio(*, editor_enabled=False):
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
    def render_structure(input_path: str, output_path: str, preset: str = DEFAULT_PRESET,
                         style_json: str | None = None, rough: bool = False,
                         save_blend: bool = False, animation: str | None = None,
                         duration: float | None = None, fps: int | None = None,
                         rock_angle: float | None = None, gif: bool | None = None,
                         width: int | None = None, height: int | None = None,
                         samples: int | None = None, timeout: float = 600, warm: bool = False,
                         open_blender: bool = False, edit_bridge: bool = False) -> dict:
        """Render a trusted local PSE/CIF/PDB/bundle with configured local Blender.

        animation: rock or 360; gif=True adds a looping GIF alongside MP4 and saved frames.
        rock_angle is the one-sided swing in degrees. output_path must be new.
        Returns PNG, manifest and requested movie paths after completion.
        Use read_render on the returned PNG to inspect its pixels.
        warm=True explicitly starts/reuses a local Blender worker (15 minute idle shutdown).
        open_blender=True saves the scene and launches desktop Blender for local editing.
        edit_bridge=True also enables local chat editing; use mcp --editor for the editor tools.
        Both desktop options require an explicit user request to open a window. For edits to an
        existing scene, discover editor_sessions and retain the selected session_id instead. Never
        render/open a replacement scene to work around a connection failure or unsupported edit.
        """
        import json
        options = {'style_doc': json.loads(Path(style_json).expanduser().read_text())} if style_json else {'preset': preset}
        options.update(rough=rough, save_blend=save_blend)
        for key, value in [('animation', animation), ('duration', duration), ('fps', fps),
                           ('rock_angle', rock_angle), ('gif', gif), ('width', width),
                           ('height', height), ('samples', samples)]:
            if value is not None:
                options[key] = value
        return render_file(input_path, output=output_path, options=options, timeout=timeout,
                           warm=warm, open_blender=open_blender, edit_bridge=edit_bridge)

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

    if editor_enabled:
        from .editor_mcp import register
        register(mcp)
    return mcp
