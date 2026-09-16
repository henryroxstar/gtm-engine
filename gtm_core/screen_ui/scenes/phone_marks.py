"""What ``phone-walkthrough`` draws ON the screen: the dim, the highlight ring, the tap ripple.

All three are drawn into the screen image — under the glass, so the bezel clips them and a scroll
carries them — and never onto the frame. Pillow strokes are aliased, so each mark is drawn at
``_SS``x into a patch just big enough to hold it and scaled down; the patch keeps a 1080p frame's
cost to the size of the mark rather than the size of the screen.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from ..draw import _ease_in_out, _ease_out, _window
from .phone_chassis import blit

_SS = 3


def _ellipse_path(box: tuple[float, float, float, float], steps: int) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    return [
        (
            cx + rx * math.cos(-math.pi / 2 + 2 * math.pi * i / steps),
            cy + ry * math.sin(-math.pi / 2 + 2 * math.pi * i / steps),
        )
        for i in range(steps + 1)
    ]


def _rounded_path(box: tuple[float, float, float, float], r: float) -> list[tuple[float, float]]:
    """A rounded rectangle's outline as one polyline, clockwise from the middle of the top edge."""
    x0, y0, x1, y1 = box
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    pts = [((x0 + x1) / 2, y0)]
    corners = (
        (x1 - r, y0 + r, -90),
        (x1 - r, y1 - r, 0),
        (x0 + r, y1 - r, 90),
        (x0 + r, y0 + r, 180),
    )
    for cx, cy, start in corners:
        for i in range(13):
            a = math.radians(start + 90 * i / 12)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(((x0 + x1) / 2, y0))
    return pts


def _partial(pts: list[tuple[float, float]], progress: float) -> list[tuple[float, float]]:
    """The first ``progress`` of a polyline's length, ending on an interpolated point."""
    lengths = [math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False)]
    budget = sum(lengths) * max(0.0, min(1.0, progress))
    out = [pts[0]]
    for (a, b), seg in zip(zip(pts, pts[1:], strict=False), lengths, strict=True):
        if budget >= seg:
            out.append(b)
            budget -= seg
            continue
        if seg > 0:
            k = budget / seg
            out.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
        break
    return out


def _stroke_patch(
    img: Image.Image,
    pts: list[tuple[float, float]],
    *,
    stroke: int,
    rgb: tuple[int, int, int],
    alpha: float,
) -> None:
    """Stroke a polyline onto ``img`` antialiased, with round caps."""
    if alpha <= 0 or len(pts) < 2:
        return
    pad = stroke + 2
    x0 = math.floor(min(p[0] for p in pts)) - pad
    y0 = math.floor(min(p[1] for p in pts)) - pad
    x1 = math.ceil(max(p[0] for p in pts)) + pad
    y1 = math.ceil(max(p[1] for p in pts)) + pad
    big = Image.new("L", ((x1 - x0) * _SS, (y1 - y0) * _SS), 0)
    d = ImageDraw.Draw(big)
    scaled = [((px - x0) * _SS, (py - y0) * _SS) for px, py in pts]
    d.line(scaled, fill=255, width=stroke * _SS, joint="curve")
    cap = stroke * _SS / 2
    for cx, cy in (scaled[0], scaled[-1]):
        d.ellipse((cx - cap, cy - cap, cx + cap, cy + cap), fill=255)
    mask = big.resize((x1 - x0, y1 - y0), Image.LANCZOS).point(lambda v: round(v * min(1.0, alpha)))
    blit(img, Image.new("RGB", mask.size, rgb), x0, y0, mask)


def ring_box(
    rect: tuple[float, float, float, float], shape: str, pad: float
) -> tuple[float, float, float, float]:
    """The ring's own box around a target: padded, or the ellipse through the padded corners."""
    x0, y0, x1, y1 = rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad
    if shape == "circle":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = (x1 - x0) / 2 * math.sqrt(2), (y1 - y0) / 2 * math.sqrt(2)
        return cx - rx, cy - ry, cx + rx, cy + ry
    return x0, y0, x1, y1


def dim_outside(
    img: Image.Image,
    box: tuple[float, float, float, float],
    *,
    shape: str,
    radius: float,
    alpha: float,
    rgb: tuple[int, int, int],
) -> None:
    """Soften everything on the screen except the ring's inside."""
    if alpha <= 0:
        return
    mask = Image.new("L", img.size, round(255 * min(1.0, alpha)))
    d = ImageDraw.Draw(mask)
    if shape == "circle":
        d.ellipse(box, fill=0)
    else:
        d.rounded_rectangle(box, radius=max(0, round(radius)), fill=0)
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, round(img.width * 0.004))))
    img.paste(Image.new("RGB", img.size, rgb), (0, 0), mask)


def draw_ring(
    img: Image.Image,
    box: tuple[float, float, float, float],
    *,
    shape: str,
    radius: float,
    progress: float,
    alpha: float,
    stroke: int,
    rgb: tuple[int, int, int],
) -> None:
    pts = _ellipse_path(box, 96) if shape == "circle" else _rounded_path(box, radius)
    _stroke_patch(img, _partial(pts, progress), stroke=stroke, rgb=rgb, alpha=alpha)


def draw_tap(
    img: Image.Image,
    center: tuple[float, float],
    *,
    radius: float,
    w: float,
    stroke: int,
    rgb: tuple[int, int, int],
) -> None:
    """A fingertip press (a disc that dips and lifts) and the ring it throws off."""
    cx, cy = center
    press = _ease_out(_window(w, 0.0, 0.15)) * (1.0 - _ease_in_out(_window(w, 0.35, 1.0)))
    if press > 0:
        r = radius * (1.0 - 0.15 * math.sin(math.pi * _window(w, 0.0, 0.4)))
        disc = Image.new("L", (math.ceil(2 * r) * _SS + _SS, math.ceil(2 * r) * _SS + _SS), 0)
        ImageDraw.Draw(disc).ellipse(
            (0, 0, 2 * r * _SS, 2 * r * _SS), fill=round(255 * 0.34 * press)
        )
        small = disc.resize((disc.width // _SS, disc.height // _SS), Image.LANCZOS)
        blit(img, Image.new("RGB", small.size, rgb), round(cx - r), round(cy - r), small)
    spread = _window(w, 0.15, 1.0)
    if 0 < spread < 1:
        rr = radius * (1.0 + 1.6 * _ease_out(spread))
        pts = _ellipse_path((cx - rr, cy - rr, cx + rr, cy + rr), 72)
        width = max(1, round(stroke * (1.0 - 0.5 * spread)))
        _stroke_patch(img, pts, stroke=width, rgb=rgb, alpha=0.9 * (1.0 - spread))
