import numpy as np
import pytest
from blender_render.sequence_palette import sequence_colors


def test_sequence_order_handles_insertions_and_separate_chains():
    colors, mixed = sequence_colors(['A','A','A','A','B'], ['10','10','10A','12','1'],
        ['C','O','C','C','P'], {'A':['#000000','#FFFFFF'],'B':['#00FF00','#00FF00']},
        {'O':'#FF0000','P':'#FF8000'})
    assert np.allclose(colors[0], [0,0,0,1])
    assert np.allclose(colors[1], colors[0])
    assert np.allclose(colors[2,:3], [.21404114]*3)
    assert np.allclose(colors[3], [1,1,1,1])
    assert np.allclose(colors[4], [0,1,0,1])
    assert np.allclose(mixed[1], [1,0,0,1])
    assert np.allclose(mixed[2], colors[2])
    assert mixed[4,0] == 1 and mixed[4,2] == 0


def test_unknown_chain_requires_explicit_fallback():
    with pytest.raises(ValueError, match='No sequence palette'):
        sequence_colors(['C'],['1'],['C'],{'A':['#000000','#FFFFFF']},{})
    colors, _ = sequence_colors(['C'],['1'],['C'],{'*':['#000000','#FFFFFF']},{})
    assert np.allclose(colors[0], [0,0,0,1])
