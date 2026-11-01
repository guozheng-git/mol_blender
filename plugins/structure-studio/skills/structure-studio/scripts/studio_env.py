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
import tempfile
import time
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
WHEELS = SKILL_ROOT / "assets" / "wheels"
BPY = "bpy==5.0.1"
PYMOL = "pymol-open-source-whl==3.2.0.2"
PYTHON = "3.11"  # bpy 5.0.1 exists only for CPython 3.11
PYMOL_PYTHONS = ((3, 10), (3, 14))  # pymol-open-source-whl ships wheels for these
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
COMMON_LINUX = ["libxrender1", "libxxf86vm1", "libxfixes3", "libxi6", "libxkbcommon0", "libsm6", "libice6",
                "libgl1", "libegl1", "libglu1-mesa", "libgomp1", "libx11-6", "libxext6"]
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
        raise SetupError(f"No renderer wheel in {WHEELS}; rebuild the plugin with scripts/build_plugin.py")
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
    env.setdefault("UV_PYTHON_INSTALL_DIR", str(home() / "python"))
    env.setdefault("UV_CACHE_DIR", str(home() / "uv-cache"))
    env.setdefault("UV_HTTP_TIMEOUT", "300")
    return env


def uv() -> Path:
    """An uv executable, installed into the studio home with pip when missing."""
    found = shutil.which("uv")
    if found:
        return Path(found)
    target = home() / "uv-tool"
    executable = target / "bin" / "uv"
    if executable.is_file():
        return executable
    _log("Installing uv (Python package manager)…")
    attempts = [[sys.executable, "-m", "pip", "install", "--quiet", "--target", str(target), "uv"],
                [sys.executable, "-m", "pip", "install", "--quiet", "--user", "--break-system-packages", "uv"]]
    errors = []
    for command in attempts:
        result = _run(command, check=False, timeout=600)
        if executable.is_file():
            return executable
        if shutil.which("uv"):
            return Path(shutil.which("uv"))
        user_uv = Path.home() / ".local" / "bin" / "uv"
        if user_uv.is_file():
            return user_uv
        errors.append((result.stderr or result.stdout)[-800:])
    raise SetupError("Could not install uv with pip. Is PyPI reachable from this environment?\n" + "\n".join(errors))


def _check_platform() -> None:
    machine, system = platform.machine().lower(), platform.system()
    if system == "Linux" and machine not in {"x86_64", "amd64"}:
        raise SetupError(f"Blender 5.0.1's bpy wheel exists only for Linux x86_64, not {machine}.")
    if system == "Darwin" and machine != "arm64":
        raise SetupError("Blender 5.0.1's bpy wheel exists only for Apple Silicon Macs.")


def _venv(path: Path, interpreter: str = PYTHON) -> Path:
    python = _bin(path, "python")
    if not python.is_file():
        _log(f"Creating a Python environment at {path} (uv downloads Python {interpreter} if needed)…")
        _run([uv(), "venv", "--quiet", "-p", interpreter, str(path)], env=_uv_env(), timeout=900)
    return python


def _pymol_interpreter() -> str:
    """This sandbox's own Python when PyMOL has wheels for it, which skips downloading 3.11."""
    low, high = PYMOL_PYTHONS
    return sys.executable if low <= sys.version_info[:2] <= high else PYTHON


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


def _apt_install(packages: list[str]) -> bool:
    if platform.system() != "Linux" or not shutil.which("apt-get"):
        return False
    prefix = [] if os.geteuid() == 0 else (["sudo", "-n"] if shutil.which("sudo") else None)
    if prefix is None:
        return False
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if not getattr(_apt_install, "updated", False):
        _run(prefix + ["apt-get", "update", "-qq"], env=env, check=False, timeout=600)
        _apt_install.updated = True
    _log("Installing system libraries: " + " ".join(packages))
    result = _run(prefix + ["apt-get", "install", "-y", "-qq", "--no-install-recommends", *packages], env=env, check=False, timeout=900)
    return result.returncode == 0


