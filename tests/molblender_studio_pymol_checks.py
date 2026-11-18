"""Runs in a real PyMOL interpreter, independently of pytest's environment."""
import json
from pathlib import Path
import sys

import pymol2

sys.path.insert(0, sys.argv[1])
import pymol_session
import scene
import structure_io

OUTPUT = Path(sys.argv[2])


def snapshot(path):
    with pymol2.PyMOL() as p:
        cmd = p.cmd
        cmd.load(str(path))
        atoms = []
        cmd.iterate("all", "atoms.append((model, index, chain, resi, name, color, alt, ss))", space={"atoms": atoms})
        return {
            "atoms": atoms,
            "coordinates": cmd.get_coords("all").tolist(),
            "enabled": cmd.get_names("all", enabled_only=1),
            "selection": cmd.count_atoms("chosen_residue"),
            "matrix": cmd.get_object_matrix("distant"),
            "orthoscopic": cmd.get("orthoscopic"),
            "view": list(cmd.get_view()),
        }


def check_preservation():
    source = OUTPUT / "original.pse"
    with pymol2.PyMOL() as p:
        cmd = p.cmd
        cmd.fab("AGG", "molecule")
        cmd.color("red", "molecule")
        cmd.pseudoatom("molecule", resn="HOH", name="O", elem="O", pos=[8, 0, 0], hetatm=1)
        cmd.alter("molecule and resi 1", "alt='B'")
        cmd.fab("GGA", "distant")
        cmd.color("blue", "distant")
        cmd.translate([50, 0, 0], object="distant", camera=0)
        cmd.fab("GG", "disabled_object")
        cmd.disable("disabled_object")
        cmd.select("chosen_residue", "molecule and resi 1")
        cmd.hide("everything", "all")
        cmd.show("sticks", "enabled")
        cmd.set("orthoscopic", 1)
        cmd.turn("y", 25)
        cmd.zoom("molecule")
        cmd.save(str(source))
    before = snapshot(source)
    camera = {"position": [30, 10, 100], "target": [20, 0, 0], "up": [0, 1, 0],
              "fov_deg": 25, "mode": "orthographic"}
    for name, extra in [("unchanged", []), ("fit", ["--fit"]), ("camera", ["--view", json.dumps(camera)])]:
        output = OUTPUT / f"{name}.pse"
        pymol_session.build(pymol_session.parse_args([str(source), "-o", str(output), *extra]))
        after = snapshot(output)
        for key in before.keys() - {"view"}:
            assert after[key] == before[key], (name, key, before[key], after[key])
        if name == "unchanged":
            assert after["view"] == before["view"]
        elif name == "fit":
            assert after["view"][:9] == before["view"][:9], "fit must keep the orientation"
            assert after["view"][12] > before["view"][12] + 10, "fit must include the distant enabled object"
    assert snapshot(source) == before, "the source session must remain unchanged"


def check_blank_chains():
    source = OUTPUT / "blank.pdb"
    with pymol2.PyMOL() as p:
        p.cmd.fab("AGG", "blank")
        p.cmd.alter("blank", "chain=''")
        p.cmd.fab("GG", "labelled")
        p.cmd.alter("labelled", "chain='A'")
        p.cmd.save(str(source))
    summary = structure_io.summarize(*structure_io.read_structure_bytes(str(source)))
    assert {chain["id"] for chain in summary["chains"]} == {"", "A"}
    for name, edit in [("colored", {"chain_colors": {"": "#ff0000", "A": "#0000ff"}}),
                       ("hidden", {"hidden": [{"chain": ""}]})]:
        output = OUTPUT / f"blank-{name}.pse"
        shared = scene.validate(edit, summary)
        pymol_session.build(pymol_session.parse_args([str(source), "-o", str(output), "--scene", json.dumps(shared)]))
        with pymol2.PyMOL() as p:
            p.cmd.load(str(output))
            blank = 'enabled and chain ""'
            assert p.cmd.count_atoms('enabled and chain A') > 0
            if name == "hidden":
                assert p.cmd.count_atoms(blank) == 0
                assert p.cmd.count_atoms('studio_hidden and chain ""') > 0
            else:
                assert p.cmd.count_atoms(blank) > 0
                colors = []
                p.cmd.iterate(blank + ' and elem C', 'colors.append(color)', space={"colors": colors})
                assert {p.cmd.get_color_tuple(color) for color in colors} == {(1.0, 0.0, 0.0)}


