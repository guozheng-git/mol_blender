"""CLI for co-pymol setup, diagnostics and Blender rendering.

Subcommands:
    setup <client>     Install the PyMOL hook and configure one MCP client
    proxy              Run the stdio MCP proxy that survives PyMOL restarts
    render <pse>       Render a saved PyMOL session with Blender
    animate <pse>      Preview an animation recipe of a saved session
    inspect <pse>      Describe a saved session's content
    presets            List Blender render presets
    renders [name]     List saved Blender renders, or show one's status

The legacy ``install-hook`` and ``install-config`` commands remain accepted as
hidden compatibility aliases but are omitted from the public help.

The setup commands are pure stdlib — they do not import pymol or mcp — so
they can run under any Python interpreter, even if the plugin itself was
installed into PyMOL's bundled Python. `render` needs PyMOL's Python, since it
opens the session in a headless PyMOL.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from co_pymol.constants import DEFAULT_HOST, DEFAULT_PORT
from co_pymol.defaults import ANIMATION, IMAGE

PYMOLRC_SENTINEL = "# co-pymol: auto-start MCP server on PyMOL launch"
PYMOLRC_LINE = "from co_pymol import __init_plugin__; __init_plugin__()"


def server_url(host: str, port: int) -> str:
    """The SSE endpoint a client connects to for a server at host:port."""
    return f"http://{host}:{port}/sse"


def load_config(path: Path) -> dict:
    """Read a JSON object from `path`, or {} if it's missing or empty."""
    text = path.read_text() if path.exists() else ""
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object at the top level.")
    return data


def pymol_server_entry(host: str, port: int, use_sse: bool) -> dict:
    """The `pymol` mcpServers entry: the restart-surviving proxy, or direct SSE.

    The proxy entry launches `-m co_pymol proxy` under *this* interpreter
    (`sys.executable`) — the one co-pymol and its deps are installed in, which is
    exactly what the proxy needs. This routes through the same CLI as the
    `co-pymol proxy` command. host/port are only emitted when non-default.
    """
    if use_sse:
        return {"url": server_url(host, port)}

    args = ["-m", "co_pymol", "proxy"]
    if host != DEFAULT_HOST:
        args += ["--host", host]
    if port != DEFAULT_PORT:
        args += ["--port", str(port)]
    return {"command": sys.executable, "args": args}


def write_mcp_config(path: Path, host: str, port: int, use_sse: bool = False) -> str:
    """Merge a `pymol` entry into mcpServers, preserving other servers.

    Writes the stdio proxy entry by default (survives PyMOL restarts); `use_sse`
    writes the direct SSE url form instead.
    """
    data = load_config(path)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"'mcpServers' in {path} must be an object.")

    kind = "SSE" if use_sse else "proxy"
    desired = pymol_server_entry(host, port, use_sse)
    existing = servers.get("pymol")
    if existing == desired:
        return f"Already configured: {path} -> pymol ({kind})"

    servers["pymol"] = desired
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")

    action = "Updated" if existing is not None else "Wrote"
    return f"{action} {path} -> pymol ({kind})"


def write_pymolrc_hook(path: Path) -> str:
    """Append the plugin startup line to ~/.pymolrc.py if not already present."""
    existing = path.read_text() if path.exists() else ""
    if PYMOLRC_LINE in existing:
        return f"Already configured: {path}"

    prefix = "" if not existing else "\n" if existing.endswith("\n") else "\n\n"
    snippet = f"{prefix}{PYMOLRC_SENTINEL}\n{PYMOLRC_LINE}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(snippet)

    action = "Appended to" if existing else "Wrote"
    return f"{action} {path}. Restart PyMOL to load the plugin."


def cmd_install_hook(args: argparse.Namespace) -> None:
    print(write_pymolrc_hook(Path.home() / ".pymolrc.py"))


