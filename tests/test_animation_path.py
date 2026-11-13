"""Camera-path animations: one PyMOL view per frame, validated before dispatch."""
import math

import pytest

from blender_render import animation
from blender_render.core import StyleValidationError
from blender_render.spec import resolve_options

VIEW = [1.0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, -50, 0, 0, 0, 40, 60, -20]


def turned(degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    # Column-major model-to-camera rotation about the screen y axis.
    return [c, 0, -s, 0, 1, 0, s, 0, c] + VIEW[9:]


def path_options(path, **output):
    return {'overrides': {'output': {'animation': {'mode': 'path', 'fps': 24, 'path': path}, **output}}}


def test_path_resolves_with_derived_duration_and_capability():
    path = [turned(i) for i in range(48)]
    resolved = resolve_options(path_options(path), {})
    config = resolved['style_doc']['output']['animation']
    assert config['mode'] == 'path' and config['fps'] == 24
    assert config['duration'] == pytest.approx(2.0)
    assert config['path'][5] == pytest.approx(turned(5))
    assert {'animation.camera_path', 'encoding.ffmpeg'} <= set(resolved['render_spec']['required_capabilities'])
    assert 'animation.camera_orbit' not in resolved['render_spec']['required_capabilities']
    assert {'render.mp4', 'frames.zip'} <= set(animation.required_names(resolved['style_doc']))


def test_different_paths_are_different_renders():
    a = resolve_options(path_options([turned(i) for i in range(10)]), {})
    b = resolve_options(path_options([turned(2 * i) for i in range(10)]), {})
    assert a['render_spec']['sha256'] != b['render_spec']['sha256']


@pytest.mark.parametrize('edit,match', [
    (lambda p: p[:3], 'too short'),
    (lambda p: p * 40, 'too long'),
    (lambda p: [v[:17] for v in p], 'too short'),
    (lambda p: [v[:11] + [50] + v[12:] for v in p], 'in front of the origin'),
    (lambda p: [v[:3] + [float('nan')] + v[4:] for v in p], 'finite'),
])
def test_invalid_paths_fail_before_dispatch(edit, match):
    # The schema rejects malformed shapes; normalize() rejects impossible cameras.
    with pytest.raises(StyleValidationError, match=match):
        resolve_options(path_options(edit([turned(i) for i in range(10)])), {})


def test_path_controls_do_not_mix_with_orbits():
    path = [turned(i) for i in range(10)]
    with pytest.raises(ValueError):
        animation.normalize({'mode': 'path', 'path': path, 'rock_angle': 10}, {'width': 64, 'height': 64})
    with pytest.raises(ValueError, match='only to mode=path'):
        animation.normalize({'mode': 'rock', 'path': path}, {'width': 64, 'height': 64})
    with pytest.raises(ValueError, match='duration must equal'):
        animation.normalize({'mode': 'path', 'path': path, 'fps': 24, 'duration': 3}, {'width': 64, 'height': 64})
    with pytest.raises(ValueError, match='15 seconds'):
        animation.normalize({'mode': 'path', 'path': [turned(0)] * 360, 'fps': 12}, {'width': 64, 'height': 64})


def test_states_ride_on_a_path_one_per_frame():
    path = [turned(i) for i in range(12)]
    states = [1 + i // 3 for i in range(12)]
    resolved = resolve_options(path_options(path) | {}, {})
    options = path_options(path)
    options['overrides']['output']['animation']['states'] = states
    resolved = resolve_options(options, {})
    assert resolved['style_doc']['output']['animation']['states'] == states
    assert 'animation.molecular_states' in resolved['render_spec']['required_capabilities']
    with pytest.raises(StyleValidationError, match='one PyMOL state number'):
        options['overrides']['output']['animation']['states'] = states[:-1]
        resolve_options(options, {})
    with pytest.raises(ValueError, match='only to mode=path'):
        animation.normalize({'mode': 'rock', 'states': [1, 2, 3, 4]}, {'width': 64, 'height': 64})


def test_states_reject_geometry_that_cannot_follow_atoms():
    from blender_render.presets import preset_path
    import json
    for preset, edit in (('cyan_mint_cutaway', 'ligand_cutout_radius'), ('ivory_lime_frosted', 'wireframe_width_angstrom')):
        style = json.loads(preset_path(preset).read_text())
        style['output']['animation'] = {'mode': 'path', 'fps': 24, 'path': [turned(i) for i in range(4)],
                                        'states': [1, 2, 3, 4]}
        with pytest.raises(StyleValidationError, match='cannot follow'):
            resolve_options({'style_doc': style}, {})


def payload(states):
    return {'cif_text': 'data_x\nloop_\n_atom_site.id\n1\n', 'view': {'pymol_view': VIEW},
            'state_coordinates': states}


@pytest.mark.parametrize('states,match', [
    ({'base': 1, 'coordinates': [[0, 0, 0]]}, '2 to 360'),
    ({'base': 3, 'coordinates': [[0, 0, 0], [1, 1, 1]]}, 'base'),
    ({'base': 1, 'coordinates': [[0, 0, 0], [1, 1]]}, 'same atoms'),
    ({'base': 1, 'coordinates': [[0, 0], [1, 1]]}, 'x, y and z'),
    ({'base': 1, 'coordinates': [[0, 0, 0], [1, 1, float('nan')]]}, 'finite'),
    ({'coordinates': [[0, 0, 0], [1, 1, 1]]}, 'exactly'),
])
def test_invalid_state_payloads_fail_before_render(states, match):
    from blender_render.core import PayloadValidationError, validate_payload_document
    with pytest.raises(PayloadValidationError, match=match):
        validate_payload_document(payload(states))


def test_state_payload_is_written_beside_the_structure(tmp_path, monkeypatch):
    import json
    from blender_render import core
    monkeypatch.setattr(core, '_ensure_tmp_run_dir', lambda run_id: tmp_path)
    states = {'base': 2, 'coordinates': [[0, 0, 0], [1.5, 2, 3]]}
    *_, view_path = core._materialize_payload_bundle(core.validate_payload_document(payload(states)), 'run')
    view = json.loads(view_path.read_text())
    assert json.loads(open(view['state_coordinates_file']).read()) == states


class FakeCmd:
    def __init__(self, states):
        self.states = states
    def count_states(self, selection):
        return len(self.states)
    def count_atoms(self, selection):
        return len(self.states[0])
    def get_coords(self, selection, state):
        return self.states[state - 1]
    def get_model(self, selection, state):
        from types import SimpleNamespace
        return SimpleNamespace(atom=[SimpleNamespace(index=i) for i in range(len(self.states[state - 1]))])


def test_exporter_collects_every_state_in_atom_order():
    from blender_render.scene.export import collect_state_coordinates
    result = collect_state_coordinates(FakeCmd([[[0, 0, 0], [1, 2, 3.00049]], [[1, 1, 1], [2, 2, 2]]]), 'all', 1)
    assert result == {'base': 1, 'coordinates': [[0, 0, 0, 1, 2, 3.0], [1, 1, 1, 2, 2, 2]]}
    with pytest.raises(ValueError, match='same 2 atoms'):
        collect_state_coordinates(FakeCmd([[[0, 0, 0], [1, 1, 1]], [[0, 0, 0]]]), 'all', 1)
    with pytest.raises(ValueError, match='2 to 360'):
        collect_state_coordinates(FakeCmd([[[0, 0, 0]]]), 'all', 1)


def cut_style(preset='cyan_mint_cutaway', **animation):
    import json
    from blender_render.presets import preset_path
    style = json.loads(preset_path(preset).read_text())
    style['output'].update(width=320, height=180)
    style['output']['animation'] = {'mode': 'path', 'fps': 24, 'path': [turned(i) for i in range(8)], **animation}
    return style


def test_lid_fades_a_cutout_from_an_anchor_view():
    lid = [1, 1, 1, 0.6, 0.2, 0, 0, 0]
    resolved = resolve_options({'style_doc': cut_style(anchor=5, lid=lid)}, {})
    config = resolved['style_doc']['output']['animation']
    assert config['anchor'] == 5 and config['lid'] == [float(v) for v in lid]
    assert 'animation.cutaway_lid' in resolved['render_spec']['required_capabilities']
    # Without a lid, the anchor defaults to the first view as before.
    assert resolve_options({'style_doc': cut_style()}, {})['style_doc']['output']['animation']['anchor'] == 0


@pytest.mark.parametrize('animation,match', [
    ({'anchor': 8}, 'maximum|index one of the path views'),
    ({'lid': [1, 0]}, 'too short|one opacity'),
    ({'lid': [1.5] * 8}, 'maximum|one opacity'),
])
def test_invalid_anchor_or_lid_fails_before_render(animation, match):
    with pytest.raises(StyleValidationError, match=match):
        resolve_options({'style_doc': cut_style(**animation)}, {})


def test_lid_needs_a_preset_with_a_cutout():
    with pytest.raises(StyleValidationError, match='needs a preset with a ligand cutout'):
        resolve_options({'style_doc': cut_style('mint_sky_assembly', lid=[1] * 8)}, {})
    with pytest.raises(ValueError, match='only to mode=path'):
        animation.normalize({'mode': 'rock', 'lid': [1, 1, 1, 1]}, {'width': 64, 'height': 64})


def test_state_export_rejects_different_atoms_with_equal_counts():
    from types import SimpleNamespace
    from blender_render.scene.export import collect_state_coordinates
    command = FakeCmd([[[0, 0, 0]], [[1, 1, 1]]])
    command.get_model = lambda selection, state: SimpleNamespace(atom=[SimpleNamespace(index=state)])
    with pytest.raises(ValueError, match='same 1 atoms'):
        collect_state_coordinates(command, 'all', 1)


def test_long_camera_path_roundtrips_in_frame_archive(tmp_path):
    from PIL import Image
    frames = tmp_path / 'frames'
    frames.mkdir()
    for i in range(360):
        Image.new('RGB', (2, 2), (i % 256, 0, 0)).save(frames / f'{i:04d}.png')
    config = animation.normalize({'mode': 'path', 'fps': 24, 'path': [turned(i / 7) for i in range(360)]},
                                 {'width': 2, 'height': 2})
    archive = tmp_path / 'frames.zip'
    animation.package_frames(frames, archive, config, 2, 2)
    with animation.unpack(archive) as (unpacked, metadata):
        assert metadata['animation'] == config and metadata['frames'] == 360
        assert (unpacked / '0359.png').read_bytes() == (frames / '0359.png').read_bytes()
