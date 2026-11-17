"""Optional executed PyMOL export check against a real Blender Render checkout."""

from __future__ import annotations

import gemmi
import pytest

from co_pymol.scene.capture import capture_pse, capture_scene
from co_pymol.scene.session import pymol_lock


def test_live_coordinates_colors_enabled_objects_and_camera_survive_export(tmp_path):
    pymol2 = pytest.importorskip("pymol2")
    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        cmd.fragment("ala", "one")
        cmd.create("two", "one")
        cmd.create("hidden", "one")
        cmd.translate([12, 0, 0], "two")
        cmd.color("red", "one")
        cmd.color("blue", "two")
        cmd.disable("hidden")
        cmd.orient("one or two")
        cmd.alter("one", "ss='H'")
        cmd.select("saved_selection", "one and name CA")
        cmd.scene("saved_view", "store")
        cmd.create("two", "two", 1, 2)
        cmd.translate([0, 3, 0], "two", state=2)
        original_session_file = str(tmp_path / "user-session.pse")
        cmd.set("session_file", original_session_file)

        def atoms():
            records = []
            cmd.iterate_state(
                1,
                "all",
                "records.append((model,index,x,y,z,ss,color))",
                space={"records": records},
            )
            return records

        before = atoms()
        second_state = cmd.get_coords("two", state=2)
        view = cmd.get_view()
        with pymol_lock:
            pse = capture_pse(cmd)
            payload = capture_scene(cmd, "all", -1)
        after = atoms()
        assert before == after
        assert cmd.get_view() == view
        assert cmd.get("session_file") == original_session_file
        assert set(cmd.get_names("objects")) == {"one", "two", "hidden"}
        assert payload["provenance"]["enabled_objects"] == ["one", "two"]
        assert payload["structure_summary"]["atom_count"] == 20
        assert payload["view"]["pymol_view"] == list(view)
        assert set(payload["color_hints"]["chain"].values()) == {"#FF0000", "#0000FF"}
        assert "cif_text" in payload and payload["ss_hints"]
        block = gemmi.cif.read_string(payload["cif_text"]).sole_block()
        exported_coords = sorted(
            zip(
                *[
                    [float(v) for v in block.find_values("_atom_site.Cartn_" + axis)]
                    for axis in ("x", "y", "z")
                ],
                strict=True,
            )
        )
        expected_coords = sorted(map(tuple, cmd.get_coords("one or two")))
        assert len(exported_coords) == len(expected_coords)
        for exported, expected in zip(exported_coords, expected_coords, strict=True):
            assert exported == pytest.approx(expected, abs=0.001)
        frozen = payload["cif_text"]
        cmd.translate([100, 0, 0], "one")
        assert payload["cif_text"] == frozen

    # Reopen in an independent PyMOL, after the original session has changed
    # and closed. This checks the actual PSE format, not a mocked serializer.
    path = tmp_path / "scene.pse"
    path.write_bytes(pse)
    with pymol2.PyMOL() as restored:
        cmd = restored.cmd
        cmd.load(str(path))
        assert atoms() == before
        assert cmd.get_view() == view
        assert set(cmd.get_names("objects")) == {"one", "two", "hidden"}
        assert set(cmd.get_names("objects", enabled_only=1)) == {"one", "two"}
        assert cmd.count_states("two") == 2
        assert "saved_selection" in cmd.get_names("selections")
        assert cmd.count_atoms("saved_selection") == 1
        assert cmd.get_scene_list() == ["saved_view"]
        assert cmd.get_coords("two", state=2) == pytest.approx(second_state)


def test_bridge_export_keeps_hand_annotated_secondary_structure():
    """bridge_render exports a single live object; DSS must not overwrite its ss."""
    pymol2 = pytest.importorskip("pymol2")
    from blender_render.scene.export import build_render_payload

    with pymol2.PyMOL() as instance:
        cmd = instance.cmd
        # DSS assigns this built helix 'H'; the user has annotated it as loop.
        cmd.fab("AAAAAAAAAAAAAAAA", "helix", ss=1)
        cmd.alter("all", "ss='L'")
        view = cmd.get_view()
        payload = build_render_payload(cmd, "(all)", -1, include_color_hints=True)
        annotated = set()
        cmd.iterate("all", "annotated.add(ss)", space={"annotated": annotated})
        assert annotated == {"L"}
        # The frozen export still carries the computed helix.
        assert payload["ss_hints"]["by_chain_resi"][":8"] == "H"
        assert cmd.get_view() == view
        assert payload["dss"]["ran"] is True
