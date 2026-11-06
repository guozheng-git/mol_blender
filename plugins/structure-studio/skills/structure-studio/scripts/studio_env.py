"""Install and locate the PyMOL and Blender (bpy module) runtimes used by the skill.

Everything lives under STRUCTURE_STUDIO_HOME (default ~/.cache/structure-studio):
  pymol/            Python 3.11 venv with open-source PyMOL (pymol2)
  render/           Python 3.11 venv with bpy 5.0.1, Molecular Nodes 4.5.10 and the renderer
  bin/blender       command-line shim that runs Blender scripts through the bpy module
  blender-render/   renderer configuration (BLENDER_RENDER_HOME)
  state.json        what was installed, for fast re-use
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
WHEELS = SKILL_ROOT / "assets" / "wheels"
PYTHON = "3.11.15"  # Rebuilt with uv on each OS; never copy a venv between platforms.
REQUIREMENTS = SKILL_ROOT / 'references'
# Shared libraries the manylinux bpy / PyMOL wheels expect from the host, by Debian package.
LINUX_LIBRARIES = {
    "libXrender.so.1": "libxrender1", "libXxf86vm.so.1": "libxxf86vm1", "libXfixes.so.3": "libxfixes3",
    "libXi.so.6": "libxi6", "libxkbcommon.so.0": "libxkbcommon0", "libSM.so.6": "libsm6", "libICE.so.6": "libice6",
    "libGL.so.1": "libgl1", "libEGL.so.1": "libegl1", "libX11.so.6": "libx11-6", "libXext.so.6": "libxext6",
    "libGLU.so.1": "libglu1-mesa", "libgomp.so.1": "libgomp1", "libXinerama.so.1": "libxinerama1",
    "libXcursor.so.1": "libxcursor1", "libXrandr.so.2": "libxrandr2", "libwayland-client.so.0": "libwayland-client0",
    "libwayland-cursor.so.0": "libwayland-cursor0", "libwayland-egl.so.1": "libwayland-egl1",
    "libdecor-0.so.0": "libdecor-0-0", "libfreetype.so.6": "libfreetype6", "libpng16.so.16": "libpng16-16",
    "libglut.so.3": "freeglut3", "libxml2.so.2": "libxml2", "libOpenGL.so.0": "libopengl0",
    "libGLX.so.0": "libglx0", "libglib-2.0.so.0": "libglib2.0-0",
}
SHIM = '''#!{python}
"""Blender command-line shim over the bpy module: runs `--python SCRIPT -- ARGS` headless."""
import runpy
import sys

argv = sys.argv[1:]
if "--version" in argv:
    import bpy
    print("Blender " + bpy.app.version_string)
    raise SystemExit(0)
if "--python" not in argv:
    raise SystemExit("This Blender shim only supports --python SCRIPT")
script = argv[argv.index("--python") + 1]
tail = argv[argv.index("--"):] if "--" in argv else []
import bpy  # noqa: F401  (initializes Blender in this process)
sys.argv = [sys.argv[0], *tail]
runpy.run_path(script, run_name="__main__")
'''


class SetupError(RuntimeError):
    pass


def home() -> Path:
    return Path(os.environ.get("STRUCTURE_STUDIO_HOME", "~/.cache/structure-studio")).expanduser()


def _bin(venv: Path, name: str) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else ""))


def _log(message: str) -> None:
    print(f"[structure-studio] {message}", file=sys.stderr, flush=True)


def _run(command: list[str], *, env: dict | None = None, timeout: float = 1800, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([str(c) for c in command], capture_output=True, text=True, env=env, timeout=timeout)
    if check and result.returncode:
        raise SetupError(f"Command failed ({result.returncode}): {' '.join(map(str, command))}\n{(result.stderr or result.stdout)[-4000:]}")
    return result


def wheel() -> Path:
    found = sorted(WHEELS.glob("*.whl"))
    if not found:
        raise SetupError('Use the staged plugin produced by scripts/build_release.py; source checkouts do not contain generated wheels.')
    return found[-1]


def state() -> dict:
    path = home() / "state.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def _save_state(update: dict) -> dict:
    data = state()
    data.update(update)
    path = home() / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return data


def _uv_env() -> dict:
    env = dict(os.environ)
    env['UV_PYTHON_INSTALL_DIR'] = str(home() / 'python')
    env['UV_CACHE_DIR'] = str(home() / 'uv-cache')
    env.setdefault("UV_HTTP_TIMEOUT", "300")
    return env


def uv() -> Path:
    """Require explicit uv installation; never bootstrap into a user/system Python."""
    found = shutil.which("uv")
    if found:
        return Path(found)
    raise SetupError('Install uv explicitly and put it on PATH, then rerun setup. No pip fallback is used.')


def _check_platform() -> None:
    machine, system = platform.machine().lower(), platform.system()
    if system == "Linux" and machine not in {"x86_64", "amd64"}:
        raise SetupError(f"Blender 5.0.1's bpy wheel exists only for Linux x86_64, not {machine}.")
    if system == "Darwin" and machine != "arm64":
        raise SetupError("Blender 5.0.1's bpy wheel exists only for Apple Silicon Macs.")


def _venv(path: Path, interpreter: str = PYTHON) -> Path:
    # Never follow an environment-directory symlink into an unrelated install.
    if path.is_symlink() or not path.resolve().is_relative_to(home().resolve()):
        raise SetupError('Managed environments must stay inside STRUCTURE_STUDIO_HOME')
    if path.exists():
        cfg = path / 'pyvenv.cfg'
        if not cfg.is_file() or 'include-system-site-packages = false' not in cfg.read_text().lower():
            raise SetupError('Refusing to modify a non-isolated environment; choose a fresh studio home')
    python = _bin(path, "python")
    if not python.is_file():
        _log(f"Creating a Python environment at {path} (uv downloads Python {interpreter} if needed)…")
        _run([uv(), "venv", "--quiet", "--managed-python", "-p", interpreter, str(path)], env=_uv_env(), timeout=900)
    version = _run([python, '-c', "import sys; print('.'.join(map(str, sys.version_info[:3])))"]).stdout.strip()
    if version != interpreter:
        raise SetupError(f'Expected Python {interpreter}, found {version}; choose a fresh STRUCTURE_STUDIO_HOME')
    return python


def _pymol_interpreter() -> str:
    return PYTHON


# ---------------------------------------------------------------- background setup

def _lock() -> Path:
    return home() / "setup.lock"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_setup() -> dict | None:
    """The setup currently installing in the background, if any."""
    try:
        info = json.loads(_lock().read_text())
    except (OSError, ValueError):
        return None
    return info if _alive(int(info.get("pid", 0))) else None


def _wait_until(ready, what: str, timeout: float = 2400) -> None:
    """Wait for a background setup to provide something; returns as soon as it is ready."""
    started, last = time.time(), 0.0
    while not ready():
        info = running_setup()
        if not info or info.get("pid") == os.getpid():
            return
        if time.time() - started > timeout:
            raise SetupError(f"Timed out waiting for the background setup ({info.get('log')})")
        if time.time() - last > 30:
            _log(f"Waiting for the background setup to finish installing {what} (log: {info.get('log')})…")
            last = time.time()
        time.sleep(3)


def _import_check(python: Path, code: str, label: str) -> None:
    """Read-only probe; report missing libraries without running apt or sudo."""
    result = _run([python, '-c', code], check=False, timeout=900)
    if result.returncode == 0:
        return
    output = result.stderr + result.stdout
    missing = re.findall(r'(lib[\w.+-]+\.so[\w.]*): cannot open shared object file', output)
    packages = sorted({LINUX_LIBRARIES.get(name, name) for name in missing})
    hint = ' Ask the system administrator to provide: ' + ', '.join(packages) if packages else ''
    raise SetupError(f'{label} failed to import.{hint}\n{output[-3000:]}')


def setup_pymol() -> Path:
    venv = home() / "pymol"
    python = _venv(venv, _pymol_interpreter())
    _run([uv(), 'pip', 'sync', '--require-hashes', '--only-binary=:all:', '-p', str(python),
          str(REQUIREMENTS / 'pymol-requirements.txt')], env=_uv_env(), timeout=900)
    _import_check(python, "import pymol2\nwith pymol2.PyMOL() as p: p.cmd.fab('AG', 'probe'); assert p.cmd.get_coords('probe').shape[1] == 3", "PyMOL")
    _save_state({"pymol_python": str(python)})
    return python


def _site_packages(python: Path) -> Path:
    out = _run([python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]).stdout.strip()
    return Path(out)


def setup_render(pymol_python: Path) -> dict:
    _check_platform()
    venv = home() / "render"
    python = _venv(venv)
    renderer = wheel()
    digest = hashlib.sha256(renderer.read_bytes()).hexdigest()
    _run([uv(), 'pip', 'sync', '--require-hashes', '--only-binary=:all:', '-p', str(python),
          str(REQUIREMENTS / 'render-requirements.txt')], env=_uv_env(), timeout=1800)
    _run([uv(), 'pip', 'install', '--no-deps', '-p', str(python), str(renderer)], env=_uv_env(), timeout=900)
    _import_check(python, "import bpy, molecularnodes, blender_render", "Blender (bpy)")
    shim = home() / "bin" / "blender"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(SHIM.format(python=python))
    shim.chmod(0o755)
    site = _site_packages(python)
    config_home = home() / "blender-render"
    env = dict(os.environ, BLENDER_RENDER_HOME=str(config_home))
    _run([_bin(venv, "blender-render"), "setup", "--blender", str(shim), "--pymol-python", str(pymol_python),
          "--runtime-path", str(site), "--ccd", str(site / "biotite/structure/info/components.bcif")], env=env)
    return _save_state({"render_python": str(python), "blender_render": str(_bin(venv, "blender-render")),
                        "blender_render_home": str(config_home), "blender_shim": str(shim), "wheel_sha256": digest,
                        "platform": f"{platform.system()} {platform.machine()}", "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S")})


def use_existing(executable: str, config_home: str | None) -> dict:
    """Use an already configured blender-render installation (for example a local checkout)."""
    path = Path(executable).expanduser()
    if not path.is_file():
        raise SetupError(f"{path} does not exist")
    return _save_state({"blender_render": str(path.absolute()), "blender_render_home": config_home or "",
                        "external_renderer": True})


def setup(components: str = "all", background: bool = False) -> dict:
    if background:
        info = running_setup()
        if info:
            return {**doctor(), "background": True, "already_running": True}
        home().mkdir(parents=True, exist_ok=True)
        log = home() / "setup.log"
        with log.open("w") as handle:
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("studio.py")), "setup", "--only", components],
                                       stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        _lock().write_text(json.dumps({"pid": process.pid, "components": components, "log": str(log),
                                       "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
        return {**doctor(), "background": True, "pid": process.pid, "log": str(log)}
    other = running_setup()
    if other and other.get("pid") != os.getpid():
        _wait_until(lambda: running_setup() is None, "everything")
    started = time.time()
    lock = _lock()
    home().mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "components": components, "log": str(home() / "setup.log"),
                                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
    try:
        interpreter = setup_pymol() if components in {"pymol", "all"} else None
        if components in {"render", "all"}:
            current = state()
            if current.get("external_renderer"):
                _log("Using the configured external renderer: " + current["blender_render"])
            else:
                setup_render(interpreter or setup_pymol())
    finally:
        try:
            if json.loads(lock.read_text()).get("pid") == os.getpid():
                lock.unlink()
        except (OSError, ValueError):
            pass
    return {**doctor(), "seconds": round(time.time() - started, 1)}


def doctor() -> dict:
    current = state()
    gpu = platform.system() == "Darwin" or bool(shutil.which("nvidia-smi"))
    running = running_setup()
    installed, bundled = installed_identity(), bundled_identity()
    update = ("none" if _render_ready() else
              "automatic" if _render_installed() and not current.get("external_renderer") and bundled else "setup")
    return {"home": str(home()), "pymol_ready": _pymol_ready(), "render_ready": _render_ready(),
            "render_installed": _render_installed(), "renderer_update": update,
            "renderer_identity": installed, "bundled_renderer_identity": bundled,
            "setup_running": bool(running), "setup_log": (running or {}).get("log", ""), "gpu_likely": gpu, "state": current}


def _pymol_ready() -> bool:
    current = state()
    return bool(current.get("pymol_python")) and Path(current["pymol_python"]).is_file()


def _identity(manifest: bytes) -> str | None:
    try:
        return json.loads(manifest)["engine_build"]
    except (ValueError, KeyError, TypeError):
        return None


def bundled_identity() -> str | None:
    """Build identity of the renderer wheel shipped in this plugin (its code, not its zip bytes)."""
    import zipfile
    try:
        with zipfile.ZipFile(wheel()) as archive:
            return _identity(archive.read("blender_render/engine-manifest.json"))
    except (SetupError, OSError, KeyError, zipfile.BadZipFile):
        return None


def installed_identity() -> str | None:
    """Build identity of the renderer installed in Studio's render environment."""
    python = state().get("render_python")
    if not python:
        return None
    venv = Path(python).parent.parent
    found = sorted(venv.glob("lib/python*/site-packages/blender_render/engine-manifest.json")) + \
        sorted(venv.glob("Lib/site-packages/blender_render/engine-manifest.json"))
    return _identity(found[0].read_bytes()) if found else None