def check_styling():
    source = OUTPUT / "styling.cif"
    with pymol2.PyMOL() as p:
        cmd = p.cmd
        cmd.load(str(Path(__file__).resolve().parent.parent / "examples/1stp.cif"), "molecule")
        cmd.alter("polymer", "b = (95, 80, 60, 40)[int(resi) % 4]")
        cmd.pseudoatom("molecule", name="ZN", resn="ZN", resi="900", chain="A", elem="Zn", hetatm=1)
        cmd.pseudoatom("molecule", name="C1", resn="GOL", resi="901", chain="A", elem="C", hetatm=1)
        cmd.pseudoatom("molecule", name="O1", resn="GOL", resi="901", chain="A", elem="O", hetatm=1)
        cmd.save(str(source))

    cases = [(style, ["--style", style], theme, False)
             for style, theme in [("overview", "chain"), ("surface", "chain"), ("secondary", "ss"),
                                  ("confidence", "plddt"), ("site", None)]]
    cases += [("scene-" + theme, ["--scene", json.dumps({"color": theme})], theme, True)
              for theme in ("chain", "ss", "plddt")]
    cases.append(("additives", ["--style", "overview", "--show-additives"], "chain", False))
    for name, flags, theme, shared in cases:
        output = OUTPUT / f"{name}.pse"
        result = pymol_session.build(pymol_session.parse_args([str(source), "-o", str(output), *flags]))
        with pymol2.PyMOL() as p:
            cmd = p.cmd
            cmd.load(str(output))
            atoms = []
            cmd.iterate("polymer and (name CA or name N)",
                        "atoms.append((name, ss, b, color))", space={"atoms": atoms})
            for atom, ss, b, color in atoms:
                if theme is None or (shared and atom != "CA"):
                    continue
                if theme == "chain":
                    expected = "#4bb5a6"
                elif theme == "ss":
                    expected = {"H": "#d8577a", "S": "#e9b949", "L": "#9fb3ad", "": "#9fb3ad"}[ss]
                else:
                    expected = {95: "#0053d6", 80: "#65cbf3", 60: "#ffdb13", 40: "#ff7d45"}[b]
                actual = cmd.get_color_tuple(color)
                assert all(abs(a - e) < 1e-6 for a, e in zip(actual, pymol_session.rgb(expected))), (name, atom, actual, expected)
            if shared:
                nitrogen = {cmd.get_color_tuple(color) for atom, _, _, color in atoms if atom == "N"}
                assert nitrogen == {cmd.get_color_tuple("nitrogen")}, (name, nitrogen)
            carbons = []
            cmd.iterate("resn BTN and elem C", "carbons.append(color)", space={"carbons": carbons})
            assert carbons, name
            for color in carbons:
                assert all(abs(a - e) < 1e-6 for a, e in zip(cmd.get_color_tuple(color), pymol_session.rgb("#f0a43a"))), name
            assert cmd.count_atoms("resn BTN and rep sticks") > 0, name
            assert cmd.count_atoms("resn ZN and rep spheres") == 1, name
            ion_sizes = []
            cmd.iterate("resn ZN", "ion_sizes.append(s.sphere_scale)", space={"ion_sizes": ion_sizes})
            assert all(abs(size - 0.6) < 1e-6 for size in ion_sizes), name
            assert bool(cmd.count_atoms("resn GOL and rep sticks")) == (name == "additives"), name
            assert cmd.count_atoms("polymer and rep cartoon") > 0, name
            if name == "surface":
                assert cmd.count_atoms("polymer and rep surface") > 0
            if name == "confidence":
                assert len(result["legend"]) == 4
            if name == "site":
                assert result["site_residues"] and result["polar_contacts"] > 0
                assert cmd.count_atoms("site_residues and rep sticks") > 0


check_preservation()
check_blank_chains()
check_styling()
print("Real PyMOL preservation, blank-chain and styling checks passed")