def _import_check(python: Path, code: str, label: str) -> None:
    """Import a module, installing missing Linux shared libraries until it loads."""
    tried_common = False
    installed: set[str] = set()
    for _ in range(12):
        result = _run([python, "-c", code], check=False, timeout=900)
        if result.returncode == 0:
            return
        output = result.stderr + result.stdout
        missing = re.findall(r"(lib[\w.+-]+\.so[\w.]*): cannot open shared object file", output)
        if not missing:
            raise SetupError(f"{label} failed to import:\n{output[-3000:]}")
        packages = [LINUX_LIBRARIES[name] for name in missing if name in LINUX_LIBRARIES]
        if not tried_common:
            packages = sorted(set(packages) | set(COMMON_LINUX))
            tried_common = True
        packages = [p for p in packages if p not in installed]
        if not packages or not _apt_install(packages):
            raise SetupError(f"{label} needs system libraries {missing} that could not be installed automatically "
                             f"(apt-get unavailable or no root). Install: {' '.join(LINUX_LIBRARIES.get(m, m) for m in missing)}")
        installed.update(packages)
    raise SetupError(f"{label} still fails to import after installing system libraries")


def setup_pymol() -> Path:
    venv = home() / "pymol"
    python = _venv(venv, _pymol_interpreter())
    if _run([python, "-c", "import pymol2"], check=False).returncode:
        _log("Installing open-source PyMOL…")
        _run([uv(), "pip", "install", "--quiet", "-p", str(python), PYMOL], env=_uv_env(), timeout=900)
    _import_check(python, "import pymol2\nwith pymol2.PyMOL() as p: p.cmd.fab('AG', 'probe')", "PyMOL")
    _save_state({"pymol_python": str(python)})
    return python


def _requirements_from_wheel(path: Path) -> Path:
    with zipfile.ZipFile(path) as archive:
        text = archive.read("blender_render/runtime-requirements.txt").decode()
    target = Path(tempfile.mkdtemp(prefix="studio-req-")) / "runtime-requirements.txt"
    target.write_text(text)
    return target


def _site_packages(python: Path) -> Path:
    out = _run([python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]).stdout.strip()
    return Path(out)


def setup_render(pymol_python: Path) -> dict:
    _check_platform()
    venv = home() / "render"
    python = _venv(venv)
    renderer = wheel()
    digest = hashlib.sha256(renderer.read_bytes()).hexdigest()
    if _run([python, "-c", "import bpy, molecularnodes"], check=False, timeout=900).returncode:
        _log("Installing Blender 5.0.1 (bpy) and Molecular Nodes 4.5.10 — about 1.2 GB, typically 1–3 minutes…")
        requirements = _requirements_from_wheel(renderer)
        _run([uv(), "pip", "install", "--quiet", "-p", str(python), BPY, "-r", str(requirements)], env=_uv_env(), timeout=1800)
    if state().get("wheel_sha256") != digest or not _bin(venv, "blender-render").is_file():
        _log("Installing the renderer package…")
        _run([uv(), "pip", "install", "--quiet", "--reinstall-package", "pymol-plugin-bridge", "-p", str(python), str(renderer)],
             env=_uv_env(), timeout=900)
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
    return {"home": str(home()), "pymol_ready": _pymol_ready(), "render_ready": _render_ready(),
            "setup_running": bool(running), "setup_log": (running or {}).get("log", ""), "gpu_likely": gpu, "state": current}


def _pymol_ready() -> bool:
    current = state()
    return bool(current.get("pymol_python")) and Path(current["pymol_python"]).is_file()


def _render_ready() -> bool:
    current = state()
    if not (current.get("blender_render") and Path(current["blender_render"]).is_file()):
        return False
    # After a plugin update the bundled renderer changes; `setup` then reinstalls just the wheel.
    return bool(current.get("external_renderer")) or current.get("wheel_sha256") == hashlib.sha256(wheel().read_bytes()).hexdigest()


def renderer_env() -> tuple[Path, dict]:
    _wait_until(_render_ready, "Blender")
    current = state()
    if not current.get("blender_render") or not Path(current["blender_render"]).is_file():
        raise SetupError("Blender is not installed yet. Run: studio.py setup")
    env = dict(os.environ)
    if current.get("blender_render_home"):
        env["BLENDER_RENDER_HOME"] = current["blender_render_home"]
    return Path(current["blender_render"]), env


def pymol_python() -> Path:
    _wait_until(_pymol_ready, "PyMOL")
    current = state()
    if current.get("pymol_python") and Path(current["pymol_python"]).is_file():
        return Path(current["pymol_python"])
    raise SetupError("PyMOL is not installed yet. Run: studio.py setup --only pymol")
