"""The ``phone-walkthrough`` action list: parse, validate, and read back the screen state at a time.

Pure — no pixels. Everything here is in SCREENSHOT pixel space (the coordinates a person measures
on the PNG they hand over), so an action list survives a change of delivery ratio untouched.

FAIL CLOSED. A walkthrough is a sequence somebody timed against a voice-over, and the defects it
can carry are all silent in a render: a typo'd key that drops a parameter, a ring drawn around a
rect below the fold, a highlight that outlives the screen it was measured on, a four-second hold
that `video_lint` V9 would only flag after encoding. Each of those is refused here, by name, before
a frame is drawn.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..base import SceneError
from ..draw import _ease_in_out, _lerp, _window

ACTION_TYPES = ("enter", "exit", "scroll", "zoom", "highlight", "tap", "swap")

#: The longest stretch with nothing moving. `video_lint` V13's spirit at shot scale: a held phone
#: reads as a screenshot slide the moment it stops, which is the note that retired `still-push`.
MAX_IDLE_S = 2.5

#: Screen width over screen height for the one chassis this scene draws (a current tall phone,
#: ~19.5:9). A constant, not a per-video choice: two chassis in one film was the defect.
SCREEN_ASPECT = 0.46

#: The status-bar band a screenshot WITHOUT one sits under, as a fraction of the screen height.
STATUS_STRIP_FRAC = 0.055

#: How much of the screen may be flat fill (status strip + bottom fill) before a screenshot is
#: refused as not phone-shaped. A landscape capture letterboxed into a phone is a different shot.
_MAX_FILL_FRAC = 0.12

_COMMON_KEYS = frozenset({"type", "start_s", "end_s"})
_KEYS = {
    "enter": frozenset({"from"}),
    "exit": frozenset({"to"}),
    "scroll": frozenset({"from_y", "to_y"}),
    "zoom": frozenset({"rect", "scale", "ease_s"}),
    "highlight": frozenset({"rect", "shape", "dim", "draw_s"}),
    "tap": frozenset({"point"}),
    "swap": frozenset({"screen", "direction"}),
}
_REQUIRED = {
    "scroll": frozenset({"to_y"}),
    "zoom": frozenset({"rect"}),
    "highlight": frozenset({"rect"}),
    "tap": frozenset({"point"}),
    "swap": frozenset({"screen"}),
}
_EDGES = ("bottom", "top", "left", "right")

#: Actions that may not overlap one another. `camera` owns the phone's position and scale,
#: `content` owns what the screen shows, `focus` owns the dim.
_EXCLUSIVE = {
    "camera": frozenset({"enter", "exit", "zoom"}),
    "content": frozenset({"scroll", "swap"}),
    "focus": frozenset({"highlight"}),
}
#: Actions measured against ONE screen's pixels, so they cannot straddle a swap.
_SCREEN_BOUND = frozenset({"zoom", "highlight", "tap", "scroll"})


@dataclass(frozen=True)
class ScreenFit:
    """How one screenshot sits in the screen, in its own pixels."""

    width: int
    height: int
    view_h: float  # screen height expressed in this screenshot's pixels (width-fit)
    strip: float  # status band above a screenshot that carries none
    max_scroll: float


@dataclass(frozen=True)
class Action:
    kind: str
    start_s: float
    end_s: float
    index: int
    screen: int = 0  # swap: the target; every other kind: the screen active when it runs
    from_screen: int = 0
    rect: tuple[float, float, float, float] | None = None
    point: tuple[float, float] | None = None
    from_y: float | None = None
    to_y: float | None = None
    scale: float | None = None
    ease_s: float = 0.0
    shape: str = "rect"
    dim: float = 0.38
    draw_s: float = 0.0
    fade_s: float = 0.0
    edge: str = "bottom"

    @property
    def label(self) -> str:
        return f"actions[{self.index}] ({self.kind})"


def fit_screen(width: int, height: int) -> ScreenFit:
    """Width-fit a screenshot to the screen. Refuses one too short to read as a phone screen."""
    if width <= 0 or height <= 0:
        raise SceneError(f"screenshot has no pixels ({width}x{height})")
    view_h = width / SCREEN_ASPECT
    shortfall = max(0.0, view_h - height)
    if shortfall > view_h * _MAX_FILL_FRAC:
        raise SceneError(
            f"screenshot {width}x{height} fills only {height / view_h:.0%} of a phone screen "
            f"(needs >= {1 - _MAX_FILL_FRAC:.0%}); pass a portrait phone capture"
        )
    strip = min(shortfall, view_h * STATUS_STRIP_FRAC)
    return ScreenFit(width, height, view_h, strip, max(0.0, height - view_h))


def view_at(actions: Sequence[Action], t: float) -> list[tuple[int, float, float]]:
    """``[(screen, scroll_y, x_shift)]`` on screen at ``t`` — two entries mid-swap, else one.

    ``x_shift`` is a fraction of the screen width. A swap lands on the TOP of its target.
    """
    screen, pos = 0, 0.0
    for a in actions:
        if a.start_s > t:
            break
        e = _ease_in_out(_window(t, a.start_s, a.end_s))
        if a.kind == "scroll":
            pos = _lerp(a.from_y, a.to_y, e)
        elif a.kind == "swap":
            if t < a.end_s:
                sign = -1.0 if a.edge == "left" else 1.0
                return [(screen, pos, sign * e), (a.screen, 0.0, sign * (e - 1.0))]
            screen, pos = a.screen, 0.0
    return [(screen, pos, 0.0)]


def motion_spans(a: Action) -> list[tuple[float, float]]:
    """The parts of an action's window in which something visibly changes."""
    if a.kind == "zoom":
        return [(a.start_s, a.start_s + a.ease_s), (a.end_s - a.ease_s, a.end_s)]
    if a.kind == "highlight":
        return [(a.start_s, a.start_s + a.draw_s), (a.end_s - a.fade_s, a.end_s)]
    return [(a.start_s, a.end_s)]


