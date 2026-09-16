"""phone-walkthrough — a product's own screenshots, inside one phone, driven by a timed action list.

``still-push`` made a real screenshot move, but only by a slow push, and a walkthrough built from
pushes reads as a slideshow. This scene is the template for a product walkthrough instead: one
consistent phone on the kit's ground, and a declarative list of timed, eased moves — enter, scroll,
zoom, highlight, tap, swap, exit — that a script can cut against a voice-over.

CONTENT BOUNDARY. Unlike the fictional mockup scenes, this one exists to show a product's REAL
screens as that product — the use the product-screenshot index permits. The pixels are never
redrawn: a screenshot is only scaled and translated, and the scene draws no text of its own
(captions are burned at finish time).

Deterministic: every value is a closed-form function of the frame index and the inputs, so two
renders of the same inputs are byte-identical.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Sequence
from pathlib import Path

from PIL import Image, ImageStat

from ...design_tokens import stroke_px
from ..base import SceneError
from ..draw import _ease_in_out, _ease_out, _lerp, _window
from ..frames import _frame_count, _write_frame_stream
from ..palette import _load_palette, _resolve_area
from . import phone_marks as marks
from .phone_actions import Action, ScreenFit, fit_screen, parse_actions, view_at
from .phone_chassis import (
    PhoneColors,
    PhoneGeometry,
    Placed,
    blit,
    button_margin,
    chassis,
    parse_layout,
    phone_colors,
    phone_geometry,
    placed,
    screen_mask,
    shadow,
)

_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_ACTIONS_BYTES = 1024 * 1024
#: Where a zoomed region's centre is carried to, as frame fractions — above the caption band.
_ZOOM_FOCUS = (0.5, 0.42)
_ENTER_TRAVEL_FRAC = 0.38  # of the frame dimension the phone travels along


class _Screen:
    """One source screenshot, prepared once: pixels, fit, and the colour its edges continue in."""

    def __init__(self, img: Image.Image) -> None:
        self.img = img
        self.fit: ScreenFit = fit_screen(*img.size)
        w, h = img.size
        self.top_rgb = tuple(round(v) for v in ImageStat.Stat(img.crop((0, 0, w, min(h, 4)))).mean)
        self.bottom_rgb = tuple(
            round(v) for v in ImageStat.Stat(img.crop((0, max(0, h - 4), w, h))).mean
        )


def _load_screen(path: Path | str, *, flag: str) -> _Screen:
    src = Path(path)
    if not src.is_file():
        raise SceneError(f"{flag} not found: {src}")
    if src.stat().st_size > _MAX_IMAGE_BYTES:
        raise SceneError(f"{flag} {src.name} is over {_MAX_IMAGE_BYTES} bytes")
    try:
        with Image.open(src) as im:
            return _Screen(im.convert("RGB"))
    except (OSError, Image.DecompressionBombError) as exc:
        raise SceneError(f"{flag} {src} could not be read as an image: {exc}") from exc


def _load_actions(actions: Path | str | dict | None) -> object:
    if actions is None:
        raise SceneError("phone-walkthrough needs --actions: the timed action list (JSON)")
    if isinstance(actions, dict):
        return actions
    path = Path(actions)
    if not path.is_file():
        raise SceneError(f"--actions not found: {path}")
    if path.stat().st_size > _MAX_ACTIONS_BYTES:
        raise SceneError(f"--actions {path.name} is over {_MAX_ACTIONS_BYTES} bytes")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SceneError(f"--actions {path} is not valid JSON: {exc}") from exc


# ── camera ──────────────────────────────────────────────────────────────────────────────────────


def _spring(t: float) -> float:
    """A near-critically damped spring settling by ``t`` = 1: overshoot under 0.2% of travel."""
    if t >= 1.0:
        return 1.0
    zeta, omega = 0.9, 8.0
    wd = omega * math.sqrt(1 - zeta * zeta)
    decay = math.exp(-zeta * omega * max(0.0, t))
    return 1.0 - decay * (math.cos(wd * t) + zeta * omega / wd * math.sin(wd * t))


def _edge_offset(edge: str, amount: float, geom: PhoneGeometry) -> tuple[float, float]:
    dx = (
        {"left": -1.0, "right": 1.0}.get(edge, 0.0)
        * amount
        * geom.frame_w
        * _ENTER_TRAVEL_FRAC
        * 1.6
    )
    dy = {"top": -1.0, "bottom": 1.0}.get(edge, 0.0) * amount * geom.frame_h * _ENTER_TRAVEL_FRAC
    return dx, dy


def _zoom_pose(
    a: Action, t: float, geom: PhoneGeometry, screens: Sequence[_Screen], plan
) -> tuple[float, float, float]:
    """``(scale, x, y)`` of the phone's top-left while zoom ``a`` runs."""
    fit = screens[a.screen].fit
    f = (geom.phone_w - 2 * geom.bezel) / fit.width
    top = view_at(plan, a.start_s)[0][1] - fit.strip
    x0, y0, x1, y1 = a.rect
    auto = min(0.88 * geom.frame_w / ((x1 - x0) * f), 0.5 * geom.frame_h / ((y1 - y0) * f), 2.5)
    target = a.scale if a.scale is not None else max(1.15, auto)
    ease_in = _ease_in_out(_window(t, a.start_s, a.start_s + a.ease_s))
    ease_out = _ease_in_out(_window(t, a.end_s - a.ease_s, a.end_s))
    e = ease_in * (1.0 - ease_out)
    bx = geom.bezel + (x0 + x1) / 2 * f
    by = geom.bezel + ((y0 + y1) / 2 - top) * f
    ox, oy = geom.origin
    px = _lerp(ox + bx, geom.frame_w * _ZOOM_FOCUS[0], e)
    py = _lerp(oy + by, geom.frame_h * _ZOOM_FOCUS[1], e)
    k = _lerp(1.0, target, e)
    return k, px - k * bx, py - k * by