def install_codex_mcp(host: str, port: int) -> str:
    """Register the restart-surviving proxy as Codex's global `pymol` MCP.

    Codex owns its TOML configuration format, so use its supported CLI instead
    of editing ``~/.codex/config.toml`` directly. ``codex mcp add`` is an upsert,
    which makes this safe to re-run when the interpreter, host, or port changes.
    """
    codex = shutil.which("codex")
    if codex is None:
        raise OSError(
            "Codex CLI was not found on PATH. Install Codex first, then re-run "
            "`co-pymol setup codex`."
        )

    entry = pymol_server_entry(host, port, use_sse=False)
    command = [
        codex,
        "mcp",
        "add",
        "pymol",
        "--",
        entry["command"],
        *entry["args"],
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise OSError(f"`codex mcp add` failed{suffix}")

    return result.stdout.strip() or "Configured Codex MCP server 'pymol'."


def install_claude_mcp(host: str, port: int) -> str:
    """Register the restart-surviving proxy as Claude Code's user MCP."""
    claude = shutil.which("claude")
    if claude is None:
        raise OSError(
            "Claude Code CLI was not found on PATH. Install Claude Code first, "
            "then re-run `co-pymol setup claude`."
        )

    entry = pymol_server_entry(host, port, use_sse=False)
    command = [
        claude,
        "mcp",
        "add",
        "--scope",
        "user",
        "pymol",
        "--",
        entry["command"],
        *entry["args"],
    ]
    result = subprocess.run(command, capture_output=True, text=True)

    # Unlike `codex mcp add`, Claude Code does not update an existing entry.
    # Remove only its user-scoped `pymol` entry and retry; other clients and
    # other Claude MCP servers are untouched.
    detail = (result.stderr or result.stdout).strip()
    if result.returncode != 0 and "already exists" in detail.lower():
        remove = subprocess.run(
            [claude, "mcp", "remove", "pymol", "--scope", "user"],
            capture_output=True,
            text=True,
        )
        if remove.returncode != 0:
            remove_detail = (remove.stderr or remove.stdout).strip()
            suffix = f": {remove_detail}" if remove_detail else ""
            raise OSError(f"`claude mcp remove` failed{suffix}")
        result = subprocess.run(command, capture_output=True, text=True)
        detail = (result.stderr or result.stdout).strip()

    if result.returncode != 0:
        suffix = f": {detail}" if detail else ""
        raise OSError(f"`claude mcp add` failed{suffix}")

    return result.stdout.strip() or "Configured Claude Code MCP server 'pymol'."


def install_cursor_mcp(host: str, port: int) -> str:
    """Register the restart-surviving proxy in Cursor's global MCP config."""
    return write_mcp_config(
        Path.home() / ".cursor" / "mcp.json", host, port, use_sse=False
    )


def cmd_setup(args: argparse.Namespace) -> None:
    """Install the hook and MCP configuration for the chosen client."""
    if args.client == "codex":
        # Preflight before changing ~/.pymolrc.py so a missing selected client
        # fails without leaving a partial setup. Other clients are not checked.
        if shutil.which("codex") is None:
            raise OSError(
                "Codex CLI was not found on PATH. Install Codex first, then "
                "re-run `co-pymol setup codex`."
            )
        configure = install_codex_mcp
        restart = "Start a new Codex session"
    elif args.client == "claude":
        if shutil.which("claude") is None:
            raise OSError(
                "Claude Code CLI was not found on PATH. Install Claude Code "
                "first, then re-run `co-pymol setup claude`."
            )
        configure = install_claude_mcp
        restart = "Start a new Claude Code session"
    else:
        configure = install_cursor_mcp
        restart = "Fully quit and reopen Cursor"

    print(write_pymolrc_hook(Path.home() / ".pymolrc.py"))
    print(configure(args.host, args.port))
    print(f"Restart PyMOL. {restart} to load the pymol tools.")


def cmd_proxy(args: argparse.Namespace) -> int:
    # Deferred import: proxy.py pulls in mcp/anyio, which (like pymol) only exist
    # where the package's deps are installed. Importing it lazily here keeps the
    # setup/install helpers runnable under a stdlib-only Python that just has
    # the package source on its path.
    from co_pymol.proxy import run_proxy

    return run_proxy(args.host, args.port)


DEFAULT_PRESET = IMAGE["preset"]

# Statuses after which a render's background helper has stopped working.
FINISHED = {"completed", "failed", "cancelled", "needs_attention"}


def blender_bridge(args: argparse.Namespace):
    # Deferred import, like proxy: keeps setup runnable without the deps.
    from co_pymol.core.blender import BlenderBridge

    bridge = BlenderBridge()
    if args.config:
        bridge.configure(args.config)
    return bridge


def wait_for_render(bridge, request_id: str, interval: float = 5) -> dict:
    """Poll saved local state only; the detached helper does the remote work."""
    while True:
        state = bridge.status(request_id)
        if state["status"] in FINISHED:
            return state
        print(f"{state['name']}: {state['status']}", file=sys.stderr)
        time.sleep(interval)


def cmd_render(args: argparse.Namespace) -> int:
    from co_pymol.utils.pymol.blender import capture_scene, open_session

    session = saved_session_path(args.session)
    bridge = blender_bridge(args)
    default = None if args.all_presets else [DEFAULT_PRESET]
    presets = bridge.select_presets(args.preset or default, args.all_presets)
    pse = session.read_bytes()
    with open_session(session) as cmd:
        payload = capture_scene(cmd, args.selection, args.state)
    options = dict(
        width=args.width,
        height=args.height,
        samples=args.samples,
        keep_colors=not args.preset_colors,
        save_blend=args.save_blend,
        pse=pse,
        name=args.name,
        protein=args.protein,
        view=args.view,
        backend=getattr(args, "backend", None),
    )
    if len(presets) == 1:
        receipt = bridge.submit(payload, preset=presets[0], **options)
    else:
        receipt = bridge.submit_batch(payload, presets=presets, **options)
    if args.wait:
        receipt = bridge.presentation(wait_for_render(bridge, receipt["request_id"]))
    elif len(presets) == 1:
        receipt = bridge.presentation(receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] not in {"failed", "needs_attention"} else 1


def saved_session_path(path: str) -> Path:
    session = Path(path).expanduser()
    if session.suffix.lower() != ".pse" or not session.is_file():
        raise ValueError(f"{session} is not a saved PyMOL session (.pse)")
    return session


def open_saved_session(path: str):
    from co_pymol.utils.pymol.blender import open_session

    return open_session(saved_session_path(path))


def cmd_inspect(args: argparse.Namespace) -> None:
    from co_pymol.utils.pymol.animate import scene_facts

    with open_saved_session(args.session) as cmd:
        print(json.dumps(scene_facts(cmd), indent=2))


CUTAWAY_PRESET = IMAGE["cutaway_preset"]


def submit_blender_animation(args: argparse.Namespace, cmd, recipe: str, frames) -> int:
    """Submit one Blender animation job of a recipe to the configured GPU profile."""
    from co_pymol.utils.pymol.blender import capture_animation

    bridge = blender_bridge(args)
    default = CUTAWAY_PRESET if recipe == "cutaway" else DEFAULT_PRESET
    preset = bridge.select_presets([args.preset or default], False)[0]
    payload, animation = capture_animation(cmd, recipe, frames, args.fps, gif=args.gif)
    receipt = bridge.submit(
        payload,
        preset=preset,
        width=args.width,
        height=args.height,
        samples=args.samples,
        # A cutaway shows its preset as designed: pink ligand and
        # element-coloured pocket residues, not the session's colours.
        keep_colors=recipe != "cutaway",
        save_blend=False,
        pse=Path(args.session).expanduser().read_bytes(),
        name=args.name,
        animation=animation,
        backend=args.backend,
    )
    if args.wait:
        receipt = wait_for_render(bridge, receipt["request_id"])
    receipt = bridge.presentation(receipt)
    print(json.dumps({"recipe": recipe, "frames": len(frames), **receipt}, indent=2))
    return 0 if receipt["status"] not in {"failed", "needs_attention"} else 1


def cmd_animate(args: argparse.Namespace) -> int | None:
    from co_pymol.core.animation import choose_recipe, frames_for
    from co_pymol.utils.pymol.animate import (
        build_cutaway,
        keys_for,
        render_preview,
        scene_facts,
    )

    with open_saved_session(args.session) as cmd:
        facts = scene_facts(cmd)
        focus = args.focus or (
            facts["ligands"][0]["selection"] if facts["ligands"] else None
        )
        if args.focus and not cmd.count_atoms(f"enabled and ({args.focus})"):
            raise ValueError(f"--focus {args.focus!r} matches no enabled atoms")
        recipe = args.recipe or choose_recipe(
            scenes=len(facts["scenes"]),
            states=facts["states"],
            has_focus=focus is not None,
        )
        keys = keys_for(cmd, recipe, focus)
        frames = frames_for(recipe, keys, args.fps, duration=args.duration, rock_angle=args.rock_angle)
        if args.blender:
            return submit_blender_animation(args, cmd, recipe, frames)
        shell = None
        if focus and recipe in {"reveal", "cutaway"}:
            cmd.show("sticks", focus)
        if recipe == "cutaway":
            shell = build_cutaway(cmd, focus, keys.focus)
        width = args.width
        viewport = facts["viewport"]
        height = args.height or round(width * viewport[1] / viewport[0])
        output = Path(args.out or Path(args.session).with_suffix(f".{recipe}.mp4"))
        render_preview(
            cmd, frames, output, fps=args.fps, width=width, height=height, shell=shell
        )
    print(
        json.dumps(
            {
                "recipe": recipe,
                "focus": focus,
                "frames": len(frames),
                "seconds": round(len(frames) / args.fps, 2),
                "preview": str(output.absolute()),
            },
            indent=2,
        )
    )


def cmd_presets(args: argparse.Namespace) -> None:
    print(json.dumps(blender_bridge(args).presets(), indent=2))


def cmd_renders(args: argparse.Namespace) -> None:
    bridge = blender_bridge(args)
    if args.name:
        print(json.dumps(bridge.presentation(bridge.status(args.name)), indent=2))
    else:
        print(json.dumps(bridge.recent(args.limit), indent=2))


def cmd_install_config(args: argparse.Namespace) -> None:
    if args.project:
        target = Path(args.project_dir).resolve() / ".cursor" / "mcp.json"
    else:
        target = Path.home() / ".cursor" / "mcp.json"

    print(write_mcp_config(target, args.host, args.port, use_sse=args.sse))
    if not args.project:
        print("Restart Cursor to pick up the change.")


def add_server_opts(parser: argparse.ArgumentParser) -> None:
    """Add client-side options locating an existing co-pymol SSE server."""
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            "SSE host the configured client or proxy connects to; does not "
            "change PyMOL's "
            f"automatic bind (default: {DEFAULT_HOST})"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=(
            "SSE port the configured client or proxy connects to; does not "
            "change PyMOL's "
            f"automatic bind (default: {DEFAULT_PORT})"
        ),
    )


