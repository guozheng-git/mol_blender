"""molblender-studio plugin: packaging, parsing, camera hand-off and viewer page assembly."""
import base64
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "molblender_studio"
SKILL = PLUGIN / "skills" / "molblender-studio"
sys.path.insert(0, str(SKILL / "scripts"))

import artifact_files  # noqa: E402
import camera  # noqa: E402
import scene  # noqa: E402
import structure_io  # noqa: E402
import studio  # noqa: E402
import viewer  # noqa: E402

EXAMPLES = ROOT / "examples"


def page_data(html: str) -> dict:
    match = re.search(r'<script type="application/json" id="studio-data">(.*?)</script>', html, re.S)
    return json.loads(match.group(1))


def test_staged_wheel_matches_renderer_source():
    # A release build stages generated artifacts outside the frozen source tree.
    wheel_path = os.environ.get('MOLBLENDER_STUDIO_TEST_WHEEL')
    if not wheel_path:
        pytest.skip('Set MOLBLENDER_STUDIO_TEST_WHEEL to validate a freshly built wheel')
    from mol_blender import engine
    root = Path(engine.__file__).parent
    with zipfile.ZipFile(wheel_path) as archive:
        expected = engine.generate_manifest(root)
        assert json.loads(archive.read('mol_blender/engine-manifest.json')) == expected
        for name in expected['files']:
            assert archive.read('mol_blender/' + name) == (root / name).read_bytes()


def test_preset_catalog_is_current():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_plugin
    assert json.loads((SKILL / "references" / "presets.json").read_text()) == build_plugin.preset_catalog()


def test_manifests_and_skill_frontmatter():
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert plugin["name"] == "molblender-studio"
    assert [(ROOT / p["source"]).resolve() for p in market["plugins"]] == [PLUGIN.resolve()]
    assert not (PLUGIN / "bin").exists(), "claude.ai refuses plugins with a top-level bin/"
    text = (SKILL / "SKILL.md").read_text()
    front = text.split("---")[1]
    keys = {line.split(":", 1)[0] for line in front.strip().splitlines()}
    # claude.ai skill uploads accept only these frontmatter keys.
    assert keys <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    fields = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in front.strip().splitlines()}
    assert fields["name"] == SKILL.name and len(fields["description"]) <= 1024 and len(fields["compatibility"]) <= 500
    assert len(text.splitlines()) < 500


@pytest.mark.parametrize("name, chains, ligand", [
    ("1stp.cif", ["A"], "BTN A300"),
    ("1bna.cif", ["A", "B"], None),
    ("2trx.cif", ["A", "B"], "MPD A601"),
    ("1sps.cif", ["A", "D", "B", "E", "C", "F"], "PTR D0"),
])
def test_structure_summary(name, chains, ligand):
    summary = structure_io.summarize(*structure_io.read_structure_bytes(str(EXAMPLES / name)))
    assert [c["id"] for c in summary["chains"]] == chains
    assert (summary["ligands"][0]["key"] if summary["ligands"] else None) == ligand
    assert summary["resolution_angstrom"] and summary["atoms"] > 400
    if name == "2trx.cif":
        assert all(l["additive"] for l in summary["ligands"]) and summary["ions"]
    if name == "1sps.cif":
        # In-chain non-standard residues large enough to matter are reported as covalent ligands.
        assert all(l["covalent"] for l in summary["ligands"]) and len(summary["ligands"]) == 3
    if name == "1bna.cif":
        assert {c["type"] for c in summary["chains"]} == {"nucleic acid"}