def _camera(
    plan: Sequence[Action], t: float, geom: PhoneGeometry, screens
) -> tuple[float, float, float, float]:
    """``(scale, x, y, opacity)`` of the phone at ``t``; opacity 0 before an enter / after an exit."""
    ox, oy = geom.origin
    for a in plan:
        if a.kind == "enter" and t < a.start_s or a.kind == "exit" and t >= a.end_s:
            return 1.0, ox, oy, 0.0
        if not a.start_s <= t < a.end_s:
            continue
        w = _window(t, a.start_s, a.end_s)
        if a.kind == "enter":
            dx, dy = _edge_offset(a.edge, 1.0 - _spring(w), geom)
            return 1.0, ox + dx, oy + dy, _ease_out(_window(w, 0.0, 0.5))
        if a.kind == "exit":
            dx, dy = _edge_offset(a.edge, 1.0 - _spring(1.0 - w), geom)
            return 1.0, ox + dx, oy + dy, 1.0 - _ease_in_out(_window(w, 0.3, 1.0))
        if a.kind == "zoom":
            return (*_zoom_pose(a, t, geom, screens, plan), 1.0)
    return 1.0, ox, oy, 1.0


# ── screen ──────────────────────────────────────────────────────────────────────────────────────


def _fill(img: Image.Image, rgb: tuple, x0: int, y0: int, x1: int, y1: int) -> None:
    """A solid rectangle clipped to ``img`` — mid-swap, a screen's box runs off either side."""
    box = (max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1))
    if box[0] < box[2] and box[1] < box[3]:
        img.paste(rgb, box)


def _paint_screen(img: Image.Image, scr: _Screen, scroll_y: float, x_off: int) -> float:
    """Width-fit ``scr`` into ``img`` at ``x_off``; returns its top edge in screenshot pixels."""
    cw, ch = img.size
    fit = scr.fit
    f = cw / fit.width
    top = scroll_y - fit.strip
    _fill(img, scr.top_rgb, x_off, 0, x_off + cw, ch)
    y0, y1 = max(0.0, top), min(float(fit.height), top + fit.view_h)
    dy = round((y0 - top) * f)
    dh = max(1, min(ch - dy, round((y1 - y0) * f)))
    _fill(img, scr.bottom_rgb, x_off, dy + dh, x_off + cw, ch)
    part = scr.img.resize((cw, dh), Image.LANCZOS, box=(0, y0, fit.width, y1))
    blit(img, part, x_off, dy)
    return top


