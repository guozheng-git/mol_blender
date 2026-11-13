"""Real PyMOL recipe export checks; called by the opt-in integration test."""
from pathlib import Path
import sys
import numpy as np
import pymol2

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from animation_session import prepare
from co_pymol.utils.pymol.animate import keys_for
from co_pymol.core.animation import template

with pymol2.PyMOL() as instance:
    cmd = instance.cmd
    cmd.load(sys.argv[3], 'molecule')
    cmd.remove('solvent')
    cmd.remove("not alt ''+A")
    cmd.alter('all', "alt=''")
    cmd.dss('all')
    for state in range(2, 4):
        cmd.create('molecule', 'molecule', 1, state)
        cmd.translate([2 * (state - 1), 0, 0], 'molecule and resi 20-40', state=state)
    cmd.fragment('gly', 'hidden')
    cmd.disable('hidden')
    cmd.select('chosen', 'molecule and resi 20')
    cmd.color('orange', 'molecule')
    cmd.translate([5, 7, 2], object='molecule', camera=0)
    cmd.orient('enabled')
    cmd.viewport(640, 480)
    cmd.scene('first', 'store')
    cmd.turn('y', 40)
    cmd.scene('second', 'store')
    cmd.color('blue', 'molecule')
    original_view = cmd.get_view()
    original_coords = cmd.get_coords('enabled').copy()
    original_matrix = cmd.get_object_matrix('molecule')
    keys = keys_for(cmd, 'scenes', None)
    assert len(keys.scenes) == 2 and cmd.get_view() == original_view
    assert cmd.get_names('objects', enabled_only=1) == ['molecule']
    colors = []
    cmd.iterate('molecule', 'colors.append(color)', space={'colors': colors})
    assert len(set(colors)) == 1 and colors[0] == cmd.get_color_index('blue')
    assert cmd.count_atoms('chosen') > 0
    for recipe in ('turntable', 'rock', 'reveal', 'cutaway', 'states', 'scenes'):
        cmd.set_view(original_view)
        config = template({'version': 1, 'recipe': recipe, 'duration': 2, 'fps': 8,
                           **({'focus': 'resn BTN'} if recipe in ('reveal', 'cutaway') else {})})
        payload, overrides, summary = prepare(cmd, config, width=128, height=96)
        assert summary['frames'] == 16
        assert payload['dss']['ran']
        assert cmd.get_names('objects', enabled_only=1) == ['molecule']
        assert cmd.get_object_matrix('molecule') == original_matrix
        if recipe == 'states':
            assert len(payload['state_coordinates']['coordinates']) == 3
            assert overrides['output']['animation']['states'][-1] == 3
        if recipe == 'cutaway':
            assert summary['anchor'] > 0
            opacity = overrides['output']['animation']['lid']
            assert opacity[0] == 1 and opacity[summary['anchor']] == 0
            assert opacity == sorted(opacity, reverse=True)
        assert np.allclose(cmd.get_coords('enabled', state=1), original_coords)
    # Saved sessions can pin an object to a state independent of the global one.
    cmd.set('state', 3)
    hidden_state = cmd.get_object_state('hidden')
    for pinned_state in (2, 3):
        cmd.set('state', pinned_state, 'molecule')
        config = template({'version': 1, 'recipe': 'states', 'duration': 1, 'fps': 4})
        payload, overrides, summary = prepare(cmd, config, width=128, height=96)
        animation = overrides['output']['animation']
        assert payload['state_coordinates']['base'] == animation['states'][summary['anchor']] == 1
        assert payload['cif_text'] == cmd.get_str('cif', '(all) and enabled', state=1)
        coordinates = np.array(payload['state_coordinates']['coordinates'][0]).reshape(-1, 3)
        assert np.allclose(coordinates, original_coords, atol=0.00051)
        assert cmd.get_object_state('hidden') == hidden_state
    # Camera-only recipes must keep an existing object's state.
    cmd.set('state', 2, 'molecule')
    payload, _, _ = prepare(cmd, template({'version': 1, 'recipe': 'rock', 'duration': 1, 'fps': 4}),
                            width=128, height=96)
    assert payload['provenance']['object_states']['molecule'] == 2
    try:
        prepare(cmd, template({'version': 1, 'recipe': 'reveal', 'focus': 'resn MISSING'}))
    except ValueError as exc:
        assert 'no enabled atoms' in str(exc)
    else:
        raise AssertionError('Missing focus was accepted')
print('All six recipes exported through real PyMOL; pinned states, scenes and transforms verified')