def test_pdb_format_and_gzip(tmp_path):
    lines = ["HEADER    TEST                                    01-JAN-00   1ABC              ",
             "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00 90.00           N",
             "ATOM      2  CA  GLY A   1       1.400   0.000   0.000  1.00 90.00           C",
             "ATOM      3  C   GLY A   1       2.000   1.400   0.000  1.00 90.00           C",
             "HETATM    4 ZN    ZN A 101       5.000   5.000   5.000  1.00 20.00          ZN",
             "HETATM    5  C1  LIG A 102       2.000   2.000   2.000  1.00 20.00           C",
             "HETATM    6  O1  LIG A 102       2.500   2.800   2.000  1.00 20.00           O", "END"]
    path = tmp_path / "AF-test.pdb.gz"
    path.write_bytes(gzip.compress("\n".join(lines).encode()))
    data, name = structure_io.read_structure_bytes(str(path))
    summary = structure_io.summarize(data, name)
    assert name == "AF-test.pdb" and summary["id"] == "1ABC"
    assert summary["ions"] == ["ZN A101"] and summary["ligands"][0]["key"] == "LIG A102"
    assert summary["likely_predicted_model"]


def test_mirrors():
    assert structure_io.mirrors("4HHB")[0][0] == "https://files.rcsb.org/download/4hhb.cif.gz"
    assert "api/prediction/P69905" in structure_io.mirrors("AF-P69905-F1")[0][0]
    with pytest.raises(ValueError):
        structure_io.mirrors("not an id")


def test_camera_conversion_matches_pymol_convention():
    # Camera on the -x side looking at +x: PyMOL reports this after `turn y, 90` (verified by ray tracing).
    view = camera.molstar_to_pymol({"position": [-50, 0, 0], "target": [0, 0, 0], "up": [0, 1, 0], "fov_deg": 30}, 20)
    assert [round(v, 6) for v in view[:9]] == [0, 0, -1, 0, 1, 0, 1, 0, 0]
    assert view[9:15] == [0, 0, -50, 0, 0, 0] and view[15:] == [30, 70, -30]
    shot = {"position": [19.9, 16.2, 65.1], "target": [19.9, 16.2, 24.8], "up": [0, 1, 0], "fov_deg": 45, "mode": "perspective"}
    back = camera.pymol_to_molstar(camera.molstar_to_pymol(shot, 30))
    for key in ("position", "target", "up"):
        assert back[key] == pytest.approx(shot[key], abs=1e-6)
    assert back["fov_deg"] == 45 and back["mode"] == "perspective"
    wrapped = json.dumps({"molblender_studio_view": shot})
    assert camera.load_view(wrapped) == shot


def test_viewer_page(tmp_path):
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    (tmp_path / "r.png").write_bytes(png)
    (tmp_path / "s.pse").write_bytes(b"session")
    result = viewer.build(str(EXAMPLES / "1stp.cif"), str(tmp_path / "v.html"), style="site", ligand="BTN",
                          renders=[f"{tmp_path / 'r.png'}=mint|Mint look"], files=[str(tmp_path / "s.pse")])
    html = (tmp_path / "v.html").read_text()
    assert not [token for token in ("__PAGE_TITLE__", "__MOLSTAR_CSS__", "__MOLSTAR_VERSION__", "__STUDIO_DATA__") if token in html]
    assert html.startswith("<title>1STP Structure Viewer</title>") and "<!doctype" not in html
    assert "cdn.jsdelivr.net/npm/molstar@5.12.0/build/viewer/molstar.js" in html
    data = page_data(html)
    assert gzip.decompress(base64.b64decode(data["structure"]["data"])) == (EXAMPLES / "1stp.cif").read_bytes()
    assert data["scene"]["layers"][0]["select"] == {"site": "BTN A300"} and data["scene"]["focus"] == {"ligand": "BTN A300"}
    assert data["workspace"] is False and data["renders"][0]["label"] == "mint"
    assert data["renders"][0]["src"].startswith("data:image/png;base64,")
    bundle = zipfile.ZipFile(__import__("io").BytesIO(base64.b64decode(data["downloads"]["data"])))
    assert bundle.read("s.pse") == b"session"
    assert result["needs_downloads_capability"] and result["structure"]["ligands"][0]["key"] == "BTN A300"
    standalone = viewer.build(str(EXAMPLES / "1stp.cif"), str(tmp_path / "s.html"), standalone=True)
    assert Path(standalone["viewer"]).read_text().startswith("<!doctype html>")


