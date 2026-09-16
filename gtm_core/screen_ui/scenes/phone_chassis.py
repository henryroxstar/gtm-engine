"""The one phone chassis ``phone-walkthrough`` draws, and where it sits in the frame.

ONE DEVICE, DRAWN FROM CODE. A generated walkthrough put the same app in two different phones a
few seconds apart, and a viewer reads that as two products. Every proportion below is a named
fraction of the phone itself, so a walkthrough at any ratio carries the identical device.

COLOURS. The ground is the kit's canvas; the frame band and shadow follow the kit's ink on a
light ground (a warm near-black on cream, not a stock grey). The glass and the island are a
device-physical black — an achromatic literal, the same class as the palette's last resorts,
because a phone's bezel is black on every tenant's film.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..base import SceneError
from ..palette import _hex_to_rgb, _logo_background, _Palette
from .phone_actions import SCREEN_ASPECT

#: Phone height as a fraction of frame height: large enough to read a list row on a phone, with
#: the frame's own margins still visible so it reads as a device and not a crop.
PHONE_H_FRAC = 0.73
#: Where the phone's vertical centre sits, as a fraction of frame height. Both are overridable by
#: an actions document's top-level ``"layout"`` — captions must move below a smaller, higher phone
#: in a 9:16 cut, so the walkthrough's own size and position can no longer be hardcoded.
_CENTER_Y_FRAC = 0.50
_PHONE_H_FRAC_RANGE = (0.45, 0.80)
_CENTER_Y_FRAC_RANGE = (0.30, 0.70)
_LAYOUT_KEYS = frozenset({"phone_h_frac", "center_y_frac"})
_BEZEL_FRAC = 0.011  # glass border, of phone height
_RIM_FRAC = 0.0045  # the lighter frame band around the glass, of phone height
_BODY_RADIUS_FRAC = 0.145  # of phone width
_ISLAND_W_FRAC = 0.32  # of screen width
_ISLAND_ASPECT = 3.4  # island width over height
_ISLAND_TOP_FRAC = 0.013  # of screen height
_BUTTON_OUT_FRAC = 0.005  # how far a side button stands proud, of phone height
#: Side buttons as (side, top, bottom) fractions of phone height.
_BUTTONS = (
    ("left", 0.18, 0.215),
    ("left", 0.25, 0.31),
    ("left", 0.33, 0.39),
    ("right", 0.27, 0.37),
)
_GLASS = (10, 10, 10)
_SUPERSAMPLE = 3
_MAX_DRAW_PX = 6000


@dataclass(frozen=True)
class PhoneLayout:
    """The optional per-shot override of the phone's size and vertical position."""

    phone_h_frac: float = PHONE_H_FRAC
    center_y_frac: float = _CENTER_Y_FRAC