# ── parsing ─────────────────────────────────────────────────────────────────────────────────────


def _num(value: object, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SceneError(f"{where} must be a finite number, got {value!r}")
    return float(value)


def _nums(value: object, n: int, *, where: str) -> tuple[float, ...]:
    if not isinstance(value, list | tuple) or len(value) != n:
        raise SceneError(f"{where} must be a list of {n} numbers, got {value!r}")
    return tuple(_num(v, where=where) for v in value)


def _choice(value: object, options: Sequence[str], *, where: str) -> str:
    if value not in options:
        raise SceneError(f"{where} must be one of {list(options)}, got {value!r}")
    return str(value)


def _bounded(value: float, lo: float, hi: float, *, where: str) -> float:
    if not lo <= value <= hi:
        raise SceneError(f"{where} must be within [{lo:g}, {hi:g}], got {value:g}")
    return value


def _check_keys(raw: dict, kind: str, where: str) -> None:
    allowed = _COMMON_KEYS | _KEYS[kind]
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SceneError(f"{where}: unknown key(s) {unknown}; {kind} accepts {sorted(allowed)}")
    missing = sorted(({"start_s", "end_s"} | _REQUIRED.get(kind, frozenset())) - set(raw))
    if missing:
        raise SceneError(f"{where}: missing required key(s) {missing}")


def _kind_fields(raw: dict, kind: str, span: float, where: str) -> dict:
    """The per-kind parameters, typed and range-checked, with their defaults resolved."""
    if kind in ("enter", "exit"):
        key = "from" if kind == "enter" else "to"
        return {"edge": _choice(raw.get(key, "bottom"), _EDGES, where=f"{where}.{key}")}
    if kind == "scroll":
        from_y = raw.get("from_y")
        return {
            "to_y": _num(raw["to_y"], where=f"{where}.to_y"),
            "from_y": None if from_y is None else _num(from_y, where=f"{where}.from_y"),
        }
    if kind == "tap":
        return {"point": _nums(raw["point"], 2, where=f"{where}.point")}
    if kind == "swap":
        screen = raw["screen"]
        if isinstance(screen, bool) or not isinstance(screen, int):
            raise SceneError(f"{where}.screen must be an integer screen index, got {screen!r}")
        return {
            "screen": screen,
            "edge": _choice(
                raw.get("direction", "left"), ("left", "right"), where=f"{where}.direction"
            ),
        }
    fields: dict = {"rect": _nums(raw["rect"], 4, where=f"{where}.rect")}
    if kind == "zoom":
        ease = _num(raw.get("ease_s", min(0.7, 0.4 * span)), where=f"{where}.ease_s")
        fields["ease_s"] = _bounded(ease, 0.05, span / 2, where=f"{where}.ease_s")
        if "scale" in raw:
            fields["scale"] = _bounded(
                _num(raw["scale"], where=f"{where}.scale"), 1.05, 3.0, where=f"{where}.scale"
            )
        return fields
    draw = _num(raw.get("draw_s", min(0.6, 0.4 * span)), where=f"{where}.draw_s")
    fields["draw_s"] = _bounded(draw, 0.05, span * 0.75, where=f"{where}.draw_s")
    fields["fade_s"] = min(0.35, 0.25 * span)
    fields["shape"] = _choice(raw.get("shape", "rect"), ("rect", "circle"), where=f"{where}.shape")
    fields["dim"] = _bounded(
        _num(raw.get("dim", 0.38), where=f"{where}.dim"), 0.0, 0.85, where=f"{where}.dim"
    )
    return fields


def _parse_one(raw: object, index: int, duration_s: float) -> Action:
    where = f"actions[{index}]"
    if not isinstance(raw, dict):
        raise SceneError(f"{where} must be an object, got {type(raw).__name__}")
    kind = raw.get("type")
    if kind not in _KEYS:
        raise SceneError(
            f"{where}: unknown action type {kind!r}; expected one of {list(ACTION_TYPES)}"
        )
    _check_keys(raw, kind, where)
    start = _num(raw["start_s"], where=f"{where}.start_s")
    end = _num(raw["end_s"], where=f"{where}.end_s")
    if not 0.0 <= start < end <= duration_s + 1e-6:
        raise SceneError(
            f"{where}: needs 0 <= start_s < end_s <= duration_s ({duration_s:g}), "
            f"got {start:g}..{end:g}"
        )
    return Action(kind, start, end, index, **_kind_fields(raw, kind, end - start, where))


# ── cross-action rules ──────────────────────────────────────────────────────────────────────────


def _overlaps(a: Action, b: Action) -> bool:
    return a.start_s < b.end_s and b.start_s < a.end_s


def _check_exclusive(actions: Sequence[Action]) -> None:
    for i, a in enumerate(actions):
        for b in actions[i + 1 :]:
            if not _overlaps(a, b):
                continue
            for group, kinds in _EXCLUSIVE.items():
                if a.kind in kinds and b.kind in kinds:
                    raise SceneError(
                        f"{a.label} {a.start_s:g}..{a.end_s:g}s overlaps {b.label} "
                        f"{b.start_s:g}..{b.end_s:g}s; only one {group} action may run at a time"
                    )
            if {a.kind, b.kind} & {"swap"} and {a.kind, b.kind} & _SCREEN_BOUND:
                raise SceneError(
                    f"{a.label} overlaps {b.label}: a {'/'.join(sorted(_SCREEN_BOUND))} is "
                    "measured on one screen and cannot straddle a swap"
                )


def _check_enter_exit(actions: Sequence[Action]) -> None:
    for kind in ("enter", "exit"):
        found = [a for a in actions if a.kind == kind]
        if len(found) > 1:
            raise SceneError(f"{found[1].label}: at most one {kind} per walkthrough")
    enter = next((a for a in actions if a.kind == "enter"), None)
    exit_ = next((a for a in actions if a.kind == "exit"), None)
    for a in actions:
        if enter is not None and a is not enter and a.start_s < enter.start_s:
            raise SceneError(f"{a.label} starts at {a.start_s:g}s, before the phone enters")
        if exit_ is not None and a is not exit_ and a.end_s > exit_.end_s:
            raise SceneError(f"{a.label} ends at {a.end_s:g}s, after the phone has exited")
    if enter is not None and exit_ is not None and exit_.start_s < enter.end_s:
        raise SceneError(f"{exit_.label} starts before {enter.label} finishes")


def _check_visible(a: Action, fit: ScreenFit, scroll_y: float, y0: float, y1: float) -> None:
    top = scroll_y - fit.strip
    if y0 < top or y1 > top + fit.view_h:
        raise SceneError(
            f"{a.label}: y {y0:g}..{y1:g} is off screen at {a.start_s:g}s (the screen shows "
            f"y {max(0.0, top):g}..{min(fit.height, top + fit.view_h):g}); scroll to it first"
        )


def _check_target(a: Action, fit: ScreenFit, resolved: Sequence[Action]) -> None:
    w, h = fit.width, fit.height
    if a.rect is not None:
        x0, y0, x1, y1 = a.rect
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise SceneError(
                f"{a.label}: rect {list(a.rect)} must satisfy 0<=x0<x1<={w} and 0<=y0<y1<={h} "
                f"(screen {a.screen} is {w}x{h})"
            )
    else:
        x0, y0 = a.point
        y1 = y0
        if not (0 <= x0 <= w and 0 <= y0 <= h):
            raise SceneError(
                f"{a.label}: point {list(a.point)} lies outside screen {a.screen} ({w}x{h})"
            )
    _check_visible(a, fit, view_at(resolved, a.start_s)[0][1], y0, y1)


def _resolve_scroll(a: Action, fit: ScreenFit, pos: float) -> Action:
    from_y = pos if a.from_y is None else a.from_y
    if abs(from_y - pos) > 0.5:
        raise SceneError(
            f"{a.label}: from_y {from_y:g} but screen {a.screen} is at {pos:g} when it starts; "
            "omit from_y to continue from where it is"
        )
    if fit.max_scroll <= 0:
        raise SceneError(
            f"{a.label}: screen {a.screen} is not taller than the phone; nothing to scroll"
        )
    if not 0 <= a.to_y <= fit.max_scroll or a.to_y == from_y:
        raise SceneError(
            f"{a.label}: to_y must differ from {from_y:g} and lie in [0, {fit.max_scroll:g}], "
            f"got {a.to_y:g}"
        )
    return replace(a, from_y=from_y)


def _resolve_screens(actions: Sequence[Action], fits: Sequence[ScreenFit]) -> tuple[Action, ...]:
    """Bind every action to the screen it runs on, and check its target against that screen."""
    resolved: list[Action] = []
    screen, pos = 0, 0.0
    for a in actions:
        if a.kind == "swap":
            if not 0 <= a.screen < len(fits) or a.screen == screen:
                raise SceneError(
                    f"{a.label}: screen {a.screen} must be another of the {len(fits)} screens "
                    f"(0 = --image, 1.. = each --still in order), not the one showing ({screen})"
                )
            a = replace(a, from_screen=screen)
            screen, pos = a.screen, 0.0
        else:
            a = replace(a, screen=screen)
            if a.kind == "scroll":
                a = _resolve_scroll(a, fits[screen], pos)
                pos = a.to_y
            elif a.kind in _SCREEN_BOUND:
                _check_target(a, fits[screen], resolved)
        resolved.append(a)
    return tuple(resolved)


def _check_idle(actions: Sequence[Action], duration_s: float) -> None:
    spans = sorted(s for a in actions for s in motion_spans(a))
    cursor = 0.0
    for lo, hi in [*spans, (duration_s, duration_s)]:
        if lo - cursor > MAX_IDLE_S + 1e-6:
            raise SceneError(
                f"nothing moves between {cursor:g}s and {lo:g}s ({lo - cursor:.2f}s > "
                f"{MAX_IDLE_S:g}s); add an action there or shorten the shot"
            )
        cursor = max(cursor, hi)


def parse_actions(
    doc: object, *, screens: Sequence[tuple[int, int]], duration_s: float
) -> tuple[Action, ...]:
    """Validate an action document against the screens it will run on. Returns time order."""
    if not isinstance(doc, dict) or not isinstance(doc.get("actions"), list):
        raise SceneError('the actions document must be an object with an "actions" list')
    unknown = sorted(set(doc) - {"actions", "version", "layout"})
    if unknown or doc.get("version", 1) != 1:
        raise SceneError(
            f'the actions document accepts only "actions", "version": 1 and "layout", '
            f"got {sorted(doc)}"
        )
    fits = [fit_screen(w, h) for w, h in screens]
    parsed = sorted(
        (_parse_one(raw, i, duration_s) for i, raw in enumerate(doc["actions"])),
        key=lambda a: (a.start_s, a.index),
    )
    _check_exclusive(parsed)
    _check_enter_exit(parsed)
    resolved = _resolve_screens(parsed, fits)
    _check_idle(resolved, duration_s)
    return resolved
