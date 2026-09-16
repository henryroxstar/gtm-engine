"""hero-reveal — one full-bleed, pre-composited still (concept art assembled outside this
pipeline: a mockup, a label, a character) animated as a single sliding, fading layer with an
optional in-shot highlight ring.

``still-push`` animates a reused asset by a slow push and nothing else — the one motion this
scene's brief called boring. ``phone-walkthrough`` earns its motion by re-compositing a product's
OWN screens inside a drawn phone, frame by frame; that machinery assumes a screen with a status
strip and a scrollable app view, which a already-finished hero frame is not. This scene sits
between the two: the source is never redrawn or re-composited (the whole picture already IS the
shot), but it enters and exits on the SAME edge-slide grammar ``phone-walkthrough`` uses — start
off-canvas, spring to rest, fade in step — so a shot built here and a shot built there cut
together as one continuous rhythm instead of two different directors' work. A hold is never a
bare static frame either: a small, fully deterministic drift keeps it alive between the enter and
the exit.

Deterministic: every value is a closed-form function of the frame index and the inputs — two
renders of the same inputs are byte-identical.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from ...design_tokens import stroke_px
from ..base import SceneError
from ..draw import _ease_in_out, _ease_out, _window
from ..frames import _frame_count, _write_frame_stream
from ..palette import _load_palette, _resolve_area
from . import phone_marks as marks
from .phone_chassis import blit, phone_colors

_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_ACTIONS_BYTES = 1024 * 1024
_EDGES = ("top", "bottom", "left", "right")
#: Of the frame dimension the WHOLE layer travels on enter/exit — bigger than
#: phone-walkthrough's 0.38 because that one moves a phone-sized element; this one moves
#: something canvas-sized, which needs more travel to read as leaving the frame at all.
_ENTER_TRAVEL_FRAC = 0.62
#: The hold is never perfectly still: a small, fully deterministic breathing scale + drift, zero
#: at both the moment enter settles and the moment exit begins so the handoff has no seam.
_IDLE_SCALE = 0.016
_IDLE_DRIFT_FRAC = 0.011
#: Bounds for the optional top-level ``"layout"`` — where the WHOLE still sits and how big it is,
#: independent of enter/exit travel. Defaults reduce to today's full-bleed placement exactly.
_LAYOUT_KEYS = frozenset({"scale", "center_x_frac", "center_y_frac"})
_SCALE_RANGE = (0.4, 1.0)  # exclusive lower bound


@dataclass(frozen=True)
class _Action:
    kind: str  # enter | exit | highlight
    start_s: float
    end_s: float
    edge: str = "bottom"
    rect: tuple[float, float, float, float] | None = None
    shape: str = "rect"
    dim: float = 0.38
    draw_s: float = 0.4
    fade_s: float = 0.3
    dim_rect: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class _Layout:
    scale: float = 1.0
    center_x_frac: float = 0.5
    center_y_frac: float = 0.5


def _num(value: object, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SceneError(f"{where} must be a finite number, got {value!r}")
    return float(value)


def _edge(value: object, *, where: str) -> str:
    if value not in _EDGES:
        raise SceneError(f"{where} must be one of {_EDGES}, got {value!r}")
    return str(value)


_COMMON = {"type", "start_s", "end_s"}
_KEYS = {
    "enter": _COMMON | {"from"},
    "exit": _COMMON | {"to"},
    "highlight": _COMMON | {"rect", "shape", "dim", "draw_s", "fade_s", "dim_rect"},
}


def _dim_rect(raw: object, *, where: str) -> tuple[float, float, float, float] | None:
    if raw is None:
        return None
    if not (isinstance(raw, list | tuple) and len(raw) == 4):
        raise SceneError(f"{where} must be 4 numbers [x0,y0,x1,y1]")
    x0, y0, x1, y1 = (_num(v, where=where) for v in raw)
    if not (x0 < x1 and y0 < y1):
        raise SceneError(f"{where} must satisfy x0<x1 and y0<y1, got {list(raw)}")
    return x0, y0, x1, y1


def _parse_one(raw: object, index: int, duration_s: float) -> _Action:
    where = f"actions[{index}]"
    if not isinstance(raw, dict):
        raise SceneError(f"{where} must be an object, got {type(raw).__name__}")
    kind = raw.get("type")
    if kind not in _KEYS:
        raise SceneError(f"{where}: type must be one of {sorted(_KEYS)}, got {kind!r}")
    unknown = sorted(set(raw) - _KEYS[kind])
    if unknown:
        raise SceneError(f"{where}: unknown key(s) {unknown}; {kind} accepts {sorted(_KEYS[kind])}")
    start = _num(raw.get("start_s"), where=f"{where}.start_s")
    end = _num(raw.get("end_s"), where=f"{where}.end_s")
    if not 0.0 <= start < end <= duration_s + 1e-6:
        raise SceneError(
            f"{where}: needs 0 <= start_s < end_s <= duration_s ({duration_s:g}), "
            f"got {start:g}..{end:g}"
        )
    if kind == "enter":
        return _Action(
            "enter", start, end, edge=_edge(raw.get("from", "bottom"), where=f"{where}.from")
        )
    if kind == "exit":
        return _Action("exit", start, end, edge=_edge(raw.get("to", "top"), where=f"{where}.to"))
    rect_raw = raw.get("rect")
    if not (isinstance(rect_raw, list | tuple) and len(rect_raw) == 4):
        raise SceneError(f"{where}.rect must be 4 numbers [x0,y0,x1,y1]")
    span = end - start
    return _Action(
        "highlight",
        start,
        end,
        rect=tuple(_num(v, where=f"{where}.rect") for v in rect_raw),
        shape=raw.get("shape", "rect"),
        dim=_num(raw.get("dim", 0.38), where=f"{where}.dim"),
        draw_s=_num(raw.get("draw_s", min(0.4, span * 0.4)), where=f"{where}.draw_s"),
        fade_s=_num(raw.get("fade_s", min(0.3, span * 0.25)), where=f"{where}.fade_s"),
        dim_rect=_dim_rect(raw.get("dim_rect"), where=f"{where}.dim_rect"),
    )


def _parse_layout(raw: object) -> _Layout:
    """The document's optional top-level ``"layout"``. Absent keeps today's full-bleed placement,
    byte-identical to before this key existed."""
    if raw is None:
        return _Layout()
    if not isinstance(raw, dict):
        raise SceneError(f'"layout" must be an object, got {type(raw).__name__}')
    unknown = sorted(set(raw) - _LAYOUT_KEYS)
    if unknown:
        raise SceneError(f"layout: unknown key(s) {unknown}; accepts {sorted(_LAYOUT_KEYS)}")
    scale = _num(raw.get("scale", 1.0), where="layout.scale")
    cx = _num(raw.get("center_x_frac", 0.5), where="layout.center_x_frac")
    cy = _num(raw.get("center_y_frac", 0.5), where="layout.center_y_frac")
    lo, hi = _SCALE_RANGE
    if not lo < scale <= hi:
        raise SceneError(f"layout.scale must be within ({lo:g}, {hi:g}], got {scale:g}")
    if not 0.0 <= cx <= 1.0:
        raise SceneError(f"layout.center_x_frac must be within [0, 1], got {cx:g}")
    if not 0.0 <= cy <= 1.0:
        raise SceneError(f"layout.center_y_frac must be within [0, 1], got {cy:g}")
    return _Layout(scale, cx, cy)


def _parse_actions(raw: object, *, duration_s: float) -> tuple[list[_Action], _Layout]:
    if not isinstance(raw, dict) or not isinstance(raw.get("actions"), list):
        raise SceneError('--actions must be {"version": 1, "actions": [...]}')
    unknown = sorted(set(raw) - {"version", "actions", "layout"})
    if unknown:
        raise SceneError(
            f'unknown top-level key(s) {unknown}; accepts "version", "actions", "layout"'
        )
    if raw.get("version", 1) != 1:
        raise SceneError(f'"version" must be 1 if present, got {raw["version"]!r}')
    parsed = [_parse_one(item, i, duration_s) for i, item in enumerate(raw["actions"])]
    for kind in ("enter", "exit"):
        if sum(1 for a in parsed if a.kind == kind) > 1:
            raise SceneError(f"only one {kind} action is allowed")
    layout = _parse_layout(raw.get("layout"))
    return sorted(parsed, key=lambda a: a.start_s), layout


def _load_actions(
    actions: Path | str | dict | None, *, duration_s: float
) -> tuple[list[_Action], _Layout]:
    if actions is None:
        raise SceneError("hero-reveal needs --actions: the timed action list (JSON)")
    if isinstance(actions, dict):
        return _parse_actions(actions, duration_s=duration_s)
    path = Path(actions)
    if not path.is_file():
        raise SceneError(f"--actions not found: {path}")
    if path.stat().st_size > _MAX_ACTIONS_BYTES:
        raise SceneError(f"--actions {path.name} is over {_MAX_ACTIONS_BYTES} bytes")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SceneError(f"--actions {path} is not valid JSON: {exc}") from exc
    return _parse_actions(raw, duration_s=duration_s)


def _load_image(path: Path | str | None, *, flag: str) -> Image.Image:
    if path is None:
        raise SceneError(f"hero-reveal needs {flag}: the pre-composited still")
    src = Path(path)
    if not src.is_file():
        raise SceneError(f"{flag} not found: {src}")
    if src.stat().st_size > _MAX_IMAGE_BYTES:
        raise SceneError(f"{flag} {src.name} is over {_MAX_IMAGE_BYTES} bytes")
    try:
        with Image.open(src) as im:
            return im.convert("RGB").copy()
    except (OSError, Image.DecompressionBombError) as exc:
        raise SceneError(f"{flag} {src} could not be read as an image: {exc}") from exc


def _canvas_rgb(src: Image.Image) -> tuple[int, int, int]:
    """The still's OWN background, sampled from its top-left corner — never the kit's
    ``palette.canvas``, since this scene never redraws the source and matching the kit here would
    draw a seam at every edge the source's own margin touches. Pure extraction: ``src`` must
    already be RGB (``_load_image`` flattens an RGBA still before this is ever called), so an
    alpha channel is never silently sampled as if it were a fourth colour band.

    Shared with ``screen_ui/fit_layout.py``'s content-box measurement — the fit check diffs
    against exactly this colour, by construction, rather than re-deriving its own idea of "the
    background" that could quietly drift from what this scene actually draws.
    """
    return tuple(src.getpixel((2, 2)))[:3]


# ── camera: scale + a resolved centre for the hold (idle drift); a pixel offset for enter/exit ───


def _spring(t: float) -> float:
    """A near-critically damped spring settling by ``t`` = 1: overshoot under 0.2% of travel.
    Identical to phone-walkthrough's, on purpose — the same settle shape reads as one house
    style across every scene that slides something into frame."""
    if t >= 1.0:
        return 1.0
    zeta, omega = 0.9, 8.0
    wd = omega * math.sqrt(1 - zeta * zeta)
    decay = math.exp(-zeta * omega * max(0.0, t))
    return 1.0 - decay * (math.cos(wd * t) + zeta * omega / wd * math.sin(wd * t))


def _edge_offset(edge: str, amount: float, w: int, h: int) -> tuple[float, float]:
    dx = {"left": -1.0, "right": 1.0}.get(edge, 0.0) * amount * w * _ENTER_TRAVEL_FRAC
    dy = {"top": -1.0, "bottom": 1.0}.get(edge, 0.0) * amount * h * _ENTER_TRAVEL_FRAC
    return dx, dy


def _idle(t: float, start: float, end: float) -> tuple[float, float, float]:
    """``(scale, cx_frac_delta, cy_frac_delta)`` — zero at both ``start`` and ``end`` so the hold
    hands off to enter/exit (both "at rest") with no visible seam. ``cy`` runs at double
    frequency so the drift wanders rather than bouncing straight back the way it came."""
    p = _window(t, start, end)
    scale = 1.0 + _IDLE_SCALE * math.sin(math.pi * p)
    dx = _IDLE_DRIFT_FRAC * math.sin(math.pi * p)
    dy = _IDLE_DRIFT_FRAC * 0.6 * math.sin(2 * math.pi * p)
    return scale, dx, dy


def _resize_cover(
    src: Image.Image, w: int, h: int, *, scale: float, cx_frac: float, cy_frac: float
) -> Image.Image:
    """A ``scale``-tight crop of ``src`` centred at ``(cx_frac, cy_frac)``, resized to ``(w, h)``.
    ``scale`` > 1 is a push in, exactly ``still-push``'s convention."""
    sw, sh = src.size
    cw, ch = min(w / scale, sw), min(h / scale, sh)
    cx = max(cw / 2, min(sw - cw / 2, cx_frac * sw))
    cy = max(ch / 2, min(sh - ch / 2, cy_frac * sh))
    box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    return src.resize((w, h), Image.LANCZOS, box=box)