def _num(value: object, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SceneError(f"{where} must be a finite number, got {value!r}")
    return float(value)


def parse_layout(raw: object) -> PhoneLayout:
    """The actions document's optional top-level ``"layout"``. Absent (``None``) keeps today's
    fixed phone size and centring, byte-identical to before this key existed."""
    if raw is None:
        return PhoneLayout()
    if not isinstance(raw, dict):
        raise SceneError(f'"layout" must be an object, got {type(raw).__name__}')
    unknown = sorted(set(raw) - _LAYOUT_KEYS)
    if unknown:
        raise SceneError(f"layout: unknown key(s) {unknown}; accepts {sorted(_LAYOUT_KEYS)}")
    h = _num(raw.get("phone_h_frac", PHONE_H_FRAC), where="layout.phone_h_frac")
    cy = _num(raw.get("center_y_frac", _CENTER_Y_FRAC), where="layout.center_y_frac")
    lo, hi = _PHONE_H_FRAC_RANGE
    if not lo <= h <= hi:
        raise SceneError(f"layout.phone_h_frac must be within [{lo:g}, {hi:g}], got {h:g}")
    lo, hi = _CENTER_Y_FRAC_RANGE
    if not lo <= cy <= hi:
        raise SceneError(f"layout.center_y_frac must be within [{lo:g}, {hi:g}], got {cy:g}")
    return PhoneLayout(h, cy)


@dataclass(frozen=True)
class PhoneGeometry:
    """The phone at scale 1, in frame pixels."""

    frame_w: int
    frame_h: int
    phone_w: float
    phone_h: float
    bezel: float
    center_y_frac: float = _CENTER_Y_FRAC

    @property
    def origin(self) -> tuple[float, float]:
        # The ``- 0.50`` delta is exactly 0.0 for the default centring, so this reduces to the
        # original ``(frame_h - phone_h) / 2`` bit-for-bit — no layout key, no pixel changes.
        oy = (self.frame_h - self.phone_h) / 2 + (
            self.center_y_frac - _CENTER_Y_FRAC
        ) * self.frame_h
        return (self.frame_w - self.phone_w) / 2, oy


@dataclass(frozen=True)
class PhoneColors:
    canvas: tuple[int, int, int]
    rim: tuple[int, int, int]
    shadow: tuple[int, int, int]
    ink: tuple[int, int, int]
    mark: tuple[int, int, int]


@dataclass(frozen=True)
class Placed:
    """The phone at one scale, snapped to whole pixels."""

    phone_w: int
    phone_h: int
    bezel: int

    @property
    def screen_size(self) -> tuple[int, int]:
        return self.phone_w - 2 * self.bezel, self.phone_h - 2 * self.bezel


def phone_geometry(
    frame_w: int, frame_h: int, *, layout: PhoneLayout | None = None
) -> PhoneGeometry:
    layout = layout or PhoneLayout()
    phone_h = frame_h * layout.phone_h_frac
    bezel = phone_h * _BEZEL_FRAC
    screen_w = (phone_h - 2 * bezel) * SCREEN_ASPECT
    return PhoneGeometry(
        frame_w, frame_h, screen_w + 2 * bezel, phone_h, bezel, center_y_frac=layout.center_y_frac
    )


def placed(geom: PhoneGeometry, scale: float) -> Placed:
    return Placed(
        round(geom.phone_w * scale), round(geom.phone_h * scale), max(1, round(geom.bezel * scale))
    )


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))


def phone_colors(kit: dict, palette: _Palette) -> PhoneColors:
    """Chassis, shadow and mark colours from the kit. The mark is ``primary``, then the accent."""
    canvas = _hex_to_rgb(palette.canvas, field="canvas")
    ink = _hex_to_rgb(palette.text, field="text")
    raw = (kit.get("palette") or {}) if isinstance(kit, dict) else {}
    mark_hex = (
        raw.get("primary") if isinstance(raw, dict) and raw.get("primary") else palette.accent
    )
    mark = _hex_to_rgb(mark_hex, field="primary")
    if _logo_background(palette) == "light":
        return PhoneColors(canvas, _mix(ink, canvas, 0.18), ink, ink, mark)
    return PhoneColors(
        canvas, _mix(canvas, ink, 0.30), (0, 0, 0), _mix(canvas, (0, 0, 0), 0.5), mark
    )


def button_margin(p: Placed) -> int:
    return max(1, round(p.phone_h * _BUTTON_OUT_FRAC))


