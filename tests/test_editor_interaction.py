"""Control semantics without bpy. Real desktop acceptance is separate."""
from contextlib import nullcontext
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import pytest
import blender_render
from blender_render import editor


def interaction(monkeypatch, bpy):
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(blender_render, 'scene_capture', NS(), raising=False)
    spec = importlib.util.spec_from_file_location('blender_render._interaction_test',
                                                 Path(editor.__file__).with_name('editor_interaction.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_playback_is_idempotent_and_scoped_to_selected_scene(monkeypatch):
    chosen, other = NS(frame_current=12, frame_subframe=.25), object()
    def set_frame(frame, *, subframe):
        chosen.frame_current, chosen.frame_subframe = frame, subframe
    chosen.frame_set = set_frame
    window = NS(scene=chosen, screen=NS(is_animation_playing=False))
    second = NS(scene=chosen, screen=NS(is_animation_playing=False))
    unrelated = NS(scene=other, screen=NS(is_animation_playing=True))
    calls = []
    def play():
        calls.append('play'); window.screen.is_animation_playing = True
    def pause(**kwargs):
        assert kwargs == {'restore_frame': False}
        calls.append('pause'); window.screen.is_animation_playing = False
        chosen.frame_current, chosen.frame_subframe = 1, 0
    bpy = NS(context=NS(mode='OBJECT', window_manager=NS(windows=[window, second, unrelated]),
                        temp_override=lambda **kw: nullcontext()),
             app=NS(is_job_running=lambda _: False),
             ops=NS(screen=NS(animation_play=play, animation_cancel=pause)))
    module = interaction(monkeypatch, bpy)
    monkeypatch.setattr(blender_render, 'editor_controls', NS(inspect=lambda scene: {'playing': window.screen.is_animation_playing}), raising=False)
    assert not module.playback(chosen, 'pause')['playback']['changed']
    with pytest.raises(ValueError, match='Another scene'):
        module.playback(chosen, 'play')
    assert not calls and unrelated.screen.is_animation_playing
    unrelated.screen.is_animation_playing = False
    for state in ('play', 'play', 'pause', 'pause'): module.playback(chosen, state)
    assert calls == ['play', 'pause']
    assert (chosen.frame_current, chosen.frame_subframe) == (12, .25)
    with pytest.raises(ValueError): module.playback(chosen, 'toggle')
    bpy.context.mode = 'EDIT_MESH'
    with pytest.raises(ValueError, match='edit mode'): module.playback(chosen, 'play')


def test_animation_speed_rejects_ambiguity_and_nonperiod_controls(monkeypatch):
    module = interaction(monkeypatch, NS())
    available = {'animation/loop/protein/motion/period_frames': [NS(value=lambda: 96)],
                 'materials/blue/roughness': [NS(value=lambda: .8)]}
    edits = []
    controls = NS(metadata=lambda: {}, controls=lambda *a: available,
                  edit=lambda scene, changes: (edits.append(changes) or {}, []))
    monkeypatch.setattr(blender_render, 'editor_controls', controls, raising=False)
    result, _ = module.animation_speed('scene', '', 2)
    assert edits == [[{'control': 'animation/loop/protein/motion/period_frames', 'value': 48}]]
    assert result['animation_speed']['period_after'] == 48
    with pytest.raises(ValueError, match='Unsupported'): module.animation_speed('scene', 'materials/blue/roughness', 2)
    available['animation/loop/other/motion/period_frames'] = [NS(value=lambda: 48)]
    with pytest.raises(ValueError, match='Select one'): module.animation_speed('scene', '', 2)
    available.clear()
    with pytest.raises(ValueError, match='0 supported'): module.animation_speed('scene', '', 2)
    assert len(edits) == 1
