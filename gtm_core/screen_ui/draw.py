from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..design_tokens import (
    RADIUS,
    REVEAL_CLASSES,
    Motion,
    Reveal,
    ease,
    reveal,
    stroke_px,
)
from ..video_lint import SafeArea
from .palette import _hex_to_rgb, _Palette

# ── easing (deterministic — no randomness, ever: two runs of the same inputs match byte-for-byte
#    in every frame's drawn geometry, which is the whole reason this exists) ───────────────────


def _ease_in_out(t: float) -> float:
    """Cubic ease-in-out. ``t`` in [0, 1] -> eased [0, 1]. A cursor or a color transition that
    starts and ends at rest reads as intentional motion; a linear one reads as a slide-show with
    extra steps, which is the V9 defect this module exists to avoid reproducing by other means."""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        round(_lerp(a[0], b[0], t)),
        round(_lerp(a[1], b[1], t)),
        round(_lerp(a[2], b[2], t)),
    )


def _window(t: float, start: float, end: float) -> float:
    """Map global progress ``t`` to local progress within [start, end], clamped to [0, 1]. The
    building block every phased animation below composes from — a cursor descent, a state flip,
    and a slide-up are each just one call to this with their own window."""
    if end <= start:
        return 1.0 if t >= end else 0.0
    return max(0.0, min(1.0, (t - start) / (end - start)))


# ── drawing primitives ──────────────────────────────────────────────────────────────────────────


