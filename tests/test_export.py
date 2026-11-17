"""Camera, DSS, and color export contracts without starting a UI."""
import pytest
from blender_render.scene.export import build_render_payload, _collect_color_hints, _collect_secondary_structure_hints


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


def test_modified_hetatm_secondary_structure_survives_export():
    scene = Scene()
    scene.atoms += [dict(chain='B', resi=str(i), resn=name, name='CA', ss='H',
                         color=1, type='HETATM')
                    for i, name in enumerate(('PTR', 'TPO', 'SEP', 'UNL'), 11)]
    hints = _collect_secondary_structure_hints(scene, 'all')['by_chain_resi']
    assert all(hints[f'B:{i}'] == 'H' for i in range(11, 14))
    assert 'B:14' not in hints


def test_empty_cif_uses_explicit_pdb_fallback():
    scene = Scene()
    original = scene.get_str
    scene.get_str = lambda fmt, *a, **kw: '' if fmt == 'cif' else original(fmt,*a,**kw)
    data = build_render_payload(scene)
    assert 'cif_text' not in data and data['pdb_text'].startswith('ATOM')


@pytest.mark.parametrize('fail', [False, True])
def test_modified_residue_dss_restores_identity_even_on_failure(fail):
    from blender_render.scene.export import _run_dss_assignment
    class Modified:
        atoms = [dict(model='one',index=1,resn='PTR'),dict(model='two',index=1,resn='SEP'),dict(model='two',index=2,resn='TPO')]
        def iterate(self, selection, expression, space):
            for atom in self.atoms: exec(expression, {}, dict(space, **atom))
        def alter(self, selection, expression, space):
            for atom in self.atoms:
                env=dict(space, **atom);exec(expression,{},env);atom['resn']=env['resn']
        def dss(self, selection):
            assert [a['resn'] for a in self.atoms] == ['TYR','SER','THR']
            if fail: raise RuntimeError('DSS failed')
    scene=Modified();result=_run_dss_assignment(scene,'enabled')
    assert result['ran'] is not fail
    assert [a['resn'] for a in scene.atoms] == ['PTR','SEP','TPO']


def test_polar_contacts_are_deduplicated_and_interchain_only():
    from blender_render.scene.export import _collect_polar_contacts
    class Contacts:
        def count_atoms(self, selection): return 1
        def iterate(self, selection, expression, space):
            space['atoms'].update({('m',1):('B','11','PTR','O1P'),('m',2):('A','12','ARG','NE'),('m',3):('B','12','GLY','N')})
        def find_pairs(self, a,b,**kw):
            assert kw==dict(state1=1,state2=1,cutoff=3.6,mode=1,angle=45)
            return [(('m',1),('m',2)),(('m',2),('m',1)),(('m',1),('m',3))]
    assert _collect_polar_contacts(Contacts(),'enabled',1)==[['A:12:ARG:NE','B:11:PTR:O1P']]


def test_polar_contact_payload_bounds_and_staging(tmp_path):
    from blender_render.core import validate_payload_document, PayloadValidationError
    payload=build_render_payload(Scene())
    payload['polar_contacts']=[['A:12:ARG:NE','B:11:PTR:O1P']]
    validate_payload_document(payload)
    payload['polar_contacts']*=257
    with pytest.raises(PayloadValidationError,match='256'):
        validate_payload_document(payload)