def add_blender_opts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        help=(
            "Blender backend configuration JSON (default: $CO_PYMOL_BLENDER_CONFIG "
            "or ~/.config/co-pymol/blender.json)"
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("local", "lab", "cluster"),
        help="Render on this computer or the lab GPU (default: the configured backend)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="co-pymol",
        description="co-pymol setup, diagnostics and Blender rendering",
    )
    sub = parser.add_subparsers(
        dest="cmd",
        required=True,
        metavar="{setup,proxy,render,animate,inspect,presets,renders}",
    )

    p_setup = sub.add_parser(
        "setup",
        help="Install the PyMOL hook and configure one MCP client",
        description=(
            "Set up co-pymol for exactly one client. This installs the shared "
            "PyMOL startup hook and configures the selected client's MCP server. "
            "Other clients do not need to be installed. "
            "No remote login or GPU job is started."
        ),
    )
    setup_clients = p_setup.add_subparsers(dest="client", required=True)
    for client, label in (
        ("codex", "Codex"),
        ("cursor", "Cursor"),
        ("claude", "Claude Code"),
    ):
        p_client = setup_clients.add_parser(
            client,
            help=f"Install the PyMOL hook and configure {label}",
        )
        add_server_opts(p_client)
        p_client.set_defaults(func=cmd_setup)

    p_proxy = sub.add_parser(
        "proxy",
        help="Run the stdio MCP proxy that survives PyMOL restarts",
        description=(
            "Run a stdio MCP proxy in the foreground. An MCP client launches this "
            "as a subprocess; it forwards to the co-pymol SSE server "
            "in PyMOL and survives PyMOL quitting/restarting so the client's "
            "connection never drops. Configure the client's stdio MCP entry to "
            "run `co-pymol proxy`."
        ),
    )
    add_server_opts(p_proxy)
    p_proxy.set_defaults(func=cmd_proxy)

    p_render = sub.add_parser(
        "render",
        help="Render a saved PyMOL session with Blender",
        description=(
            "Open a saved PyMOL session headlessly, export its enabled objects "
            "and camera, and submit a Blender render through the configured "
            "backend. Returns once submitted unless --wait is given; the render "
            "continues in the background either way. Only render sessions you "
            "trust: PSE files can run code when opened."
        ),
    )
    p_render.add_argument("session", help="Saved PyMOL session (.pse)")
    p_render.add_argument(
        "--preset",
        action="append",
        help=f"Preset ID or alias; repeat for several (default: {DEFAULT_PRESET})",
    )
    p_render.add_argument(
        "--all-presets", action="store_true", help="Render every available preset"
    )
    p_render.add_argument(
        "--selection", default="all", help="Atoms to export (default: all)"
    )
    p_render.add_argument(
        "--state", type=int, default=-1, help="Object state (default: current)"
    )
    p_render.add_argument("--width", type=int, default=IMAGE["preview"]["width"], help="Pixels wide")
    p_render.add_argument(
        "--height",
        type=int,
        default=IMAGE["preview"]["height"],
        help="Pixels high; 0 follows the session's viewport aspect ratio",
    )
    p_render.add_argument("--samples", type=int, default=IMAGE["preview"]["samples"], help="Cycles samples")
    p_render.add_argument(
        "--preset-colors",
        action="store_true",
        help="Use the preset palette instead of the session's colours",
    )
    p_render.add_argument(
        "--save-blend", action="store_true", help="Also keep the Blender file"
    )
    p_render.add_argument("--name", help="Output folder name (default: random)")
    p_render.add_argument("--protein", help="Protein folder (project_directory)")
    p_render.add_argument("--view", help="View folder (project_directory)")
    p_render.add_argument(
        "--wait",
        action="store_true",
        help="Wait for the render to finish and report its output paths",
    )
    add_blender_opts(p_render)
    p_render.set_defaults(func=cmd_render)

    from co_pymol.core.animation import RECIPES

    p_animate = sub.add_parser(
        "animate",
        help="Preview an animation recipe of a saved PyMOL session",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Render a quick ray-traced PyMOL preview (MP4) of an animation "
            "recipe, to check the motion before a full render. Needs ffmpeg. "
            "Only open sessions you trust.\n\nrecipes:\n"
            + "\n".join(f"  {name:10} {text}" for name, (_, text) in RECIPES.items())
            + "\n\nWithout --recipe: scenes if two or more are saved, else states "
            "if there are several, else reveal if there is a ligand, else "
            "turntable."
        ),
    )
    p_animate.add_argument("session", help="Saved PyMOL session (.pse)")
    p_animate.add_argument("--recipe", choices=list(RECIPES))
    p_animate.add_argument(
        "--focus",
        help="Selection to reveal (default: the largest ligand)",
    )
    p_animate.add_argument("--out", help="Output MP4 (default: <session>.<recipe>.mp4)")
    p_animate.add_argument("--width", type=int, default=ANIMATION["preview"]["width"], help="Pixels wide")
    p_animate.add_argument(
        "--height",
        type=int,
        default=ANIMATION["preview"]["height"],
        help="Pixels high; 0 follows the session's viewport aspect ratio",
    )
    p_animate.add_argument("--fps", type=int, default=ANIMATION["fps"], help="Frames per second")
    p_animate.add_argument("--duration", type=float, help="Scale the recipe to this many seconds")
    p_animate.add_argument("--rock-angle", type=float, help="Rock recipe angle in degrees")
    p_animate.add_argument("--gif", action=argparse.BooleanOptionalAction, default=ANIMATION["gif"],
                           help="Also encode a GIF with --blender")
    p_animate.add_argument(
        "--blender",
        action="store_true",
        help=(
            "Render with Blender on the configured GPU profile instead of a "
            "PyMOL preview (every recipe)"
        ),
    )
    p_animate.add_argument(
        "--preset",
        help=(
            f"Blender preset with --blender (default: {DEFAULT_PRESET}; "
            f"{CUTAWAY_PRESET} for cutaway)"
        ),
    )
    p_animate.add_argument(
        "--samples", type=int, default=ANIMATION["preview"]["samples"], help="Cycles samples with --blender"
    )
    p_animate.add_argument("--name", help="Output folder name with --blender")
    p_animate.add_argument(
        "--wait",
        action="store_true",
        help="With --blender, wait for the render and report its files",
    )
    add_blender_opts(p_animate)
    p_animate.set_defaults(func=cmd_animate)

    p_inspect = sub.add_parser(
        "inspect",
        help="Describe a saved PyMOL session: objects, ligands, states, scenes",
    )
    p_inspect.add_argument("session", help="Saved PyMOL session (.pse)")
    p_inspect.set_defaults(func=cmd_inspect)

    p_presets = sub.add_parser("presets", help="List Blender render presets")
    add_blender_opts(p_presets)
    p_presets.set_defaults(func=cmd_presets)

    p_renders = sub.add_parser(
        "renders",
        help="List saved Blender renders, or show one's status",
        description=(
            "Read saved local render records. Never contacts the render service."
        ),
    )
    p_renders.add_argument("name", nargs="?", help="Render name or request ID")
    p_renders.add_argument(
        "--limit", type=int, default=20, help="How many to list (default: 20)"
    )
    add_blender_opts(p_renders)
    p_renders.set_defaults(func=cmd_renders)

    return parser


