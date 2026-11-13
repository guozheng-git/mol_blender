# SPDX-License-Identifier: MIT
# Adapted from co-pymol; Copyright (c) 2026 Soo-Jeong Kim.
"""Animation recipes: keyframe timelines compiled to one setting per frame.

Pure and stdlib-only — no PyMOL or numpy, so the CLI can list recipes
without them. The PyMOL layer supplies the key views (the session's own
camera, a view of the focus region, saved scenes) and renders the frames.

A PyMOL view is 18 floats: a column-major model-to-camera rotation (0-8), the
camera offset (9-11), the rotation origin (12-14), the front and back clipping
planes (15-16) and the orthoscopic flag (17).

Defaults follow common molecular-animation practice: slow motion (a full turn
takes about ten seconds), eased starts and stops except in seamless loops,
holds on the moments that matter, and rotation about the screen's vertical
axis through the model's origin.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

from co_pymol.defaults import ANIMATION, ANIMATION_LIMITS

RECIPE_DEFAULTS = ANIMATION["recipes"]

View = tuple[float, ...]


@dataclass(frozen=True)
class Frame:
    view: View
    state: int = 0  # 0 leaves the session's current state
    lid_opacity: float = 1.0  # cutaway lid: 1 is closed, 0 is fully removed


@dataclass(frozen=True)
class Keys:
    """Views and counts measured from the session by the PyMOL layer."""

    overview: View
    focus: View | None = None  # framing the focus region, e.g. a ligand pocket
    scenes: tuple[View, ...] = ()
    states: int = 1


# A segment lasts some seconds and maps progress t in [0, 1) plus the last
# frame of the previous segment to the frame at t.
Segment = tuple[float, Callable[[float, Frame], Frame]]


def smooth(t: float) -> float:
    """Ease in and out: zero speed and acceleration at both ends."""
    return t * t * t * (t * (6 * t - 15) + 10)


Matrix = list[list[float]]


def _rotation(view: View) -> Matrix:
    # Column-major storage: view[3 * c + r] is row r, column c.
    return [[view[3 * c + r] for c in range(3)] for r in range(3)]


def _with_rotation(view: View, m: Matrix) -> View:
    return tuple(float(m[r][c]) for c in range(3) for r in range(3)) + tuple(view[9:])


def _product(a: Matrix, b: Matrix) -> Matrix:
    return [
        [sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)
    ]


def turn(view: View, degrees: float, axis: str = "y") -> View:
    """Rotate the camera about a screen axis through the origin (PyMOL `turn`)."""
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    matrix = {
        "x": [[1, 0, 0], [0, c, -s], [0, s, c]],
        "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
        "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]],
    }[axis]
    return _with_rotation(view, _product(matrix, _rotation(view)))


def _quaternion(m: Matrix) -> tuple[float, float, float, float]:
    """Convert a rotation without losing axis signs at or near a half-turn."""
    trace = sum(m[i][i] for i in range(3))
    if trace > 0:
        scale = 2 * math.sqrt(1 + trace)
        q = (scale / 4, (m[2][1] - m[1][2]) / scale,
             (m[0][2] - m[2][0]) / scale, (m[1][0] - m[0][1]) / scale)
    else:
        # Use the largest vector component as the denominator when w is small.
        i = max(range(3), key=lambda index: m[index][index])
        j, k = (i + 1) % 3, (i + 2) % 3
        scale = 2 * math.sqrt(1 + m[i][i] - m[j][j] - m[k][k])
        vector = [0.0, 0.0, 0.0]
        vector[i] = scale / 4
        vector[j] = (m[i][j] + m[j][i]) / scale
        vector[k] = (m[i][k] + m[k][i]) / scale
        q = ((m[k][j] - m[j][k]) / scale, *vector)
    norm = math.sqrt(sum(value * value for value in q))
    return tuple(value / norm for value in q)


def _matrix(q: tuple[float, ...]) -> Matrix:
    w, x, y, z = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def blend(a: View, b: View, t: float) -> View:
    """Interpolate two views: rotation along the shortest arc, the rest linearly."""
    qa, qb = _quaternion(_rotation(a)), _quaternion(_rotation(b))
    dot = sum(i * j for i, j in zip(qa, qb))
    if dot < 0:
        qb, dot = tuple(-i for i in qb), -dot
    angle = math.acos(min(1.0, dot))
    if angle < 1e-6:
        q = qa
    else:
        wa = math.sin((1 - t) * angle) / math.sin(angle)
        wb = math.sin(t * angle) / math.sin(angle)
        q = tuple(wa * i + wb * j for i, j in zip(qa, qb))
    rest = tuple((1 - t) * x + t * y for x, y in zip(a[9:17], b[9:17]))
    return _with_rotation(a, _matrix(q))[:9] + rest + (a[17],)


# Segments keep state and lid opacity from the previous frame unless they set them.


def hold(view: View, seconds: float) -> Segment:
    return seconds, lambda t, last: replace(last, view=view)


def move(a: View, b: View, seconds: float) -> Segment:
    return seconds, lambda t, last: replace(last, view=blend(a, b, smooth(t)))


def spin(view: View, seconds: float, degrees: float = 360) -> Segment:
    # Constant speed: a full turn then loops without a visible seam.
    return seconds, lambda t, last: replace(last, view=turn(view, degrees * t))


def rock(view: View, seconds: float, degrees: float) -> Segment:
    # One sine cycle starting and ending on `view`, so it also loops.
    return seconds, lambda t, last: replace(
        last, view=turn(view, degrees * math.sin(2 * math.pi * t))
    )


def fade_out(view: View, seconds: float) -> Segment:
    def frame(t: float, last: Frame) -> Frame:
        return replace(last, view=view, lid_opacity=last.lid_opacity * (1 - smooth(t)))

    return seconds, frame


def play_states(view: View, seconds: float, states: int, degrees: float) -> Segment:
    def frame(t: float, last: Frame) -> Frame:
        return Frame(
            view=turn(view, degrees * math.sin(2 * math.pi * t)),
            # Protect exact state boundaries from floating-point rounding.
            state=1 + min(states - 1, math.floor(t * states + 1e-9)),
            lid_opacity=last.lid_opacity,
        )

    return seconds, frame


def compile_frames(segments: list[Segment], fps: int) -> list[Frame]:
    """Sample every segment at fps; each one starts where the last one ended."""
    frames: list[Frame] = []
    last: Frame | None = None
    elapsed = 0.0
    for seconds, segment in segments:
        elapsed += seconds
        count = round(elapsed * fps) - len(frames)
        if last is None:
            last = segment(0.0, Frame(view=()))
        frames.extend(segment(i / count, last) for i in range(count))
        last = segment(1.0, last)
    return frames


# Recipe timings and angles come from render_defaults.yaml.


def turntable_recipe(keys: Keys) -> list[Segment]:
    return [spin(keys.overview, RECIPE_DEFAULTS["turntable"]["duration"])]


def rock_recipe(keys: Keys) -> list[Segment]:
    config = RECIPE_DEFAULTS["rock"]
    return [rock(keys.overview, config["duration"], config["rock_angle"])]


def reveal_recipe(keys: Keys) -> list[Segment]:
    config = RECIPE_DEFAULTS["reveal"]
    focus = _require_focus(keys)
    return [
        hold(keys.overview, config["overview_hold"]),
        move(keys.overview, focus, config["approach"]),
        hold(focus, config["focus_hold"]),
        rock(focus, config["rock_duration"], config["rock_angle"]),
    ]


def cutaway_recipe(keys: Keys) -> list[Segment]:
    """Overview with the site buried, push in, fade the shell in front, rock."""
    config = RECIPE_DEFAULTS["cutaway"]
    focus = _require_focus(keys)
    return [
        hold(keys.overview, config["overview_hold"]),
        move(keys.overview, focus, config["approach"]),
        hold(focus, config["before_fade_hold"]),
        fade_out(focus, config["fade_duration"]),
        hold(focus, config["after_fade_hold"]),
        rock(focus, config["rock_duration"], config["rock_angle"]),
    ]


def states_recipe(keys: Keys) -> list[Segment]:
    if keys.states < 2:
        raise ValueError("The states recipe needs a structure with several states")
    config = RECIPE_DEFAULTS["states"]
    seconds = min(ANIMATION_LIMITS["max_duration"], max(config["min_duration"], keys.states * config["seconds_per_state"]))
    return [play_states(keys.overview, seconds, keys.states, config["rock_angle"])]


def scenes_recipe(keys: Keys) -> list[Segment]:
    if len(keys.scenes) < 2:
        raise ValueError("The scenes recipe needs at least two saved scenes")
    config = RECIPE_DEFAULTS["scenes"]
    segments = [hold(keys.scenes[0], config["hold_duration"])]
    for a, b in zip(keys.scenes, keys.scenes[1:]):
        segments += [move(a, b, config["transition_duration"]), hold(b, config["hold_duration"])]
    return segments


def _require_focus(keys: Keys) -> View:
    if keys.focus is None:
        raise ValueError("This recipe needs a focus region, e.g. a ligand")
    return keys.focus


RECIPES: dict[str, tuple[Callable[[Keys], list[Segment]], str]] = {
    "turntable": (turntable_recipe, f"Full turn in {RECIPE_DEFAULTS['turntable']['duration']:g}s at constant speed; loops."),
    "rock": (rock_recipe, f"Swing ±{RECIPE_DEFAULTS['rock']['rock_angle']:g}° over {RECIPE_DEFAULTS['rock']['duration']:g}s; loops. Keeps the front in view."),
    "reveal": (
        reveal_recipe,
        "Overview, ease in to look down into the focus pocket, then rock gently.",
    ),
    "cutaway": (
        cutaway_recipe,
        "Overview with the site buried, push in, fade away the surface covering "
        "the pocket, then rock gently.",
    ),
    "states": (states_recipe, f"Play through all states while rocking ±{RECIPE_DEFAULTS['states']['rock_angle']:g}°."),
    "scenes": (scenes_recipe, "Fly between saved scenes, holding at each."),
}


def choose_recipe(*, scenes: int, states: int, has_focus: bool) -> str:
    """Default when the user names no recipe: the most specific content wins."""
    if scenes >= 2:
        return "scenes"
    if states >= 2:
        return "states"
    if has_focus:
        return "reveal"
    return "turntable"


def frames_for(recipe: str, keys: Keys, fps: int = ANIMATION["fps"], *, duration=None, rock_angle=None) -> list[Frame]:
    if recipe not in RECIPES:
        raise ValueError(f"Unknown recipe {recipe!r}; choose from {', '.join(RECIPES)}")
    segments = RECIPES[recipe][0](keys)
    if rock_angle is not None:
        if recipe != "rock" or type(rock_angle) not in (int, float) or not math.isfinite(rock_angle) or not 0 < rock_angle <= ANIMATION_LIMITS["max_rock_angle"]:
            raise ValueError(f"rock_angle requires the rock recipe and an angle in (0, {ANIMATION_LIMITS['max_rock_angle']}]")
        segments = [rock(keys.overview, RECIPE_DEFAULTS["rock"]["duration"], rock_angle)]
    seconds = sum(s for s, _ in segments)
    duration = seconds if duration is None else duration
    if type(fps) is not int or not 1 <= fps <= ANIMATION_LIMITS["max_fps"]:
        raise ValueError(f"fps must be an integer from 1 to {ANIMATION_LIMITS['max_fps']}")
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= ANIMATION_LIMITS["max_duration"]:
        raise ValueError(f"duration must be positive and at most {ANIMATION_LIMITS['max_duration']} seconds; shorten long scene tours explicitly")
    count = duration * fps
    if not ANIMATION_LIMITS["min_frames"] <= count <= ANIMATION_LIMITS["max_frames"] or not math.isclose(count, round(count), abs_tol=1e-8):
        raise ValueError(f"duration × fps must be an integer from {ANIMATION_LIMITS['min_frames']} to {ANIMATION_LIMITS['max_frames']}")
    frames = compile_frames([(s * duration / seconds, f) for s, f in segments], fps)
    if recipe == "states" and {frame.state for frame in frames} != set(range(1, keys.states + 1)):
        raise ValueError("The movie is too short to show every state; increase duration or FPS")
    return frames


def template(value: dict) -> dict:
    """Validate a portable recipe template; no paths, executable code or renderer settings."""
    if not isinstance(value, dict) or set(value) - {"version", "recipe", "fps", "gif", "duration", "rock_angle", "focus"}:
        raise ValueError("Unsupported animation template fields")
    if type(value.get("version")) is not int or value["version"] != 1 or value.get("recipe") not in RECIPES:
        raise ValueError("Animation templates need version=1 and a known recipe")
    result = {"fps": ANIMATION["fps"], "gif": ANIMATION["gif"], **value}
    if result["recipe"] == "rock":
        result.setdefault("rock_angle", RECIPE_DEFAULTS["rock"]["rock_angle"])
    if type(result["fps"]) is not int or not 1 <= result["fps"] <= ANIMATION_LIMITS["max_fps"]:
        raise ValueError(f"fps must be an integer from 1 to {ANIMATION_LIMITS['max_fps']}")
    if type(result["gif"]) is not bool:
        raise ValueError("gif must be a boolean")
    for key in ("duration", "rock_angle"):
        if key in result and (type(result[key]) not in (int, float) or not math.isfinite(result[key]) or result[key] <= 0):
            raise ValueError(key + " must be a positive finite number")
    if "duration" in result and result["duration"] > ANIMATION_LIMITS["max_duration"]:
        raise ValueError(f"duration must be at most {ANIMATION_LIMITS['max_duration']} seconds")
    if "rock_angle" in result and (result["recipe"] != "rock" or result["rock_angle"] > ANIMATION_LIMITS["max_rock_angle"]):
        raise ValueError(f"rock_angle requires the rock recipe and an angle in (0, {ANIMATION_LIMITS['max_rock_angle']}]")
    if "focus" in result and (result["recipe"] not in {"reveal", "cutaway"} or not isinstance(result["focus"], str) or not result["focus"].strip()):
        raise ValueError("focus must be a nonempty PyMOL selection for reveal or cutaway")
    return result


def animation_payload(recipe: str, frames: list[Frame], fps: int, *, gif: bool = ANIMATION["gif"]) -> dict:
    """Translate a compiled recipe into the renderer's existing camera-path input."""
    animation = {"mode": "path", "path": [[float(v) for v in frame.view] for frame in frames],
                 "fps": fps, "gif": gif}
    if recipe == "cutaway":
        lid = [round(frame.lid_opacity, 6) for frame in frames]
        try:
            anchor = lid.index(0)
        except ValueError:
            raise ValueError("The movie is too short to open the cutaway; increase duration or FPS") from None
        animation.update(lid=lid, anchor=anchor)
    if recipe == "states":
        animation["states"] = [frame.state for frame in frames]
    return animation