def _masks(p: Placed, ss: int) -> tuple[Image.Image, Image.Image, Image.Image]:
    """``(outer, inner, glass_alpha)`` L masks at ``ss``x: body+buttons, body inside the rim, and
    the alpha of everything that is not the see-through screen."""
    m = button_margin(p) * ss
    w, h, bez = p.phone_w * ss, p.phone_h * ss, p.bezel * ss
    size = (w + 2 * m, h)
    body_r = round(w * _BODY_RADIUS_FRAC)
    rim = max(ss, round(h * _RIM_FRAC))
    outer = Image.new("L", size, 0)
    od = ImageDraw.Draw(outer)
    od.rounded_rectangle((m, 0, m + w - 1, h - 1), radius=body_r, fill=255)
    for side, top, bottom in _BUTTONS:
        x0, x1 = (0, m + rim) if side == "left" else (m + w - rim, 2 * m + w - 1)
        od.rounded_rectangle((x0, round(h * top), x1, round(h * bottom)), radius=m, fill=255)
    inner = Image.new("L", size, 0)
    ImageDraw.Draw(inner).rounded_rectangle(
        (m + rim, rim, m + w - 1 - rim, h - 1 - rim), radius=max(1, body_r - rim), fill=255
    )
    hole = Image.new("L", size, 0)
    screen_w, screen_h = w - 2 * bez, h - 2 * bez
    ImageDraw.Draw(hole).rounded_rectangle(
        (m + bez, bez, m + w - 1 - bez, h - 1 - bez), radius=max(1, body_r - bez), fill=255
    )
    island_w = round(screen_w * _ISLAND_W_FRAC)
    island_h = round(island_w / _ISLAND_ASPECT)
    ix0 = m + (w - island_w) // 2
    iy0 = bez + round(screen_h * _ISLAND_TOP_FRAC)
    ImageDraw.Draw(hole).rounded_rectangle(
        (ix0, iy0, ix0 + island_w, iy0 + island_h), radius=island_h // 2, fill=0
    )
    return outer, inner, ImageChops.subtract(outer, hole)


@lru_cache(maxsize=4)
def chassis(p: Placed, colors: PhoneColors) -> tuple[Image.Image, Image.Image]:
    """``(rgb, alpha)`` for the device at one size. The screen is a transparent hole."""
    ss = max(1, min(_SUPERSAMPLE, _MAX_DRAW_PX // max(1, p.phone_h)))
    outer, inner, alpha = _masks(p, ss)
    rgb = Image.new("RGB", outer.size, colors.rim)
    rgb.paste(_GLASS, (0, 0, *outer.size), inner)
    out_size = (p.phone_w + 2 * button_margin(p), p.phone_h)
    return rgb.resize(out_size, Image.LANCZOS), alpha.resize(out_size, Image.LANCZOS)


@lru_cache(maxsize=4)
def screen_mask(p: Placed) -> Image.Image:
    """The screen's rounded shape, for the screen image to be pasted through.

    Without it a rectangular screenshot's corners stand outside the body's rounded corners: the
    chassis hole is transparent there, and so is everything around the phone.
    """
    ss = max(1, min(_SUPERSAMPLE, _MAX_DRAW_PX // max(1, p.phone_h)))
    sw, sh = p.screen_size
    big = Image.new("L", (sw * ss, sh * ss), 0)
    corner = max(1, round(p.phone_w * ss * _BODY_RADIUS_FRAC) - p.bezel * ss)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, sw * ss - 1, sh * ss - 1), radius=corner, fill=255)
    return big.resize((sw, sh), Image.LANCZOS)


@lru_cache(maxsize=4)
def shadow(p: Placed) -> tuple[Image.Image, int, int]:
    """A soft drop shadow mask and its ``(dx, dy)`` offset from the phone's top-left.

    Drawn at quarter size and scaled up: the result is a blur, so resolution buys nothing.
    """
    pad = round(p.phone_h * 0.08)
    q = 4
    size = ((p.phone_w + 2 * pad) // q, (p.phone_h + 2 * pad) // q)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (pad // q, pad // q, (pad + p.phone_w) // q, (pad + p.phone_h) // q),
        radius=round(p.phone_w * _BODY_RADIUS_FRAC) // q,
        fill=round(255 * 0.30),
    )
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, round(p.phone_h * 0.028) // q)))
    full = mask.resize((size[0] * q, size[1] * q), Image.BILINEAR)
    return full, -pad, -pad + round(p.phone_h * 0.022)


def blit(
    frame: Image.Image, layer: Image.Image, x: int, y: int, mask: Image.Image | None = None
) -> None:
    """Paste ``layer`` at ``(x, y)`` clipped to ``frame`` — a phone sliding in starts off-frame."""
    fw, fh = frame.size
    lw, lh = layer.size
    x0, y0, x1, y1 = max(0, x), max(0, y), min(fw, x + lw), min(fh, y + lh)
    if x0 >= x1 or y0 >= y1:
        return
    box = (x0 - x, y0 - y, x1 - x, y1 - y)
    frame.paste(layer.crop(box), (x0, y0), None if mask is None else mask.crop(box))
