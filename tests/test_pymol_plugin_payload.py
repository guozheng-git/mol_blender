from __future__ import annotations

from blender_render.pymol_bridge_plugin import build_render_payload


class FakeCmd:
    def __init__(self, *, dss_error: str | None = None):
        self._dss_error = dss_error
        self.dss_calls: list[str] = []

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

    def dss(self, selection):
        self.dss_calls.append(str(selection))
        if self._dss_error is not None:
            raise RuntimeError(self._dss_error)

    def iterate(self, selection, expression, space):
        assert selection == "(all)"
        records = space["records"]
        records.append(("A", "17", "H", 0))
        records.append(("A", "18", "H", 0))
        records.append(("A", "19", "S", 0))
        records.append(("A", "20", "L", 0))
        records.append(("A", "21", "", 0))
        records.append(("A", "22", "H", 1))


def test_build_render_payload_contains_cif_and_view_fields():
    fake = FakeCmd()
    payload = build_render_payload(fake, "(all)")

    assert isinstance(payload["cif_text"], str)
    assert payload["cif_text"].strip()
    assert isinstance(payload["pdb_text"], str)
    assert payload["pdb_text"].strip()
    assert payload["dss"]["ran"] is True
    assert payload["dss"]["selection"] == "(all)"
    assert fake.dss_calls == ["(all)"]
    assert payload["ss_hints"]["source"] == "pymol_dss"
    assert payload["ss_hints"]["by_chain_resi"]["A:17"] == "H"
    assert payload["ss_hints"]["by_chain_resi"]["A:19"] == "S"
    assert payload["ss_hints"]["by_chain_resi"]["A:20"] == "L"

    view = payload["view"]
    assert "pymol_view" in view
    assert len(view["pymol_view"]) == 18
    assert view["fov_deg"] == 50.0
    assert view["orthoscopic"] is False
    assert view["viewport_px"] == [1600, 900]


def test_build_render_payload_fails_when_dss_assignment_fails():
    fake = FakeCmd(dss_error="dss failed")
    try:
        build_render_payload(fake, "(all)")
    except RuntimeError as exc:
        message = str(exc)
        assert "Secondary-structure assignment failed before export" in message
        assert "dss failed" in message
    else:  # pragma: no cover - defensive branch
        raise AssertionError("Expected RuntimeError when dss fails")
