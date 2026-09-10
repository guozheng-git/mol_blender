# SPDX-License-Identifier: GPL-3.0-or-later
"""Fresh-preview entrypoint: verify source/runtime, then call the shared renderer."""
import importlib.metadata
import json
import os
import sys
from pathlib import Path

import bpy

package = Path(__file__).resolve().parent
sys.path[:0] = [str(package), str(package.parent)]
from blender_render.developer import digest_file, source_files

cfg = json.loads(Path(os.environ["BLENDER_RENDER_DEV_CONFIG"]).read_text())
expected = cfg["identity"]["files"]
if source_files(package) != expected or digest_file(Path(cfg["ccd"])) != cfg["ccd_sha256"]:
    raise RuntimeError("Development source/CCD changed before Blender execution")
import molecularnodes
import biotite
import numpy
import tomllib
try:
    mn_version = importlib.metadata.version("molecularnodes")
except importlib.metadata.PackageNotFoundError:
    mn_version = tomllib.loads(Path(molecularnodes.__file__).with_name("blender_manifest.toml").read_text())["version"]
versions = {"blender": bpy.app.version_string, "molecularnodes": mn_version,
            "python": ".".join(map(str, sys.version_info[:2])),
            "numpy": numpy.__version__, "biotite": biotite.__version__}
lock = json.loads((package / "runtime.lock.json").read_text())
if cfg["ccd_sha256"] != lock["ccd_sha256"]:
    raise RuntimeError("Development CCD does not match runtime.lock.json")
if any(versions[key] != lock[key] for key in versions):
    raise RuntimeError("Development runtime does not match runtime.lock.json: " + str(versions))
from blender_render import blender_custom_render as render
from blender_render import worker_ccd

# cfg names the preview's private copy. Never select an inherited runtime CCD
# path: the Blender process owns a second, pinned capture for its full lifetime.
os.environ["PRESS_BIOTITE_CCD_PATH"] = cfg["ccd"]
capture = worker_ccd.initialize(parent=Path(cfg["root"]), expected=lock["ccd_sha256"])
try:
    args = render._parse_argv(sys.argv)
    render._render(args)

    loaded = {}
    for name, module in list(sys.modules.items()):
        raw = getattr(module, "__file__", None)
        if not raw:
            continue
        path = Path(raw).resolve()
        # Both flat compatibility imports and package imports must resolve to this
        # snapshot. Check every loaded module whose name belongs to the package.
        owned = name.startswith("blender_render") or (name.split(".")[0] + ".py") in expected
        if owned and path.suffix == ".py":
            if not path.is_relative_to(package):
                raise RuntimeError("Stale/foreign renderer import: " + str(path))
            relative = path.relative_to(package).as_posix()
            if digest_file(path) != expected.get(relative):
                raise RuntimeError("Loaded source digest mismatch: " + str(path))
            loaded[name] = {"path": str(path), "sha256": digest_file(path)}
    if source_files(package) != expected:
        raise RuntimeError("Source snapshot changed during preview")
    manifest_path = Path(args.run_manifest)
    manifest = json.loads(manifest_path.read_text())
    manifest["development_source"] = {
        "sha256": cfg["identity"]["sha256"], "loaded_modules": loaded,
        "runtime_versions": versions, "blender_binary": bpy.app.binary_path,
        "blender_binary_sha256": digest_file(Path(bpy.app.binary_path)),
        "pid": os.getpid(), "ccd_sha256": cfg["ccd_sha256"],
        "runtime_modules": {module.__name__: {"path": module.__file__, "sha256": digest_file(Path(module.__file__))}
                            for module in (molecularnodes, biotite, numpy)},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
finally:
    capture.close()
