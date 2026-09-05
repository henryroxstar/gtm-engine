"""Per-frame composition for :mod:`caller_row` — split out to keep that module under the
repo's §R10 file-length ratchet (docs/RULES.md).

Deliberately self-contained (no import from ``caller_row``, which imports this module) —
every geometry value a draw function needs is a field on :class:`_CallerRowLayout`, computed
once by ``render_caller_row_frames`` outside the per-frame loop, rather than a shared module
constant re-derived on both sides of the split.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw

from ...design_tokens import stroke_px
from ..draw import _ease_out, _glow, _panel, _text_tracked, _tracked_w, _window

#: How long the UI takes to settle at the head of a beat. The live footage is cut in at this mark
#: by the caller (`inset_video(start_s=...)`), so the shot opens on its own chrome arriving and the
#: call visibly picking up, rather than on a hard cut into a talking head.
SETTLE_S = 0.35

#: The overline that makes the sector possessive without spending payload width on the word.
#: "AGENT FOR / A PHONE COMPANY" says what "THE PHONE COMPANY'S AGENT" said, in a column narrow
#: enough to hold the type floor.
_AGENT_OVERLINE = "AGENT FOR"


def _meta(spec, t: float) -> str:
    """``3/4 · 00:09`` — which call this is, and a timer that makes it read as LIVE rather than as
    a lower third. Kept terse because it shares the aura row with the org label, and the label is
    the payload of the two."""
    secs = spec.start_s + t
    return f"{spec.index}/{spec.of}  ·  {int(secs) // 60:02d}:{int(secs) % 60:02d}"


def _aura(
    layer: Image.Image,
    centre: tuple[int, int],
    radius: int,
    rgb: tuple[int, int, int],
    *,
    amplitude: float,
    phase: float,
) -> None:
    """The voice agent, drawn onto ``layer`` as a breathing ring rather than a face.

    DELIBERATELY NOT A FACE. A convincing synthetic face on the agent's side imports a deepfake
    reading that fights the film's own argument — the point of these four calls is that the
    machine knew who it was talking to, not that it looked like someone. A ring that breathes says
    "something is listening" and claims nothing else.

    ``amplitude`` is the difference between idling while the caller speaks and answering.
    """
    draw = ImageDraw.Draw(layer)
    cx, cy = centre
    for i in range(3):
        beat = 0.5 + 0.5 * math.sin(2 * math.pi * (phase - i * 0.12))
        r = radius * (0.46 + 0.27 * i + 0.13 * beat * amplitude)
        alpha = round(255 * (0.60 - 0.16 * i) * (0.45 + 0.55 * amplitude))
        draw.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            outline=(*rgb, max(0, min(255, alpha))),
            width=stroke_px("hairline", layer.height),
        )
    core = radius * 0.22
    draw.ellipse(
        (cx - core, cy - core, cx + core, cy + core),
        fill=(*rgb, round(255 * min(1.0, 0.55 + 0.45 * amplitude))),
    )


@dataclass
class _CallerRowLayout:
    """Everything a single composed frame needs, computed once outside the per-frame loop."""

    w: int
    h: int
    #: The scene's `_CallerRowSpec`, typed loosely so this module needs no import back into
    #: `caller_row` (see the module docstring). Only `.active`, `.index`, `.of`, `.start_s` are read.
    spec: object
    palette: object
    accent: tuple[int, int, int]
    text_rgb: tuple[int, int, int]
    dim_rgb: tuple[int, int, int]
    ground: Image.Image
    tiles: list[Image.Image]
    connecting: Image.Image
    thumbs_rects: list[tuple[int, int, int, int]]
    active_rect: tuple[int, int, int, int]
    radius: int
    agent_rect: tuple[int, int, int, int]
    rule_x: int
    over_font: object
    meta_font: object
    label_font: object
    lines: list[str]
    aura_r: int
    gap_b: int
    line_h: float
    label_top: float
    meta_top: float
    aura_cy: int
    speak_at: float


def _draw_caller_row_active_tile(
    img: Image.Image, draw: ImageDraw.ImageDraw, geo: _CallerRowLayout, t: float, settle: float
) -> None:
    ax, ay, aw, ah = geo.active_rect
    _glow(img, (ax, ay, ax + aw, ay + ah), geo.accent, strength=0.34 * settle, spread=0.020)
    if t < SETTLE_S:
        tile = geo.connecting.copy()
        if settle < 1.0:
            tile.putalpha(tile.getchannel("A").point(lambda v, settle=settle: round(v * settle)))
        img.paste(tile, (ax, ay), tile)
    draw.rounded_rectangle(
        (ax, ay, ax + aw, ay + ah),
        radius=geo.radius,
        outline=(*geo.accent, round(235 * settle)),
        width=stroke_px("rule", geo.h),
    )


def _draw_caller_row_participant_strip(
    img: Image.Image, draw: ImageDraw.ImageDraw, geo: _CallerRowLayout, settle: float
) -> None:
    for j, (x, y, jw, jh) in enumerate(geo.thumbs_rects):
        live = j == geo.spec.active
        oy = 0 if live else round((1 - settle) * geo.h * 0.010)
        tile = geo.tiles[j]
        if settle < 1.0:
            tile = tile.copy()
            tile.putalpha(tile.getchannel("A").point(lambda v, settle=settle: round(v * settle)))
        img.paste(tile, (x, y - oy), tile)
        draw.rounded_rectangle(
            (x, y - oy, x + jw, y + jh - oy),
            radius=round(geo.h * 0.008),
            outline=(*geo.accent, round(235 * settle))
            if live
            else (*geo.dim_rgb, round(110 * settle)),
            width=stroke_px("rule" if live else "hairline", geo.h),
        )


def _draw_caller_row_agent_zone(
    img: Image.Image,
    layer: Image.Image,
    draw: ImageDraw.ImageDraw,
    geo: _CallerRowLayout,
    t: float,
    settle: float,
) -> None:
    gx, gy, gw, gh = geo.agent_rect
    _panel(img, (gx, gy, gx + gw, gy + gh), geo.palette, alpha=settle, active=True)
    draw.line(
        (geo.rule_x, gy + round(gh * 0.10), geo.rule_x, gy + round(gh * 0.90)),
        fill=(*geo.dim_rgb, round(90 * settle)),
        width=stroke_px("hairline", geo.h),
    )

    amplitude = 0.30 + 0.70 * _ease_out(_window(t, geo.speak_at, geo.speak_at + 0.35))
    _aura(
        layer,
        (gx + gw // 2, geo.aura_cy),
        geo.aura_r,
        geo.accent,
        amplitude=amplitude * settle,
        phase=(geo.spec.start_s + t) / 1.6,
    )
    over_w = _tracked_w(draw, _AGENT_OVERLINE, geo.over_font)
    _text_tracked(
        draw,
        (gx + (gw - over_w) / 2, geo.label_top),
        _AGENT_OVERLINE,
        geo.over_font,
        (*geo.dim_rgb, round(225 * settle)),
    )
    ly = geo.label_top + geo.over_font.size + geo.gap_b
    for line in geo.lines:
        lw = _tracked_w(draw, line, geo.label_font)
        _text_tracked(
            draw,
            (gx + (gw - lw) / 2, ly),
            line,
            geo.label_font,
            (*geo.text_rgb, round(255 * settle)),
        )
        ly += geo.line_h

    meta = _meta(geo.spec, t)
    mw = _tracked_w(draw, meta, geo.meta_font)
    _text_tracked(
        draw,
        (gx + (gw - mw) / 2, geo.meta_top),
        meta,
        geo.meta_font,
        (*geo.dim_rgb, round(215 * settle)),
    )


def _compose_caller_row_frame(t: float, geo: _CallerRowLayout) -> Image.Image:
    settle = _ease_out(_window(t, 0.0, SETTLE_S))
    # The ground stays RGB and every faded element is drawn on ONE alpha layer composited at
    # the end. Drawing a part-alpha colour straight onto an image with `ImageDraw` REPLACES
    # the pixel rather than blending it, so a fade written that way is not a fade at all — it
    # is full-strength ink with an alpha channel that the RGB flatten then discards.
    img = geo.ground.copy()
    layer = Image.new("RGBA", (geo.w, geo.h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    _draw_caller_row_active_tile(img, draw, geo, t, settle)
    _draw_caller_row_participant_strip(img, draw, geo, settle)
    _draw_caller_row_agent_zone(img, layer, draw, geo, t, settle)
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
