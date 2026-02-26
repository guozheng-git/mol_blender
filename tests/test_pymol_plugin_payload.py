from __future__ import annotations

from pymol_plugin_bridge.pymol_bridge_plugin import build_render_payload


class FakeCmd:
    def get_str(self, fmt, selection, state=-1):
        assert selection == "(all)"
        assert state == -1
        if fmt == "cif":
            return "\n".join(
                [
                    "data_test",
                    "loop_",
                    "_atom_site.group_PDB",
                    "_atom_site.id",
                    "ATOM 1",
                ]
            )
        if fmt == "pdb":
            return "ATOM      1  N   GLY A   1      11.104  13.207  10.207  1.00 20.00           N"
        raise KeyError(fmt)

    def get_view(self, quiet=1):
        assert quiet == 1
        return [
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            -50.0,
            0.0,
            0.0,
            0.0,
            2.0,
            120.0,
            50.0,
        ]

    def get(self, key):
        if key == "field_of_view":
            return "50"
        if key == "orthoscopic":
            return "0"
        raise KeyError(key)

    def get_viewport(self):
        return (1600, 900)


def test_build_render_payload_contains_cif_and_view_fields():
    payload = build_render_payload(FakeCmd(), "(all)")

    assert isinstance(payload["cif_text"], str)
    assert payload["cif_text"].strip()
    assert isinstance(payload["pdb_text"], str)
    assert payload["pdb_text"].strip()

    view = payload["view"]
    assert "pymol_view" in view
    assert len(view["pymol_view"]) == 18
    assert view["fov_deg"] == 50.0
    assert view["orthoscopic"] is False
    assert view["viewport_px"] == [1600, 900]