def build_legacy_parser(command: str) -> argparse.ArgumentParser:
    """Build a parser for a pre-setup compatibility command.

    These commands remain callable by old scripts but stay out of the public
    parser, so ``co-pymol --help`` presents only the supported setup interface.
    """
    parser = argparse.ArgumentParser(prog=f"co-pymol {command}")
    if command == "install-hook":
        parser.description = (
            "Compatibility command: append the PyMOL startup hook. New installs "
            "should use `co-pymol setup <client>`."
        )
        parser.set_defaults(func=cmd_install_hook)
        return parser

    if command != "install-config":  # defensive; main filters this already
        raise ValueError(f"Unknown compatibility command: {command}")

    parser.description = (
        "Compatibility command: write Cursor MCP configuration. New installs "
        "should use `co-pymol setup cursor`."
    )
    parser.add_argument(
        "--project",
        action="store_true",
        help="Write project-level config (./.cursor/mcp.json) instead of global",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Project root for --project (default: current directory)",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Write the direct SSE url entry instead of the restart-surviving proxy",
    )
    add_server_opts(parser)
    parser.set_defaults(func=cmd_install_config)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] in {"install-hook", "install-config"}:
        command = raw_argv.pop(0)
        args = build_legacy_parser(command).parse_args(raw_argv)
    else:
        args = build_parser().parse_args(raw_argv)
    try:
        # Subcommands return an exit code (proxy, render) or None (the rest).
        return args.func(args) or 0
    except (OSError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