def _marks(
    img: Image.Image, plan, t: float, top: float, f: float, colors: PhoneColors, frame_h: int
) -> None:
    stroke = stroke_px("emphasis", frame_h)
    for a in plan:
        if not a.start_s <= t < a.end_s or a.kind not in ("highlight", "tap"):
            continue
        if a.kind == "tap":
            center = (a.point[0] * f, (a.point[1] - top) * f)
            marks.draw_tap(
                img,
                center,
                radius=img.width * 0.055,
                w=_window(t, a.start_s, a.end_s),
                stroke=stroke,
                rgb=colors.mark,
            )
            continue
        rect = (a.rect[0] * f, (a.rect[1] - top) * f, a.rect[2] * f, (a.rect[3] - top) * f)
        box = marks.ring_box(rect, a.shape, img.width * 0.022)
        radius = min((box[3] - box[1]) / 2, img.width * 0.045)
        drawn = _ease_out(_window(t, a.start_s, a.start_s + a.draw_s))
        fade = 1.0 - _ease_in_out(_window(t, a.end_s - a.fade_s, a.end_s))
        marks.dim_outside(
            img, box, shape=a.shape, radius=radius, alpha=a.dim * drawn * fade, rgb=colors.ink
        )
        marks.draw_ring(
            img,
            box,
            shape=a.shape,
            radius=radius,
            progress=drawn,
            alpha=fade,
            stroke=stroke,
            rgb=colors.mark,
        )


def _screen_image(
    plan, t: float, size: tuple[int, int], screens: Sequence[_Screen], colors, frame_h: int
) -> Image.Image:
    img = Image.new("RGB", size, colors.canvas)
    views = view_at(plan, t)
    for screen, scroll_y, shift in views:
        top = _paint_screen(img, screens[screen], scroll_y, round(shift * size[0]))
    if len(views) == 1:
        _marks(img, plan, t, top, size[0] / screens[views[0][0]].fit.width, colors, frame_h)
    return img


# ── frames ──────────────────────────────────────────────────────────────────────────────────────


def _frame(
    plan, t: float, bg: Image.Image, geom: PhoneGeometry, screens, colors: PhoneColors
) -> Image.Image:
    k, x, y, opacity = _camera(plan, t, geom, screens)
    frame = bg.copy()
    if opacity <= 0:
        return frame
    p: Placed = placed(geom, k)
    ix, iy = round(x), round(y)
    sh_mask, sdx, sdy = shadow(p)
    blit(frame, Image.new("RGB", sh_mask.size, colors.shadow), ix + sdx, iy + sdy, sh_mask)
    blit(
        frame,
        _screen_image(plan, t, p.screen_size, screens, colors, geom.frame_h),
        ix + p.bezel,
        iy + p.bezel,
        screen_mask(p),
    )
    rgb, alpha = chassis(p, colors)
    blit(frame, rgb, ix - button_margin(p), iy, alpha)
    return frame if opacity >= 1 else Image.blend(bg, frame, opacity)


def render_phone_walkthrough_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    image: Path | None = None,
    stills: Sequence[Path] | None = None,
    actions: Path | dict | None = None,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Render the walkthrough as numbered PNGs. Screen 0 is ``image``; ``stills`` are 1.. in order."""
    area = _resolve_area(ratio)
    palette = _load_palette(kit)
    n_frames = _frame_count(fps, duration_s)
    if image is None:
        raise SceneError("phone-walkthrough needs --image: the first screen")
    screens = [_load_screen(image, flag="--image")]
    screens += [_load_screen(s, flag="--still") for s in stills or ()]
    raw = _load_actions(actions)
    plan = parse_actions(raw, screens=[s.img.size for s in screens], duration_s=duration_s)
    layout = parse_layout(raw.get("layout") if isinstance(raw, dict) else None)
    colors = phone_colors(kit, palette)
    geom = phone_geometry(area.width, area.height, layout=layout)
    bg = Image.new("RGB", (area.width, area.height), colors.canvas)

    def frames() -> Iterator[Image.Image]:
        for i in range(n_frames):
            yield _frame(plan, i / fps, bg, geom, screens, colors)

    return _write_frame_stream(frames(), out_dir, prefix="phone-walkthrough")
