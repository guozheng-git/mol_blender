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

View = tuple[float, ...]


@dataclass(frozen=True)
class Frame:
    view: View
    state: int = 0  # 0 leaves the session's current state
    fade: float = 0.0  # transparency of the cutaway shell; 0 is opaque


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
    # Near a half-turn the antisymmetric terms vanish. Derive the smaller
    # components from the largest one instead, retaining mixed-axis signs.
    trace = sum(m[i][i] for i in range(3))
    if trace > 0:
        scale = 2 * math.sqrt(1 + trace)
        q = [scale / 4, (m[2][1] - m[1][2]) / scale,
             (m[0][2] - m[2][0]) / scale, (m[1][0] - m[0][1]) / scale]
    else:
        i = max(range(3), key=lambda index: m[index][index])
        j, k = (i + 1) % 3, (i + 2) % 3
        scale = 2 * math.sqrt(1 + m[i][i] - m[j][j] - m[k][k])
        q = [(m[k][j] - m[j][k]) / scale, 0., 0., 0.]
        q[i + 1] = scale / 4
        q[j + 1] = (m[j][i] + m[i][j]) / scale
        q[k + 1] = (m[k][i] + m[i][k]) / scale
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
    dot = sum(i * j for i, j in zip(qa, qb, strict=True))
    if dot < 0:
        qb, dot = tuple(-i for i in qb), -dot
    angle = math.acos(min(1.0, dot))
    if angle < 1e-6:
        q = qa
    else:
        wa = math.sin((1 - t) * angle) / math.sin(angle)
        wb = math.sin(t * angle) / math.sin(angle)
        q = tuple(wa * i + wb * j for i, j in zip(qa, qb, strict=True))
    rest = tuple((1 - t) * x + t * y for x, y in zip(a[9:17], b[9:17], strict=True))
    return _with_rotation(a, _matrix(q))[:9] + rest + (a[17],)


# Segments. Each keeps state and fade from the previous frame unless it sets them.


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


def fade(view: View, seconds: float, to: float) -> Segment:
    def frame(t: float, last: Frame) -> Frame:
        return replace(last, view=view, fade=to * smooth(t))

    return seconds, frame


def play_states(view: View, seconds: float, states: int, degrees: float) -> Segment:
    def frame(t: float, last: Frame) -> Frame:
        return Frame(
            view=turn(view, degrees * math.sin(2 * math.pi * t)),
            # Exact frame/state boundaries can round just below an integer
            # (e.g. 13/360*360). Avoid repeating one state and skipping the next.
            state=1 + min(states - 1, math.floor(t * states + 1e-9)),
            fade=last.fade,
        )

    return seconds, frame


def compile_frames(segments: list[Segment], fps: int) -> list[Frame]:
    """Sample every segment at fps; each one starts where the last one ended."""
    frames: list[Frame] = []
    last: Frame | None = None
    for seconds, segment in segments:
        count = max(1, round(seconds * fps))
        if last is None:
            last = segment(0.0, Frame(view=()))
        frames.extend(segment(i / count, last) for i in range(count))
        last = segment(1.0, last)
    return frames


# Recipes. Every number here is a default worth tuning against previews.


def turntable_recipe(keys: Keys) -> list[Segment]:
    return [spin(keys.overview, 10)]


def rock_recipe(keys: Keys) -> list[Segment]:
    return [rock(keys.overview, 6, 25)]


def reveal_recipe(keys: Keys) -> list[Segment]:
    focus = _require_focus(keys)
    return [
        hold(keys.overview, 1.5),
        move(keys.overview, focus, 3),
        hold(focus, 1),
        rock(focus, 4, 12),
    ]


CUTAWAY_FADE = 0.8  # keep a ghost of the shell so the pocket stays in context


def cutaway_recipe(keys: Keys) -> list[Segment]:
    """Overview with the site buried, push in, fade the shell in front, rock."""
    focus = _require_focus(keys)
    return [
        hold(keys.overview, 1.5),
        move(keys.overview, focus, 3),
        hold(focus, 0.5),
        fade(focus, 2, CUTAWAY_FADE),
        hold(focus, 0.5),
        rock(focus, 4, 12),
    ]


def states_recipe(keys: Keys) -> list[Segment]:
    if keys.states < 2:
        raise ValueError("The states recipe needs a structure with several states")
    seconds = min(15.0, max(4.0, keys.states * 0.25))
    return [play_states(keys.overview, seconds, keys.states, 10)]


def scenes_recipe(keys: Keys) -> list[Segment]:
    if len(keys.scenes) < 2:
        raise ValueError("The scenes recipe needs at least two saved scenes")
    segments = [hold(keys.scenes[0], 1.5)]
    for a, b in zip(keys.scenes, keys.scenes[1:], strict=False):
        segments += [move(a, b, 2.5), hold(b, 1.5)]
    return segments


def _require_focus(keys: Keys) -> View:
    if keys.focus is None:
        raise ValueError("This recipe needs a focus region, e.g. a ligand")
    return keys.focus


RECIPES: dict[str, tuple[Callable[[Keys], list[Segment]], str]] = {
    "turntable": (turntable_recipe, "Full turn in 10s at constant speed; loops."),
    "rock": (rock_recipe, "Swing ±25° over 6s; loops. Keeps the front in view."),
    "reveal": (
        reveal_recipe,
        "Overview, ease in to look down into the focus pocket, then rock gently.",
    ),
    "cutaway": (
        cutaway_recipe,
        "Overview with the site buried, push in, fade the surface in front of "
        "it to a ghost, then rock gently.",
    ),
    "states": (states_recipe, "Play through all states while rocking ±10°."),
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


def frames_for(recipe: str, keys: Keys, fps: int = 24) -> list[Frame]:
    if recipe not in RECIPES:
        raise ValueError(f"Unknown recipe {recipe!r}; choose from {', '.join(RECIPES)}")
    return compile_frames(RECIPES[recipe][0](keys), fps)
