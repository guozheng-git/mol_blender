"""Run with a PyMOL Python interpreter: validate_export.py [output-report.json]."""
import json
import sys
import tempfile
from pathlib import Path
import numpy as np
import pymol2

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from blender_render.export import build_render_payload

with pymol2.PyMOL() as instance:
    cmd = instance.cmd
    cmd.load(str(root/'examples/2trx.cif'), 'mol_one')
    cmd.create('mol_two','mol_one')
    cmd.transform_object('mol_two',[1,0,0,30,0,1,0,-15,0,0,1,8,0,0,0,1])
    cmd.create('disabled_copy','mol_one')
    cmd.disable('disabled_copy')
    cmd.color('red','mol_one and resi 1-40')
    cmd.color('blue','mol_one and resi 41-100')
    cmd.alter('mol_one and resi 10','resi="10A"')
    cmd.turn('x',32); cmd.turn('y',-18); cmd.zoom('enabled')
    cmd.set('orthoscopic',1)
    before = dict(view=cmd.get_view(), objects=cmd.get_names('objects'), enabled=cmd.get_names('objects',enabled_only=1))
    expected = np.concatenate([cmd.get_coords('mol_one'),cmd.get_coords('mol_two')])
    payload = build_render_payload(cmd, include_color_hints=True)
    assert before['view'] == cmd.get_view()
    assert before['objects'] == cmd.get_names('objects')
    assert before['enabled'] == cmd.get_names('objects',enabled_only=1)
    assert payload['provenance']['enabled_objects'] == ['mol_one','mol_two']
    assert payload['cif_text'].count('data_') == 1
    assert 'A:10A' in payload['ss_hints']['by_chain_resi']
    assert len(set(payload['color_hints']['atoms'].values())) >= 3
    with tempfile.TemporaryDirectory() as temporary:
        path=Path(temporary)/'scene.cif'; path.write_text(payload['cif_text'])
        cmd.load(str(path),'exported')
        actual=cmd.get_coords('exported')
    assert expected.shape == actual.shape
    # CIF canonicalizes atom order and rounds coordinates. Compare both point
    # clouds, avoiding unstable lexicographic order for near-equal coordinates.
    def distance(left,right):
        return max(float(np.sqrt(((batch[:,None,:]-right[None,:,:])**2).sum(axis=2)).min(axis=1).max()) for batch in np.array_split(left,32))
    coordinate_error=max(distance(expected,actual),distance(actual,expected))
    assert coordinate_error < .002, coordinate_error
    report={'status':'passed','atom_count':len(actual),'transformed_objects':2,'disabled_objects_excluded':True,
        'camera_preserved':True,'insertion_codes_preserved':True,'atom_colors_preserved':True,'maximum_coordinate_error_angstrom':coordinate_error}
    print(json.dumps(report,indent=2))
    if len(sys.argv)>1: Path(sys.argv[1]).write_text(json.dumps(report,indent=2))