def _render_installed() -> bool:
    current = state()
    return bool(current.get("blender_render")) and Path(current["blender_render"]).is_file()


def _render_ready() -> bool:
    if not _render_installed():
        return False
    if state().get("external_renderer"):
        return True
    bundled = bundled_identity()
    # Rebuilding a wheel changes its bytes but not its code: compare the renderer build identity.
    return bundled is not None and installed_identity() == bundled


def _pins(text: str) -> dict:
    """name==version pins from a hash-locked requirements file."""
    pins = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line.strip())
        if match:
            pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return pins


def _dependencies_match(python: Path) -> bool:
    """The render environment already has every pinned dependency at its pinned version."""
    pinned = _pins((REQUIREMENTS / "render-requirements.txt").read_text())
    frozen = _pins(_run([uv(), "pip", "freeze", "-p", str(python)], env=_uv_env(), timeout=120).stdout)
    return all(frozen.get(name) == version for name, version in pinned.items())


def refresh_renderer() -> dict:
    """Swap in this plugin's renderer wheel after a plugin update. Local and offline: no download.

    Only the renderer changes. If the update also changed Blender's pinned dependencies,
    stop and say so: that needs a real setup (a download), which the user approves.
    """
    current = state()
    python = Path(current["render_python"])
    if not _dependencies_match(python):
        raise SetupError("This plugin update changes Blender's pinned dependencies, so it needs a one-time download. "
                         "Run: studio.py setup --background")
    renderer = wheel()
    _log("Updating the renderer from the installed plugin (offline, a few seconds)…")
    _run([uv(), "pip", "install", "--offline", "--no-deps", "--reinstall", "-p", str(python), str(renderer)],
         env=_uv_env(), timeout=300)
    _import_check(python, "import blender_render", "the renderer")
    if installed_identity() != bundled_identity():
        raise SetupError("The renderer update did not take effect; run: studio.py setup")
    return _save_state({"wheel_sha256": hashlib.sha256(renderer.read_bytes()).hexdigest()})