def _font(face: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(face), size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    return draw.textlength(text, font=font)


def _fit_font(
    draw: ImageDraw.ImageDraw,
    face: Path,
    text: str,
    max_w: int,
    start_px: int,
    floor_px: int,
) -> ImageFont.FreeTypeFont:
    """Largest size in ``[floor_px, start_px]`` at which ``text`` fits ``max_w``.

    Returns the FLOOR when even that overruns, rather than shrinking below it: a caller whose copy
    cannot fit at the legibility floor has a copy problem, and silently typesetting it at 30px
    would hide that by producing something nobody can read on a phone anyway.
    """
    size = start_px
    while size > floor_px:
        font = _font(face, size)
        if _text_w(draw, text, font) <= max_w:
            return font
        size -= 2
    return _font(face, floor_px)


def _dashed_rect(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    rgb: tuple[int, int, int],
    *,
    width: int,
    dash: int,
) -> None:
    """A dashed rectangle. Pillow has no dash support, so the edges are walked in segments.

    Used for an ABSENCE — a cell that is empty because something is missing, which has to read
    differently from a cell that is merely unlit.
    """
    x0, y0, x1, y1 = box
    step = dash * 2
    for x in range(x0, x1, step):
        draw.line((x, y0, min(x + dash, x1), y0), fill=rgb, width=width)
        draw.line((x, y1, min(x + dash, x1), y1), fill=rgb, width=width)
    for y in range(y0, y1, step):
        draw.line((x0, y, x0, min(y + dash, y1)), fill=rgb, width=width)
        draw.line((x1, y, x1, min(y + dash, y1)), fill=rgb, width=width)


# ── design system ───────────────────────────────────────────────────────────────────────────────
#
# Every card in a film shares these four primitives, so "consistency across the diagrams" is a
# property of the code rather than of whoever last hand-tuned a radius. The operator's note was
# that the cards read as BORING: single-weight type on plain rectangles on a flat field, with a
# symmetric fade as the only motion. Each cause has a primitive below.
#
# The tenant's own ``[imagery].style`` already describes the world these cards live beside —
# "interconnected glowing nodes and fine thread-like pathways on a deep navy field, teal and cyan
# light traces, volumetric light". The footage honours it and the cards did not, which is most of
# why they looked like slides dropped into a film. ``_backdrop`` is that language, drawn at an
# amplitude low enough to leave every contrast ratio intact.


#: A burned caption occupies the band below this fraction of frame height, so nothing a scene
#: draws TEXT into may reach it. Measured 2026-08-29 (Reap `system_pro_box`, 16:9): the caption box
#: sat at 0.72..0.79. Module-level so a test names ONE number rather than restating it.
CAPTION_BAND_TOP_FRAC = 0.70


#: Where a citation FOOTER starts, just under the measured caption box (which ends at 0.79). Text
#: may sit either side of a burned caption — what it may not do is share those rows with it. A
#: card with more content than fits above the caption puts its lowest-priority block here rather
#: than squeezing the payload type toward its floor.
_FOOT_BAND_TOP_FRAC = 0.795


def _text_band(h: int, area: SafeArea) -> tuple[int, int]:
    """The vertical band a scene may lay TEXT in: the safe area, cut off at the caption band.

    The safe area's own bottom margin (0.12 at 16:9, so a floor at 0.88h) is about the frame EDGE
    and knows nothing about a caption burned in later. Every full-frame card was laying itself out
    against that floor, so a card tall enough to use it put its own lowest lines at 0.72–0.76h —
    underneath the caption, which is the "captions over the other caption" defect: two legible
    strings occupying the same pixels, neither readable.

    Only TEXT is bound by this. A panel's border or fill may pass behind the caption band without
    costing anything — what cannot is a second string competing with the caption for the same
    reader. Scenes therefore lay their text stack out against this band and let their chrome run
    to the safe area if it wants to.
    """
    return round(h * area.top), min(h - round(h * area.bottom), round(h * CAPTION_BAND_TOP_FRAC))


#: Cubic EASE-OUT — fast departure, settling arrival. Entrances use THIS, never `_ease_in_out`:
#: a symmetric curve starts slow, which reads as sluggish on an element that is simply arriving
#: (Emil Kowalski's rule, and the reason `ease-in` is banned outright on UI). `_ease_in_out` stays
#: correct for a value MOVING between two on-screen states (a cursor, a slide), where both ends
#: are at rest and the symmetry is the point.
def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - pow(1 - t, 3)


#: Amplitude ceiling for anything the backdrop draws, as a fraction of the way from canvas toward
#: white or black. Payload type is white on canvas at roughly 14:1; a field this shallow moves the
#: ratio by well under a point, so depth never costs legibility. Raising it is a contrast change,
#: not a taste change — `video_lint`'s contrast rule is the gate that would catch it.
_BACKDROP_MAX_DELTA = 0.05


def _backdrop(w: int, h: int, palette: _Palette) -> Image.Image:
    """The shared card ground: a lit vertical field, one soft accent bloom, and a thread lattice.

    Built ONCE per scene and copied per frame — a gaussian over a 1080p frame is far too slow to
    run 250 times, and none of this moves anyway. Every position is a closed-form function of its
    index: deterministic by construction, not by a seeded generator, so two runs match byte for
    byte the way the rest of this module does.
    """
    canvas = _hex_to_rgb(palette.canvas, field="canvas")
    accent = _hex_to_rgb(palette.accent, field="accent")

    # A flat fill is what makes a card read as a slide. Lifting the top and sinking the bottom
    # gives the frame a light direction, which is all "depth" means here.
    top = _lerp_color(canvas, (255, 255, 255), _BACKDROP_MAX_DELTA)
    bottom = _lerp_color(canvas, (0, 0, 0), _BACKDROP_MAX_DELTA * 4)
    column = Image.new("RGB", (1, h))
    px = column.load()
    for y in range(h):
        px[0, y] = _lerp_color(top, bottom, y / max(1, h - 1))
    base = column.resize((w, h), Image.BILINEAR)

    # One bloom, off-centre and high — a single light source. Two would read as a gradient mesh,
    # which is the stock-gradient cliché the kit's own `negative` list is aimed at.
    bloom = Image.new("L", (w, h), 0)
    bd = ImageDraw.Draw(bloom)
    r = round(h * 0.62)
    bd.ellipse(
        (round(w * 0.30) - r, round(h * 0.10) - r, round(w * 0.30) + r, round(h * 0.10) + r),
        fill=round(255 * _BACKDROP_MAX_DELTA * 1.6),
    )
    bloom = bloom.filter(ImageFilter.GaussianBlur(round(h * 0.10)))
    base = Image.composite(Image.new("RGB", (w, h), accent), base, bloom)

    # The thread lattice, at the very bottom of perception: present when you look for it, invisible
    # when you are reading the card. Nodes sit on a lattice walked by an irrational step, so they
    # never line up into a visible grid.
    threads = Image.new("RGB", (w, h), (0, 0, 0))
    td = ImageDraw.Draw(threads)
    trace = _lerp_color((0, 0, 0), accent, 0.5)
    phi = 0.6180339887
    pts: list[tuple[int, int]] = []
    for i in range(26):
        fx = (i * phi) % 1.0
        fy = (i * phi * phi * 3) % 1.0
        pts.append((round(w * fx), round(h * fy)))
    for i, (ax, ay) in enumerate(pts):
        bx, by = pts[(i * 7 + 3) % len(pts)]
        td.line((ax, ay, bx, by), fill=trace, width=stroke_px("thread", h))
    for x_, y_ in pts:
        rr = round(h * 0.004)
        td.ellipse((x_ - rr, y_ - rr, x_ + rr, y_ + rr), fill=accent)
    threads = threads.filter(ImageFilter.GaussianBlur(round(h * 0.002)))
    return Image.blend(base, Image.blend(base, threads, 0.5), _BACKDROP_MAX_DELTA * 1.3)


def _glow(
    img: Image.Image,
    box: tuple[int, int, int, int],
    rgb: tuple[int, int, int],
    *,
    strength: float,
    spread: float,
) -> None:
    """Bloom ``rgb`` outward from ``box``, in place. Marks the ONE element that is live.

    A single accent used on everything states no hierarchy at all, which is the other half of the
    "boring" note: when every panel is teal-edged, teal stops meaning "look here". A bloom costs a
    blur per call, so callers apply it to the active element only, never per panel.
    """
    if strength <= 0:
        return
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        box, radius=round(h * RADIUS["sm"]), fill=round(255 * max(0.0, min(1.0, strength)))
    )
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, round(h * spread))))
    img.paste(Image.composite(Image.new("RGB", (w, h), rgb), img, mask), (0, 0))