def test_viewer_escapes_script_end(tmp_path):
    source = tmp_path / "x.cif"
    source.write_text((EXAMPLES / "1stp.cif").read_text().replace("_struct.title", "_struct.title '</script><b>'\n#_x", 1))
    viewer.build(str(source), str(tmp_path / "x.html"))
    html = (tmp_path / "x.html").read_text()
    assert page_data(html)["title"] == "</script><b>"
    assert html.count("</script>") == html.count("<script")


def test_cpu_defaults_cap_heavy_presets(monkeypatch):
    monkeypatch.setattr(studio.studio_env, "doctor", lambda: {"gpu_likely": False})
    args = studio.parser().parse_args(["render", "x.cif", "--preset", "dried_paint_green"])
    assert studio.cpu_defaults(args) == {"width": 1280, "height": 1072, "samples": 64}
    args = studio.parser().parse_args(["render", "x.cif", "--preset", "dried_paint_green", "--full-quality"])
    assert studio.cpu_defaults(args) == {}
    monkeypatch.setattr(studio.studio_env, "doctor", lambda: {"gpu_likely": True})
    assert studio.cpu_defaults(studio.parser().parse_args(["render", "x.cif"])) == {}


def test_unique_output_names(tmp_path):
    (tmp_path / "a.png").write_bytes(b"")
    (tmp_path / "a-2.render").mkdir()
    assert studio.unique(tmp_path / "a.png").name == "a-3.png"


