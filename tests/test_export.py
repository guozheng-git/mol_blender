"""Camera, DSS, and color export contracts without starting a UI."""
import pytest
from blender_render.export import build_render_payload, _collect_color_hints, _collect_secondary_structure_hints


class Scene:
    def __init__(self):
        self.selections = []
        self.atoms = [dict(chain='a', resi='10A', resn='ALA', name='CA', ss='H', color=1, type='ATOM'),
                      dict(chain='a', resi='10B', resn='GLY', name='CA', ss='S', color=2, type='ATOM')]
    def dss(self, selection): self.selections.append(selection)
    def get_str(self, fmt, selection, state=-1):
        if fmt == 'cif': return 'data_export\nloop_\n_atom_site.group_PDB\n_atom_site.id\nATOM 1\n'
        return 'ATOM      1  CA  ALA a  10A     1.000   2.000   3.000  1.00 20.00           C'
    def iterate(self, selection, expression, space):
        for atom in self.atoms: exec(expression, {}, dict(space, **atom))
    def get_color_tuple(self, index): return (1,0,0) if index == 1 else (0,0,1)
    def get_view(self, quiet=1): return [1,0,0,0,1,0,0,0,1,0,0,-40,2,3,4,1,100,1]
    def get_viewport(self): return (1600,900)
    def get(self, key): return '1' if key == 'orthoscopic' else '30'


def test_cif_first_dss_enabled_and_exact_camera():
    scene = Scene()
    data = build_render_payload(scene, '(all)', include_color_hints=True)
    assert data['cif_text'].startswith('data_') and data['pdb_text'].startswith('ATOM')
    assert scene.selections == ['((all)) and enabled']
    assert data['dss']['ran']
    assert data['view'] == dict(pymol_view=scene.get_view(), viewport_px=[1600,900], fov_deg=30, orthoscopic=True)
    assert data['ss_hints']['by_chain_resi'] == {'a:10A':'H', 'a:10B':'S'}
    assert data['color_hints']['atoms'] == {'a:10A:ALA:CA':'#FF0000','a:10B:GLY:CA':'#0000FF'}


def test_export_failure_is_explicit():
    scene = Scene()
    def fail(_): raise RuntimeError('assignment unavailable')
    scene.dss = fail
    with pytest.raises(RuntimeError, match='Secondary-structure assignment failed'):
        build_render_payload(scene)


def test_empty_cif_uses_explicit_pdb_fallback():
    scene = Scene()
    original = scene.get_str
    scene.get_str = lambda fmt, *a, **kw: '' if fmt == 'cif' else original(fmt,*a,**kw)
    data = build_render_payload(scene)
    assert 'cif_text' not in data and data['pdb_text'].startswith('ATOM')