def _panel(
    img: Image.Image,
    box: tuple[int, int, int, int],
    palette: _Palette,
    *,
    alpha: float,
    active: bool = False,
    radius_frac: float | None = None,
) -> None:
    """The one panel every card is built from: fill, hairline top light, hairline border.

    The top edge is drawn one step lighter than the fill. That single line is what separates a
    surface catching light from a filled rectangle, and it is why this is a shared primitive
    rather than four scenes each calling ``rounded_rectangle`` with their own numbers.
    """
    if alpha <= 0:
        return
    w, h = img.size
    canvas = _hex_to_rgb(palette.canvas, field="canvas")
    surface = _hex_to_rgb(palette.surface, field="surface")
    accent = _hex_to_rgb(palette.accent, field="accent")
    text = _hex_to_rgb(palette.text, field="text")
    x0, y0, x1, y1 = box
    radius = round(h * (RADIUS["sm"] if radius_frac is None else radius_frac))

    if active:
        _glow(img, box, accent, strength=0.30 * alpha, spread=0.022)

    draw = ImageDraw.Draw(img)
    edge = accent if active else _lerp_color(canvas, text, 0.18)
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=_lerp_color(canvas, surface, alpha),
        outline=_lerp_color(canvas, edge, alpha * (0.9 if active else 0.7)),
        width=stroke_px("rule", h),
    )
    # INSIDE the panel, never straddling its top edge. Pillow centres a line on its path, so a
    # 1px light at `y0 + 1` was harmless and a scaled one is not: at 9:16 the ported weight is 7px,
    # which put three rows of lit pixels ABOVE the card and broke the safe-area guard in
    # `tests/media/test_screen_ui.py`. It is a highlight on a surface; it belongs on the surface.
    light_w = stroke_px("hairline", h)
    light_y = y0 + light_w // 2 + 1
    draw.line(
        (x0 + radius, light_y, x1 - radius, light_y),
        fill=_lerp_color(canvas, _lerp_color(surface, text, 0.22), alpha),
        width=light_w,
    )