def _refresh_once() -> None:
    """Refresh under the setup lock, so two renders started together update it only once."""
    other = running_setup()
    if other and other.get("pid") != os.getpid():
        _wait_until(lambda: running_setup() is None, "the renderer")
        if _render_ready():
            return
    lock = _lock()
    home().mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "components": "renderer-refresh", "log": "",
                                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
    try:
        refresh_renderer()
    finally:
        try:
            if json.loads(lock.read_text()).get("pid") == os.getpid():
                lock.unlink()
        except (OSError, ValueError):
            pass


def renderer_env() -> tuple[Path, dict]:
    _wait_until(_render_ready, "Blender")
    current = state()
    if not _render_installed():
        raise SetupError("Blender is not installed yet. Run: studio.py setup --background")
    if not current.get("external_renderer") and not _render_ready():
        _refresh_once()  # Never run a renderer older than this plugin's.
        current = state()
    env = dict(os.environ)
    if current.get("blender_render_home"):
        env["BLENDER_RENDER_HOME"] = current["blender_render_home"]
    return Path(current["blender_render"]), env


def shared_cluster_home() -> Path:
    """Where the terminal's `blender-render cluster` keeps lab profiles (its default folder)."""
    return Path.home() / ".config" / "blender-render"


def cluster_env() -> tuple[Path, dict]:
    """The installed blender-render with the terminal's config folder, for `cluster` commands only.

    Local renders keep Studio's own BLENDER_RENDER_HOME. Cluster commands drop it, so a
    lab profile set up in Studio or in a terminal is the same profile. A BLENDER_RENDER_HOME
    inherited from a shell (for example a checkout's .runtime) is dropped too, so the
    folder never depends on how the app was launched.
    """
    executable, env = renderer_env()
    env.pop("BLENDER_RENDER_HOME", None)
    return executable, env


def cluster_profiles() -> list[str]:
    folder = shared_cluster_home() / "clusters"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def pymol_python() -> Path:
    _wait_until(_pymol_ready, "PyMOL")
    current = state()
    if current.get("pymol_python") and Path(current["pymol_python"]).is_file():
        return Path(current["pymol_python"])
    raise SetupError("PyMOL is not installed yet. Run: studio.py setup --only pymol")