def test_session_camera_changes_do_not_request_restyling(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(studio, "run_pymol_session", lambda args: commands.append(args))
    args, extra = studio.parser().parse_known_args(["session", "original.pse", "--fit", "--out-dir", str(tmp_path)])
    studio.cmd_session(args, extra)
    assert "--fit" in commands[0] and "--style" not in commands[0]
    args.style = "surface"
    studio.cmd_session(args, extra)
    assert commands[1][-2:] == ["--style", "surface"]


def test_decompressed_inputs_with_same_name_keep_their_own_content(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    paths = []
    for folder, content in [("first", b"data_first\n"), ("second", b"data_second\n")]:
        source = tmp_path / folder / "model.cif.gz"
        source.parent.mkdir()
        source.write_bytes(gzip.compress(content))
        path = studio.renderable(str(source), output)
        assert path.read_bytes() == content
        paths.append(path)
    assert paths[0] != paths[1]
    assert paths[0].read_bytes() == b"data_first\n"


def test_render_staging_is_cleaned_on_failure(tmp_path, monkeypatch):
    source = tmp_path / "model.cif.gz"
    source.write_bytes(gzip.compress(b"data_test\n"))
    monkeypatch.setattr(studio.studio_env, "renderer_env", lambda: (Path("unused"), {}))
    staged = []

    def fail(args, path, directory, executable, env, *, recipe, staging):
        assert path.read_bytes() == b"data_test\n"
        staged.append(path)
        raise RuntimeError("render failure")

    monkeypatch.setattr(studio, "_render_prepared", fail)
    args = studio.parser().parse_args(["render", str(source), "--out-dir", str(tmp_path)])
    with pytest.raises(RuntimeError, match="render failure"):
        studio.cmd_render(args)
    assert not staged[0].exists() and not staged[0].parent.exists()


@pytest.mark.parametrize("chain", ["A", ""])
def test_show_chain_preserves_other_chains_and_residue_exclusions(chain):
    hidden = [{"chain": [chain, "B"]}, {"chain": [chain], "residues": "1-5"}]
    args = studio.parser().parse_args(["scene", json.dumps({"hidden": hidden}), "--show-chain", chain])
    assert studio.cmd_scene(args)["scene"]["hidden"] == [
        {"chain": ["B"]}, {"chain": [chain], "residues": "1-5"}]
    assert hidden[0]["chain"] == [chain, "B"]


def test_blank_pdb_chain_identity_and_selection():
    data = b"ATOM      1  CA  GLY     1       0.000   0.000   0.000  1.00 90.00           C\n"
    summary = structure_io.summarize(data, "blank.pdb")
    assert summary["chains"][0]["id"] == ""
    normalized = scene.validate({"hidden": [{"chain": ""}]}, summary)
    assert scene.to_pymol(normalized["hidden"][0], "obj") == '(obj) and chain ""'
    assert scene.ligand_pymol("HEM :142", "obj") == '(obj and resn HEM and chain "" and resi 142)'
    assert scene.ligand_pymol("HEM 142", "obj") == '(obj and resn HEM and chain 1 and resi 42)'


def test_viewer_rejects_binarycif_explicitly(tmp_path):
    source = tmp_path / "structure.bcif"
    source.write_bytes(b"binary fixture")
    with pytest.raises(ValueError, match="convert BinaryCIF"):
        viewer.build(str(source), str(tmp_path / "page.html"))


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed for the viewer regression suite")
def test_viewer_javascript_behaviors():
    result = subprocess.run(["node", "--test", str(ROOT / "tests/molblender_studio_viewer.test.cjs")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_scene_validation_and_shortcuts(tmp_path):
    summary = structure_io.summarize(*structure_io.read_structure_bytes(str(EXAMPLES / "1stp.cif")))
    start = scene.validate({"hidden": [{"chain": "A", "residues": "1-10, 20"}]}, summary)
    assert start["hidden"] == [{"chain": ["A"], "residues": "1-10,20"}] and start["representation"] == "cartoon"
    for bad, message in [({"hidden": [{"chain": "Z"}]}, "Chain 'Z'"), ({"layers": [{"select": {"site": "XYZ"}}]}, "Ligand"),
                         ({"color": "purple"}, "color"), ({"wobble": 1}, "Unknown scene fields")]:
        with pytest.raises(scene.SceneError, match=message):
            scene.validate(bad, summary)
    current = tmp_path / "current.json"
    current.write_text(json.dumps(dict(scene.default_scene(), revision=3, hidden=[{"chain": "A"}])))
    args = studio.parser().parse_args(["scene", str(current), "--show-chain", "A", "--hide-chain", "B", "--site", "BTN A300",
                                       "--color", "ss", "--note", "n"])
    edited = studio.cmd_scene(args)["scene"]
    assert edited["hidden"] == [{"chain": ["B"]}] and edited["revision"] == 4 and edited["color"] == "ss"
    assert edited["layers"] == [{"select": {"site": "BTN A300"}, "representation": "sticks", "color": "element"}]
    assert edited["focus"] == {"ligand": "BTN A300"} and edited["opacity"] == 0.45 and edited["updated_by"] == "claude"


def test_scene_selections_translate_to_pymol():
    sel = lambda value: scene.to_pymol(scene._selection(value, "s"), "obj")
    assert sel({"chain": ["A", "B"], "residues": "-3-5,60A"}) == "(obj) and chain A+B and resi \\-3-5+60A"
    assert "within 5.0 of (obj and resn HEM and chain A and resi 142)" in sel({"site": "HEM A142"})
    assert sel({"kind": "water"}) == "(obj) and solvent"


def test_session_bundle_round_trip(tmp_path):
    pse, blend = tmp_path / "a.pse", tmp_path / "a.blend"
    pse.write_bytes(os.urandom(6000))  # incompressible, so the small part size forces several parts
    blend.write_bytes(b"\x28\xb5\x2f\xfd" + os.urandom(3000))
    original = artifact_files.PART_CHARS
    artifact_files.PART_CHARS = 2000  # force multi-part bundles
    try:
        packed = artifact_files.bundle({"pse": str(pse), "blend": str(blend)}, str(tmp_path / "out" / "r1"))
    finally:
        artifact_files.PART_CHARS = original
    assert len(packed["parts"]) > 1 and {f["role"] for f in packed["files"]} == {"pse", "blend"}
    restored = artifact_files.restore(list(reversed(packed["parts"])), str(tmp_path / "back"))
    assert {Path(r["path"]).read_bytes() for r in restored} == {pse.read_bytes(), blend.read_bytes()}
    with pytest.raises(ValueError, match="missing bundle parts"):
        artifact_files.restore(packed["parts"][:1], str(tmp_path / "partial"))
    png = tmp_path / "r.png"
    png.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="))
    render = {"preset": "mint_sky_assembly", "seconds": 3.2, "outputs": {"render.png": str(png)}, "session": packed}
    doc = artifact_files.record(render, {"image": "img", "session": [f"id{i}" for i in range(len(packed["parts"]))]}, label="L")
    assert doc["status"] == "done" and doc["image"]["width"] == 1 and doc["session"]["asset_ids"][0] == "id0"
    with pytest.raises(ValueError, match="asset id"):
        artifact_files.record(render, {"image": "img", "session": ["only-one"]})


@pytest.mark.parametrize("filename", ["../outside.pse", "/outside.pse", "sub/file.pse", "..\\outside.pse", "C:\\outside.pse", "..", ""])
def test_bundle_restore_rejects_unsafe_names(tmp_path, filename):
    source = tmp_path / "source.pse"
    source.write_bytes(b"session")
    packed = artifact_files.bundle({"pse": str(source)}, str(tmp_path / "bundle"))
    part = Path(packed["parts"][0])
    data = json.loads(part.read_text())
    data["entries"][0]["filename"] = filename
    part.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsafe bundle"):
        artifact_files.restore(packed["parts"], str(tmp_path / "out"))
    assert not (tmp_path / "out").exists()


def test_bundle_restore_refuses_symlinks_and_overwrites(tmp_path):
    source = tmp_path / "source.pse"
    source.write_bytes(b"session")
    packed = artifact_files.bundle({"pse": str(source)}, str(tmp_path / "bundle"))
    out = tmp_path / "out"
    out.mkdir()
    target = out / source.name
    target.symlink_to(source)
    with pytest.raises(ValueError, match="Unsafe bundle"):
        artifact_files.restore(packed["parts"], str(out))
    target.unlink()
    target.write_bytes(b"keep me")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        artifact_files.restore(packed["parts"], str(out))
    assert target.read_bytes() == b"keep me"
    target.unlink()
    artifact_files.restore(packed["parts"], str(out))
    artifact_files.restore(packed["parts"], str(out))
    assert target.read_bytes() == source.read_bytes()


def test_workspace_command_and_stored_text(tmp_path):
    out = studio.cmd_workspace(studio.parser().parse_args(["workspace", str(EXAMPLES / "1stp.cif"), "--out-dir", str(tmp_path)]))
    assert out["capabilities"]["db"]["rules"][0] == {"path": "scene", "read": "view", "write": "interact"}
    html = Path(out["page"]).read_text()
    assert html.startswith("<title>") and page_data(html)["workspace"] is True
    meta = json.loads(Path(out["meta_file"]).read_text())
    assert meta["structure"]["asset_id"] is None and meta["structure"]["sha256"] == artifact_files.sha256((EXAMPLES / "1stp.cif").read_bytes())
    assert json.loads(Path(out["scene_file"]).read_text())["chain_colors"] == {"A": "#4bb5a6"}
    stored = tmp_path / "0123456789abcdef0123456789abcdef.txt"
    stored.write_bytes(Path(out["structure_upload"]).read_bytes())
    data, name = structure_io.read_structure_bytes(str(stored))
    assert name == "0123456789abcdef0123456789abcdef.cif" and data == (EXAMPLES / "1stp.cif").read_bytes()
    assert studio.renderable(str(stored), tmp_path, "1stp.cif").name == "1stp.cif"


@pytest.mark.parametrize("compressed", [False, True])
def test_workspace_uses_one_source_snapshot(tmp_path, monkeypatch, compressed):
    raw = (EXAMPLES / "1stp.cif").read_bytes()
    source = tmp_path / ("input.cif.gz" if compressed else "input.cif")
    source.write_bytes(gzip.compress(raw) if compressed else raw)
    read = structure_io.read_structure_bytes
    summarize = structure_io.summarize
    calls = {"read": 0, "summarize": 0}

    def read_once(path):
        calls["read"] += 1
        snapshot = read(path)
        # The page and uploaded file must use the same snapshot even if the source disappears.
        source.unlink()
        return snapshot

    def summarize_once(data, name):
        calls["summarize"] += 1
        return summarize(data, name)

    monkeypatch.setattr(structure_io, "read_structure_bytes", read_once)
    monkeypatch.setattr(structure_io, "summarize", summarize_once)
    out = studio.cmd_workspace(studio.parser().parse_args([
        "workspace", str(source), "--out-dir", str(tmp_path / "workspace"),
    ]))
    assert calls == {"read": 1, "summarize": 1}
    data = page_data(Path(out["page"]).read_text())
    assert gzip.decompress(base64.b64decode(data["structure"]["data"])) == raw
    assert Path(out["structure_upload"]).read_bytes() == raw
    meta = json.loads(Path(out["meta_file"]).read_text())
    assert meta["structure"]["sha256"] == artifact_files.sha256(raw)
    assert data["scene"]["chain_colors"] == {"A": "#4bb5a6"}


def test_pymol_written_ligands_are_not_polymer(tmp_path):
    # PyMOL's mmCIF gives ligands chain-style label_seq_id values; only residues with a backbone are polymer.
    rows = [("ATOM", "N", "N", "GLY", "A", 1, 1, 0.0), ("ATOM", "C", "CA", "GLY", "A", 1, 1, 1.4), ("ATOM", "C", "C", "GLY", "A", 1, 1, 2.0),
            ("HETATM", "FE", "FE", "HEM", "B", 142, 142, 5.0), ("HETATM", "C", "CHA", "HEM", "B", 142, 142, 6.0),
            ("HETATM", "SE", "SE", "MSE", "A", 2, 2, 3.0), ("HETATM", "N", "N", "MSE", "A", 2, 2, 3.5), ("HETATM", "C", "CA", "MSE", "A", 2, 2, 4.0),
            ("HETATM", "C", "C", "MSE", "A", 2, 2, 4.4)]
    lines = ["data_s1", "loop_"] + [f"_atom_site.{f}" for f in ("group_PDB", "type_symbol", "label_atom_id", "label_comp_id",
                                                                  "label_asym_id", "label_seq_id", "auth_seq_id", "Cartn_x",
                                                                  "Cartn_y", "Cartn_z", "auth_asym_id")]
    lines += [f"{g} {e} {n} {c} {a} {s} {s2} {x} 0 0 A" for g, e, n, c, a, s, s2, x in rows]
    path = tmp_path / "s1.cif"
    path.write_text("\n".join(lines) + "\n")
    summary = structure_io.summarize(*structure_io.read_structure_bytes(str(path)))
    assert summary["chains"] == [{"id": "A", "residues": 2, "type": "protein", "description": "", "gene": ""}]
    assert [l["key"] for l in summary["ligands"]] == ["HEM A142"]


def test_background_setup_lock(tmp_path, monkeypatch):
    import studio_env
    monkeypatch.setenv("MOLBLENDER_STUDIO_HOME", str(tmp_path))
    assert studio_env.running_setup() is None
    (tmp_path / "setup.lock").write_text(json.dumps({"pid": os.getpid(), "log": "x"}))
    assert studio_env.running_setup()["pid"] == os.getpid()
    studio_env._wait_until(lambda: {}, lambda current: False, "nothing")  # our own lock never blocks
    (tmp_path / "setup.lock").write_text(json.dumps({"pid": 2 ** 22 + 12345, "log": "x"}))
    assert studio_env.running_setup() is None  # a dead installer is ignored
    assert studio_env.doctor()["render_ready"] is False
    assert studio_env._pymol_interpreter() == studio_env.PYTHON