def _draw_highlights(
    content: Image.Image, actions: list[_Action], t: float, mark_rgb: tuple, ink_rgb: tuple
) -> None:
    stroke = stroke_px("emphasis", content.height)
    for a in actions:
        if a.kind != "highlight" or not (a.start_s <= t < a.end_s):
            continue
        box = marks.ring_box(a.rect, a.shape, content.width * 0.022)
        radius = min((box[3] - box[1]) / 2, content.width * 0.045)
        drawn = _ease_out(_window(t, a.start_s, a.start_s + a.draw_s))
        fade = 1.0 - _ease_in_out(_window(t, a.end_s - a.fade_s, a.end_s))
        # `dim_rect` is confined here rather than in `phone_marks.dim_outside` (shared with
        # phone-walkthrough, which has no such key): dim everywhere as before, then put back the
        # UNDIMMED pixel wherever it fell outside the caller's rect.
        before = content.copy() if a.dim_rect is not None else None
        marks.dim_outside(
            content, box, shape=a.shape, radius=radius, alpha=a.dim * drawn * fade, rgb=ink_rgb
        )
        if before is not None:
            revert = Image.new("L", content.size, 255)
            ImageDraw.Draw(revert).rectangle(a.dim_rect, fill=0)
            content.paste(before, (0, 0), revert)
        marks.draw_ring(
            content,
            box,
            shape=a.shape,
            radius=radius,
            progress=drawn,
            alpha=fade,
            stroke=stroke,
            rgb=mark_rgb,
        )


