"""Milestone B local acceptance: real edited Python and JSON, independent processes.

Supply a public 1SPS StructureBundle from the existing exporter and explicit
installed runtime paths. All snapshots, mutations, state and results are local.
"""
import argparse
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

checkout = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(checkout))
from blender_render import authoring, developer
from blender_render.spec import resolve_options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--runtime-path", type=Path, action="append", required=True)
    parser.add_argument("--runtime-wheels", type=Path)
    parser.add_argument("--ccd", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    base = json.loads((checkout / "examples/developer/macro_focus.json").read_text())
    evidence = {"cases": {}, "checks": {}, "bundle_sha256": developer.digest_file(args.bundle)}

    def render(name, options, source=checkout):
        print("START", name, flush=True)
        result = developer.preview(checkout=source, bundle=args.bundle, options=options,
            catalog_dir=root / "catalog", output=root / name, blender=args.blender,
            runtime_paths=args.runtime_path, runtime_wheels=args.runtime_wheels, ccd=args.ccd,
            timeout=300)
        evidence["cases"][name] = {key: result[key] for key in
            ("sha256", "bundle_sha256", "render_spec", "preview_key", "png_sha256", "cache_hit")}
        evidence["cases"][name]["pid"] = result["blender"]["pid"]
        if result["render_spec"]["required_capabilities"] and "camera.depth_of_field" in result["render_spec"]["required_capabilities"]:
            env = developer.isolated_environment(root / (name + "-inspect-state"))
            command = [str(args.blender), "--background", "--factory-startup", "--disable-autoexec",
                str(root / name / "render.blend"), "--python-exit-code", "1", "--python",
                str(checkout / "scripts/inspect_developer_scene.py"), "--", str(root / name / "scene.json")]
            with (root / name / "scene.log").open("w") as log:
                subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
            shutil.rmtree(root / (name + "-inspect-state"))
            evidence["cases"][name]["scene"] = json.loads((root / name / "scene.json").read_text())
        print("PASS", name, result["sha256"][:12], flush=True)
        return result

    opened = render("focus", {"style_doc": base})
    aperture = copy.deepcopy(base)
    aperture["camera"]["depth_of_field"]["f_stop"] = 8
    sharp = render("json_aperture", {"style_doc": aperture})
    nearer = copy.deepcopy(base)
    nearer["camera"]["depth_of_field"]["focus_distance_angstrom"] = 65
    near = render("json_focus", {"style_doc": nearer})
    default = copy.deepcopy(base)
    del default["camera"]["depth_of_field"]["f_stop"]
    render("json_default", {"style_doc": default})

    alternate = root / "alternate-source"
    developer.snapshot_source(checkout, alternate)
    path = alternate / "blender_render/blender_custom_render.py"
    code = path.read_text()
    before = 'camera.data.dof.aperture_fstop = float(cfg.get("f_stop", 2.8))'
    after = before + ' * 16.0'
    assert code.count(before) == 1
    path.write_text(code.replace(before, after))
    changed = render("python_change", {"style_doc": base}, alternate)
    evidence["python_mutation"] = {"file": "blender_render/blender_custom_render.py",
                                    "before": before, "after": after,
                                    "applied_only_to_temporary_source": True}
    shutil.rmtree(alternate)

    # Saving and reuse run through two additional CLI processes, not an in-memory catalog.
    style_path = root / "saved-style.json"
    style_path.write_text(json.dumps(base, indent=2))
    cli = [sys.executable, "-B", "-m", "blender_render.cli", "dev", "--catalog-dir", str(root / "catalog")]
    subprocess.run([*cli, "save", "user/macro-focus", str(style_path), "--description", "Selective molecular macro focus", "--camera-policy", "authored"],
                   cwd=checkout, check=True, stdout=subprocess.DEVNULL)
    restarted = json.loads(subprocess.check_output([*cli, "inspect", "user/macro-focus"], cwd=checkout, text=True))
    assert restarted["style_doc"] == base
    saved = render("saved_restart", {"preset": "user/macro-focus"})

    quality = {"output": {"width": 640, "height": 480, "samples": 64}, "renderer": {"seed": 7}}
    for preset in ("pastel", "green_paint"):
        result = render(preset, {"preset": preset, "overrides": quality})
        manifest = json.loads((root / preset / "manifest.json").read_text())
        assert manifest["polymer_backend"] == "mn_cartoon" and manifest["ligand_backend"] == "mn_native_sticks"
        assert not manifest["ligand_atom_spheres"] and not manifest["camera_depth_of_field"]["enabled"]
        assert not (root / preset / "render.blend").exists()

    palette = copy.deepcopy(quality)
    palette["materials"] = {"inner_chain_a": {"base_color": "#729ECC"}}
    render("json_palette", {"preset": "pastel", "overrides": palette})
    observed = {"representations": ["cartoon", "sticks"], "material_family": "clay",
        "base_preset": "builtin/preset_1_interface",
        "palette": ["#729ECC", "#F97BB4"], "background": "Pale blue gray",
        "lighting": "Soft highlights", "edges": "Smooth ribbons and round sticks",
        "composition": "One compact protein and a highlighted ligand",
        "ligand_treatment": "Pink sticks", "required_capabilities": [],
        "overrides": {"materials": palette["materials"]}}
    high = authoring.reference_brief(root / "json_palette/render.png", observed)
    low_observed = copy.deepcopy(observed)
    low_observed.update(required_capabilities=["camera.depth_of_field"], overrides={},
                        composition="Selective focus within a compact protein; other depths are soft")
    low = authoring.reference_brief(root / "focus/render.png", low_observed)
    assert high["preset_decision"]["fit"] == "high"
    assert high["preset_decision"]["preset_id"] == "builtin/preset_1_interface"
    replay = copy.deepcopy(quality)
    replay.update(high["preset_decision"]["overrides"])
    replayed = resolve_options({"preset": high["preset_decision"]["preset_id"], "overrides": replay},
                               json.loads(args.bundle.read_text()))
    assert replayed["render_spec"]["sha256"] == evidence["cases"]["json_palette"]["render_spec"]["sha256"]
    assert low["preset_decision"]["new_preset_recommended"] and low["preset_decision"]["fit"] == "low"
    evidence["references"] = {"existing_override": high, "new_design": low,
                              "origin": "Public-fixture PNG previews generated by this validation run"}

    cases = evidence["cases"]
    assert opened["render_spec"]["sha256"] == changed["render_spec"]["sha256"] == saved["render_spec"]["sha256"]
    assert opened["sha256"] == sharp["sha256"] == near["sha256"] == saved["sha256"]
    assert opened["sha256"] != changed["sha256"] and opened["preview_key"] != changed["preview_key"]
    assert opened["preview_key"] == saved["preview_key"]  # provenance namespaces don't affect pixels
    delta = [key for key in opened["files"] if opened["files"][key] != changed["files"][key]]
    assert delta == ["blender_custom_render.py"]
    assert len({case["pid"] for case in cases.values()}) == len(cases)
    assert not any(case["cache_hit"] for case in cases.values())
    scenes = [case["scene"] for case in cases.values() if "scene" in case]
    assert len({scene["geometry_sha256"] for scene in scenes}) == 1
    assert all(scene["camera_matrix"] == scenes[0]["camera_matrix"] for scene in scenes)
    assert all(scene["seed"] == 7 for scene in scenes)
    for name, distance, fstop in (("focus", 82, .5), ("json_aperture", 82, 8),
                                   ("json_focus", 65, .5), ("json_default", 82, 2.8), ("python_change", 82, 8)):
        dof = cases[name]["scene"]["depth_of_field"]
        assert dof["enabled"] and abs(dof["focus_distance_angstrom"] - distance) < 1e-4
        assert abs(dof["f_stop"] - fstop) < 1e-5
    def pixels(name):
        return np.asarray(Image.open(root / name / "render.png").convert("RGB"), dtype=float)
    def distance(a, b):
        return float(np.abs(pixels(a) - pixels(b)).mean())
    def sharpness(name):
        image = Image.open(root / name / "render.png").convert("L").filter(ImageFilter.GaussianBlur(1))
        values = np.asarray(image, dtype=float)
        return float(np.square(np.diff(values, axis=0)).mean() + np.square(np.diff(values, axis=1)).mean())
    comparisons = {"aperture_mean_difference": distance("focus", "json_aperture"),
                   "focus_mean_difference": distance("focus", "json_focus"),
                   "python_vs_equivalent_json": distance("python_change", "json_aperture"),
                   "restart_vs_original": distance("focus", "saved_restart"),
                   "open_aperture_edge_energy": sharpness("focus"),
                   "closed_aperture_edge_energy": sharpness("json_aperture")}
    assert comparisons["aperture_mean_difference"] > .5 and comparisons["focus_mean_difference"] > .5
    assert comparisons["closed_aperture_edge_energy"] > comparisons["open_aperture_edge_energy"] * 1.1
    assert comparisons["python_vs_equivalent_json"] < .1 and comparisons["restart_vs_original"] < .1
    evidence["checks"] = {"fixed_json_python_change": True, "fixed_python_json_controls": True,
        "unchanged_geometry_and_camera": True, "distinct_fresh_processes": True,
        "restarted_saved_preset": True, "legacy_styles_png_only": True, "image_comparisons": comparisons}
    (root / "acceptance.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence["checks"], indent=2), flush=True)


if __name__ == "__main__":
    main()