#: Tracking for UPPERCASE labels, as a fraction of font size. Tight-set caps at small sizes are
#: the single most "undesigned" thing on these cards, so labels stay letter-spaced regardless of
#: weight. This used to be the ONLY hierarchy lever: with a single face vendored, contrast had to
#: come from size, opacity and tracking alone. A kit may now vendor a weight ladder
#: (``captions.load_faces`` — body/label/heading, each falling back to ``caption``), so tracking
#: and weight work together; a single-face kit is unchanged and still relies on this.
_LABEL_TRACKING = 0.10


def _text_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    *,
    tracking: float = _LABEL_TRACKING,
) -> None:
    """Draw ``text`` with per-character tracking. Pillow has no letter-spacing."""
    x, y = xy
    extra = font.size * tracking
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + extra


def _tracked_w(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    tracking: float = _LABEL_TRACKING,
) -> float:
    """Width of ``text`` as ``_text_tracked`` will draw it — for centring and for fit checks."""
    if not text:
        return 0.0
    return sum(draw.textlength(c, font=font) for c in text) + font.size * tracking * (len(text) - 1)


def _draw_cursor(draw: ImageDraw.ImageDraw, x: int, y: int, rgb: tuple[int, int, int]) -> None:
    """A simple filled arrow-cursor glyph — legible at UI-mockup scale without a font."""
    size = 16
    points = [
        (x, y),
        (x, y + size),
        (x + size * 0.35, y + size * 0.75),
        (x + size * 0.62, y + size * 1.05),
        (x + size * 0.78, y + size * 0.9),
        (x + size * 0.5, y + size * 0.58),
        (x + size * 0.78, y + size * 0.5),
    ]
    draw.polygon(points, fill=rgb)


# ── one motion language ─────────────────────────────────────────────────────────────────────────
#
# Before this helper the card family spoke two: `record_grid` revealed by opacity alone
# (`_lerp_color(canvas, ink, arrive)` with no positional change), while `checkpoint_flow` grew its
# connector lines and `caller_record` typed its value — no rule said which a scene should use, so
# every card chose for itself and the film read as a set of unrelated slides. Every entrance now
# goes through here: the same eased curve, the same travel distance, the same clamped cascade,
# all named by `gtm_core.design_tokens` rather than by whoever last tuned the scene.
#
# TWO CLOCKS, DELIBERATELY. A scene's BEAT schedule — which card, row or node arrives at which
# second — stays authored, because those beats are cut against a measured VO and moving them
# would desynchronise the film. What this replaces is the ENTRANCE MECHANIC inside each beat.
# The tempo's stagger is for SIBLINGS within one idea (the rows of a panel, the labels on a
# diagram); it is not a substitute for a narrative beat, which is exactly the distinction
# `.stagger` keeps on the deck surface, where it applies to a list and never to a slide change.


