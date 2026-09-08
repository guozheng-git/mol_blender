"""B fix acceptance using an installed controller and fresh, isolated Blender previews."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

checkout = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(checkout))
from blender_render import authoring, developer
from blender_render.presets import preset_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-site", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--runtime-path", type=Path, action="append", required=True)
    parser.add_argument("--runtime-wheels", type=Path, required=True)
    parser.add_argument("--ccd", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    evidence = {"cases": {}, "source_sha256": developer.source_digest(developer.source_files(checkout / "blender_render"))}
    catalog = root / "catalog"
    macro = json.loads((checkout / "examples/developer/macro_focus.json").read_text())
    authoring.save_preset("user/macro", macro, catalog, description="Authored camera", camera_policy="authored")
    pastel = json.loads(preset_path("pastel").read_text())
    authoring.save_preset("user/pastel", pastel, catalog, description="Saved built-in defaults")
    quality = root / "quality.json"
    quality.write_text(json.dumps({"output": {"width": 480, "samples": 32, "save_blend": False}, "renderer": {"seed": 7}}))
    # This temporary controller is the built wheel with only DOF schema support removed.
    old_site = root / "older-controller"
    shutil.copytree(args.installed_site / "blender_render", old_site / "blender_render", ignore=shutil.ignore_patterns("__pycache__"))
    schema_path = old_site / "blender_render/style.schema.json"
    schema = json.loads(schema_path.read_text())
    del schema["properties"]["camera"]["properties"]["depth_of_field"]
    schema_path.write_text(json.dumps(schema))

    def preview(name, preset, *, source=checkout, controller=args.installed_site, negative=None, extra=()):
        output = root / name
        bootstrap = ("import sys,json,hashlib,pathlib; sys.path.insert(0, sys.argv.pop(1)); "
                     "from blender_render import cli; "
                     "print(json.dumps({'controller_module':cli.__file__, 'controller_schema_sha256':"
                     "hashlib.sha256(pathlib.Path(cli.__file__).with_name('style.schema.json').read_bytes()).hexdigest()}), file=sys.stderr); "
                     "raise SystemExit(cli.main())")
        command = [sys.executable, "-B", "-I", "-c", bootstrap, str(controller), "dev", "--catalog-dir", str(catalog),
                   "preview", str(args.bundle), "--checkout", str(source), "--preset", preset, "--output", str(output),
                   "--blender", str(args.blender), "--ccd", str(args.ccd), "--overrides-json", str(quality), *extra]
        for path in args.runtime_path:
            command += ["--runtime-path", str(path)]
        if not negative:
            command += ["--runtime-wheels", str(args.runtime_wheels)]
        env = developer.isolated_environment(root / (name + "-controller-state"))
        proc = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=360)
        (root / (name + ".log")).write_text(proc.stdout + proc.stderr)
        controller_record = json.loads(proc.stderr.splitlines()[0])
        assert Path(controller_record["controller_module"]).resolve().is_relative_to(controller.resolve())
        record = {"command": command, "exit": proc.returncode, **controller_record}
        if negative:
            log = (output / "preview.log").read_text()
            assert proc.returncode == 1 and negative in log, log
            assert not (output / "blender.log").exists() and not (output / "render.png").exists()
            record.update(rejected_before_blender=True, expected_error=negative)
        else:
            assert proc.returncode == 0, proc.stderr
            result = json.loads((output / "preview.json").read_text())
            assert result["sha256"] == evidence["source_sha256"] and not result["cache_hit"]
            assert result["blender"]["sha256"] == result["sha256"]
            if preset.startswith("user/"):
                assert result["catalog_snapshot"]["sha256"] == developer.digest_file(catalog / (preset + ".json"))
            record.update(source_sha256=result["sha256"], spec_sha256=result["render_spec"]["sha256"],
                          pid=result["blender"]["pid"], png_sha256=result["png_sha256"],
                          catalog_snapshot=result.get("catalog_snapshot"))
        evidence["cases"][name] = record
        print("PASS", name, flush=True)

    # Real success despite the installed controller not understanding the saved DOF JSON.
    preview("older_controller", "user/macro", controller=old_site)
    preview("builtin_pose", "pastel")
    preview("local_pose", "user/pastel")
    pair = [json.loads((root / case / "manifest.json").read_text()) for case in ("builtin_pose", "local_pose")]
    assert pair[0]["camera_matrix_world"] == pair[1]["camera_matrix_world"]
    assert pair[0]["resolution"] == pair[1]["resolution"]
    assert pair[0]["resolved_preset"] == pair[1]["resolved_preset"]
    images = [np.asarray(Image.open(root / case / "render.png").convert("RGBA"), dtype=float) for case in ("builtin_pose", "local_pose")]
    difference = float(np.abs(images[0] - images[1]).mean())
    assert difference < .1
    assert not any((root / case / "render.blend").exists() for case in ("builtin_pose", "local_pose"))
    evidence["parity"] = {"camera_and_spec_equal": True, "resolution": pair[0]["resolution"], "mean_rgba_difference": difference, "png_only": True}
    assert len({record["pid"] for record in evidence["cases"].values()}) == 3

    old_source = root / "older-checkout"
    developer.snapshot_source(checkout, old_source)
    (old_source / "blender_render/style.schema.json").write_bytes(schema_path.read_bytes())
    preview("older_checkout", "user/macro", source=old_source, negative="Additional properties are not allowed")
    preview("incompatible_policy", "user/macro", negative="cannot discard", extra=("--preserve-view",))
    original = json.loads((catalog / "user/macro.json").read_text())
    invalid = {
        "malformed": b"{broken",
        "wrong_id": json.dumps({**original, "id": "user/other"}).encode(),
        "wrong_digest": json.dumps({**original, "sha256": "wrong"}).encode(),
        "missing_policy": json.dumps({k:v for k,v in original.items() if k != "camera_policy"}).encode(),
        "invalid_policy": json.dumps({**original, "camera_policy": "guess"}).encode(),
    }
    errors = ["Expecting property name", "identity/format", "digest mismatch", "camera_policy", "camera_policy"]
    for (name, raw), error in zip(invalid.items(), errors):
        (catalog / "user/macro.json").write_bytes(raw)
        preview(name, "user/macro", negative=error)
    (catalog / "user/macro.json").write_text(json.dumps(original))
    shutil.rmtree(old_site)
    shutil.rmtree(old_source)
    (root / "acceptance.json").write_text(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