def _frame(
    actions: list[_Action],
    layout: _Layout,
    t: float,
    src: Image.Image,
    w: int,
    h: int,
    duration_s: float,
    canvas_rgb: tuple,
    mark_rgb: tuple,
    ink_rgb: tuple,
) -> Image.Image:
    canvas = Image.new("RGB", (w, h), canvas_rgb)
    enter = next((a for a in actions if a.kind == "enter"), None)
    exit_ = next((a for a in actions if a.kind == "exit"), None)
    if enter and t < enter.start_s:
        return canvas
    if exit_ and t >= exit_.end_s:
        return canvas

    active_highlights = [a for a in actions if a.kind == "highlight"]
    content = src
    if any(a.start_s <= t < a.end_s for a in active_highlights):
        content = src.copy()
        _draw_highlights(content, active_highlights, t, mark_rgb, ink_rgb)

    # The still's OWN framed size and position — `layout.scale`/`center_*_frac` at their defaults
    # (1.0, 0.5, 0.5) collapse `sw, sh` to `(w, h)` and `base_x, base_y` to exactly `(0.0, 0.0)`
    # (the `- 0.5` terms are then literal zeros), so every pixel below is unchanged from before
    # this key existed.
    sw = max(1, round(w * layout.scale))
    sh = max(1, round(h * layout.scale))
    base_x = layout.center_x_frac * w - sw / 2
    base_y = layout.center_y_frac * h - sh / 2

    if enter and enter.start_s <= t < enter.end_s:
        wl = _window(t, enter.start_s, enter.end_s)
        dx, dy = _edge_offset(enter.edge, 1.0 - _spring(wl), w, h)
        layer = content if content.size == (sw, sh) else content.resize((sw, sh), Image.LANCZOS)
        opacity = _ease_out(_window(wl, 0.0, 0.6))
    elif exit_ and exit_.start_s <= t < exit_.end_s:
        wl = _window(t, exit_.start_s, exit_.end_s)
        dx, dy = _edge_offset(exit_.edge, 1.0 - _spring(1.0 - wl), w, h)
        layer = content if content.size == (sw, sh) else content.resize((sw, sh), Image.LANCZOS)
        opacity = 1.0 - _ease_in_out(_window(wl, 0.25, 1.0))
    else:
        hold_start = enter.end_s if enter else 0.0
        hold_end = exit_.start_s if exit_ else duration_s
        scale, dxf, dyf = _idle(t, hold_start, hold_end)
        # The idle zoom/drift is a crop at the CONTENT's own native resolution — cropping
        # straight into (sw, sh) would conflate "how much of the picture is visible" with
        # "how big the picture is on the frame", so a `layout.scale` < 1 (sw, sh smaller than
        # the frame) silently zoomed IN instead of shrinking the whole picture (the still's own
        # edges — the pill, the mascot — ran off the smaller box instead of fitting inside it).
        zoomed = _resize_cover(
            content,
            content.width,
            content.height,
            scale=scale,
            cx_frac=0.5 + dxf,
            cy_frac=0.5 + dyf,
        )
        layer = zoomed if zoomed.size == (sw, sh) else zoomed.resize((sw, sh), Image.LANCZOS)
        dx, dy, opacity = 0.0, 0.0, 1.0

    if opacity <= 0:
        return canvas
    result = canvas.copy()
    blit(result, layer, round(base_x + dx), round(base_y + dy))
    return result if opacity >= 1.0 else Image.blend(canvas, result, opacity)


def render_hero_reveal_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    image: Path | None = None,
    actions: Path | dict | None = None,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Render the hero still as numbered PNGs, one enter + optional highlights + one exit."""
    area = _resolve_area(ratio)
    palette = _load_palette(kit)
    colors = phone_colors(kit, palette)
    w, h = area.width, area.height
    n_frames = _frame_count(fps, duration_s)
    src = _load_image(image, flag="--image")
    plan, layout = _load_actions(actions, duration_s=duration_s)

    canvas_rgb = _canvas_rgb(src)

    def frames() -> Iterator[Image.Image]:
        for i in range(n_frames):
            yield _frame(
                plan, layout, i / fps, src, w, h, duration_s, canvas_rgb, colors.mark, colors.ink
            )

    return _write_frame_stream(frames(), out_dir, prefix="hero-reveal")