def _entrance(
    index: int,
    *,
    motion: Motion,
    duration_s: float,
    h: int,
    start: float = 0.0,
    cls: str = "fade_up",
    room: int | None = None,
) -> Reveal:
    """The ``index``-th sibling's entrance, with ``start`` given as a FRACTION of shot progress.

    Scenes reason in progress fractions (``t`` in [0, 1] across the shot) while the tempo tokens
    are in seconds, so the conversion happens once here instead of in every scene. ``cls`` names a
    reveal from the ported vocabulary — a scene says what KIND of thing is arriving and never how
    far or how long, which is the property that keeps the family speaking one language.
    """
    travel_scale, duration_scale, scale_from = REVEAL_CLASSES[cls]
    rev = reveal(
        index,
        tempo=motion.default_tempo,
        duration_s=duration_s,
        h=h,
        start_s=max(0.0, start) * duration_s,
        duration_scale=duration_scale,
        travel_scale=travel_scale,
        scale_from=scale_from,
        reduced_motion=motion.reduced_motion,
    )
    if room is None or rev.travel_px <= room:
        return rev
    # A `fade_up` starts BELOW its resting place and rises, so an element near the bottom of the
    # text band dips into the rows a caption is burned into while it arrives. On the deck surface
    # there is no such edge; here there is, and it is absolute — `_text_band` exists because two
    # legible strings in the same pixels is the defect. So the travel is clamped to the room the
    # element actually has, rather than the layout being shrunk by a travel step everywhere or the
    # motion being dropped from the card family that needed it most.
    return Reveal(rev.start, rev.end, max(0, room), rev.scale_from)


def _arrive(rev: Reveal, t: float) -> tuple[float, int]:
    """``(eased opacity in [0, 1], remaining downward travel in px)`` for one element at ``t``.

    The pair is returned together on purpose: an element that fades without moving is the defect
    this module had, and splitting the two invites a caller to use one and forget the other.
    """
    return rev.progress(t), rev.offset_px(t)


def _ease_named(name: str, t: float) -> float:
    """A ported easing curve by name — for a value MOVING between two on-screen states.

    ``_ease_out`` and ``_ease_in_out`` above stay for the hand-rolled cases they already serve;
    this is the door to the five Material curves the deck surface uses, so a scene that wants
    "emphasized decelerate" gets the same curve the decks do rather than a fourth approximation.
    """
    return ease(name, t)


# ── flow grammar, borrowed from the brand team's own gateway art ───────────────────────────────
#
# `knowledge/brand/infographics/flow-gateway-*.png` is how a tenant's own brand art draws a reach: a luminous
# track on a near-black ground, a bright dot where a line meets a node, and a chevron where the
# track arrives. Those three moves are borrowed here as GRAMMAR. The files themselves are not
# composited in — they are 1672x941 (under 1080p), their labels are trade vocabulary this film's
# register bans, and one carries a cartoon-robot icon `[imagery].negative` bans. The ring-node
# treatment is deliberately NOT borrowed either: this film's `graphic_vocabulary` rule collapses
# every card onto one small component set, and that set is the panel.


def _join_dot(
    draw: ImageDraw.ImageDraw, x: int, y: int, rgb: tuple[int, int, int], h: int, alpha: float
) -> None:
    """The bright dot where a track meets a node. Reads as a connection, not a collision."""
    if alpha <= 0:
        return
    r = max(1, round(h * 0.006 * min(1.0, alpha)))
    draw.ellipse((x - r, y - r, x + r, y + r), fill=rgb)


def _arrow_head(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    rgb: tuple[int, int, int],
    h: int,
    *,
    direction: str = "right",
    when: bool = True,
) -> None:
    """A chevron at the end of a track, so the reach has a DIRECTION rather than just a length.

    ``when`` is a parameter rather than a caller-side ``if`` on purpose: a chevron is only correct
    once its track has essentially landed, and three call sites each re-deriving "essentially" is
    how two of them end up with different thresholds.
    """
    if not when:
        return
    a = round(h * 0.016)
    b = round(h * 0.011)
    if direction == "right":
        pts = [(x - a, y - b), (x, y), (x - a, y + b)]
    elif direction == "up":
        pts = [(x - b, y + a), (x, y), (x + b, y + a)]
    else:
        pts = [(x - b, y - a), (x, y), (x + b, y - a)]
    draw.polygon(pts, fill=rgb)
