"""Rebuild the public pose/color fixture with a PyMOL Python interpreter.

Run from any directory: python examples/make_session.py
The only molecular input is the adjacent RCSB 1SPS.cif structure.
"""
import json
from pathlib import Path
import pymol2


def main():
    root = Path(__file__).resolve().parent
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        cmd.load(str(root / "1sps.cif"), "src_sh2")
        cmd.remove("not (chain A or chain D)")
        cmd.remove("solvent")
        cmd.dss("all")
        cmd.hide("everything")
        cmd.show("cartoon", "polymer")
        cmd.show("sticks", "resn PTR")
        cmd.set_color("paint_sage", [0.48, 0.65, 0.40])
        cmd.set_color("paint_helix", [0.10, 0.40, 0.24])
        cmd.set_color("paint_sheet", [0.65, 0.75, 0.27])
        cmd.set_color("paint_ligand", [0.82, 0.55, 0.18])
        cmd.color("paint_sage", "all")
        cmd.color("paint_helix", "ss H")
        cmd.color("paint_sheet", "ss S")
        cmd.color("paint_ligand", "resn PTR")
        cmd.orient("all")
        cmd.turn("x", 18)
        cmd.turn("y", -25)
        cmd.zoom("all", buffer=3)
        cmd.set("orthoscopic", 1)
        cmd.set("field_of_view", 20)
        cmd.save(str(root / "1sps_pose.pse"))
        (root / "1sps_pose.json").write_text(json.dumps({
            "source": "https://www.rcsb.org/structure/1SPS",
            "selection": "chain A or chain D; solvent removed",
            "pymol_version": cmd.get_version()[0],
            "view": list(cmd.get_view()), "orthoscopic": True,
            "field_of_view": 20,
            "atom_count": cmd.count_atoms("all"),
            "ptr_atoms": cmd.count_atoms("resn PTR"),
        }, indent=2) + "\n")


if __name__ == "__main__":
    main()
