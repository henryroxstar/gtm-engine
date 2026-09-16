from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import INSET, RADIUS, px, resolve_motion, stroke_px
from ...shots_lint.overlay import OVERLAY_KINDS, normalize_overlay_kind
from ..base import SceneError
from ..draw import _arrive, _entrance, _font, _lerp_color, _text_band
from ..fit import _fit_or_refuse
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _Palette, _resolve_area
from .message_card import _wrap

# ── chat-bubble — one message beside a phone, burned in post ──────────────────────────────────
#
# The sibling of `message-card` for a shot that HAS a phone in it. A card is a notification the
# film shows the viewer; a bubble is what the character on screen is typing or reading, drawn
# beside the phone rather than on it, so the phone can be held at the natural angle of someone
# typing — tilted toward their own face, screen away from the lens — and the screen stays blank
# and evenly lit. Three kinds, from the shot list's overlay grammar: OUTGOING (what they write)
# sits on the far side from the phone and points at it, INCOMING (what a tool or a person sends
# back) sits on the other side, and an ACTION CHIP is the one thing they tap — a pill, one line.
#
# WHY IT IS DRAWN HERE AND NOT BY THE VIDEO MODEL: the same reason as every scene in this module
# (`shots_lint._lint_no_in_frame_text`). A generation model asked for a chat bubble produces
# letterform-shaped noise. And the same reason it is not composited ONTO the screen: a warp onto
# a hand-held phone can only resample down, so the words are illegible at feed size exactly where
# they matter.
#
# THE COPY IS DATA. A shot list carried this data (`production.overlay`) on four shots, and nothing
# consumed it — no scene, no finish verb, no lint — so the film shipped its bubbles as a note in a
# JSON file. Everything drawn here arrives through `bubble=`; the defaults below exist only so the
# scene renders standalone, and they are deliberately unremarkable and numberless, for the reason
# `message-card` gives: a bubble dramatizes a real-looking exchange, and a digit reads as a
# quantity someone can be held to.

#: Generic and fictional. `side` and `w_frac` are empty on purpose: they derive from `kind`
#: unless the caller states them, and a literal default would silently pin every kind to one side.
_BUBBLE_DEFAULTS: dict[str, str] = {
    "kind": "outgoing",
    "text": "Can you send that over before the call?",
    "side": "",
    "y_frac": "0.62",
    "w_frac": "",
    "arrive_s": "0.25",
}

#: Which frame edge each kind hugs when the caller does not say. From the overlay grammar the shot
#: list was written to: outgoing right of the phone, incoming left, the chip beside the incoming.
_DEFAULT_SIDE: dict[str, str] = {"outgoing": "right", "incoming": "left", "chip": "left"}

#: Max bubble width as a fraction of frame width. A chip is compact by definition.
_DEFAULT_W_FRAC: dict[str, float] = {"outgoing": 0.46, "incoming": 0.46, "chip": 0.40}

#: A bubble refuses rather than truncates past this many wrapped lines; the same ceiling, for the
#: same reason, as `message-card`'s. A chip is ONE line — a chip that wraps is a bubble.
_MAX_LINES: dict[str, int] = {"outgoing": 4, "incoming": 4, "chip": 1}

#: A codepoint no font maps (a Unicode noncharacter). Rendering it yields the face's `.notdef`
#: glyph — the tofu box — which is what any other unmapped character renders as too, so a mask
#: identical to this one IS a missing glyph. Pillow exposes no glyph-coverage query; this is the
#: check without adding a font-parsing dependency.
_NO_GLYPH_PROBE = "\U0010ffff"


@dataclass(frozen=True)
class _Bubble:
    kind: str
    text: str
    side: str
    y_frac: float
    w_frac: float
    arrive_s: float


def _as_float(value: object, key: str) -> float:
    try:
        return float(str(value).strip())
    except ValueError as exc:
        raise SceneError(f"chat bubble: {key} must be a number, got {value!r}") from exc


def _resolve_bubble(bubble: dict | None) -> _Bubble:
    """The bubble spec, with unset keys deriving from the kind.

    Same posture as `message-card`'s `_resolve_message`: an unknown key is a caller error and says
    so, rather than being dropped into a dict nobody reads. Values arrive as strings from the CLI
    and as numbers from the finish verb; both are accepted and normalised once, here.
    """
    resolved: dict[str, object] = dict(_BUBBLE_DEFAULTS)
    for key, value in (bubble or {}).items():
        if key not in _BUBBLE_DEFAULTS:
            raise SceneError(
                f"chat bubble: unknown key {key!r} — expected one of {sorted(_BUBBLE_DEFAULTS)}"
            )
        resolved[key] = value

    kind = normalize_overlay_kind(resolved["kind"])
    if kind is None:
        raise SceneError(
            f"chat bubble: kind {resolved['kind']!r} is not one of {sorted(OVERLAY_KINDS)} "
            "(or the shot-list spellings 'outgoing bubble' / 'incoming bubble' / 'action chip')"
        )
    # `strip()` on each line, not on the whole: a hard break is the caller's, and surrounding
    # whitespace on a line is never a decision.
    text = "\n".join(line.strip() for line in str(resolved["text"]).split("\n")).strip("\n")
    if not text.strip():
        raise SceneError("chat bubble: text was set to an empty string")

    side = str(resolved["side"] or "").strip().lower() or _DEFAULT_SIDE[kind]
    if side not in ("left", "right"):
        raise SceneError(f"chat bubble: side {resolved['side']!r} is not 'left' or 'right'")

    y_frac = _as_float(resolved["y_frac"], "y_frac")
    if not 0.0 < y_frac < 1.0:
        raise SceneError(f"chat bubble: y_frac {y_frac} must be strictly between 0 and 1")
    raw_w = str(resolved["w_frac"] or "").strip()
    w_frac = _as_float(raw_w, "w_frac") if raw_w else _DEFAULT_W_FRAC[kind]
    if not 0.0 < w_frac < 1.0:
        raise SceneError(f"chat bubble: w_frac {w_frac} must be strictly between 0 and 1")
    arrive_s = _as_float(resolved["arrive_s"], "arrive_s")
    if arrive_s < 0.0:
        raise SceneError(f"chat bubble: arrive_s {arrive_s} must be >= 0")
    return _Bubble(kind, text, side, y_frac, w_frac, arrive_s)


def resolve_bubble(bubble: dict | None) -> dict[str, object]:
    """The normalised spec as a plain dict — kind, text, side, y_frac, w_frac, arrive_s.

    The ONE home for the defaults. `video_finish.overlays` records the resolved anchor in its
    sidecar through this rather than restating "outgoing means right" beside the scene that
    decides it; two copies of a default is a drift nobody notices until a bubble lands on the
    wrong side of the phone.
    """
    return asdict(_resolve_bubble(bubble))


def _faded(bands: tuple[Image.Image, ...], a: float) -> Image.Image:
    """The layer at whole-element opacity ``a``, scaled on its alpha band."""
    r, g, b, alpha = bands
    return Image.merge("RGBA", (r, g, b, alpha.point(lambda v: round(v * a))))


def _missing_glyphs(font, text: str) -> list[str]:
    """Every character in ``text`` the face cannot draw, so the caller decides, not the tofu box.

    An emoji in the copy is a real decision — keep it and vendor a face that has it, or cut it —
    and a rendered box makes that decision silently in the wrong direction on every frame.
    """

    def _mask(ch: str) -> tuple[tuple[int, int], bytes]:
        mask = font.getmask(ch)
        return mask.size, bytes(mask)

    notdef = _mask(_NO_GLYPH_PROBE)
    return sorted({ch for ch in text if not ch.isspace() and _mask(ch) == notdef})


def _luma(rgb: tuple[int, int, int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _dark_ink(palette: _Palette) -> tuple[int, int, int]:
    """The kit's own dark colour, if it has one; an achromatic literal otherwise (never a hue)."""
    for field in ("text", "canvas"):
        rgb = _hex_to_rgb(getattr(palette, field), field=field)
        if _luma(rgb) < 128:
            return rgb
    return (24, 24, 28)


def _light_ink(palette: _Palette) -> tuple[int, int, int]:
    text = _hex_to_rgb(palette.text, field="text")
    return text if _luma(text) >= 128 else (255, 255, 255)


@dataclass(frozen=True)
class _Style:
    fill: tuple[int, int, int]
    fill_alpha: int
    ink: tuple[int, int, int]
    outline: tuple[int, int, int] | None


def _style(kind: str, palette: _Palette) -> _Style:
    """Fill and glyph colours per kind, chosen by luminance rather than by role name.

    A tenant's `accent` may be light or dark; the glyph colour is what keeps the words legible on
    it either way. Incoming is the light, quiet bubble on every kit — a dark-surface kit gets its
    surface lifted toward white rather than a bubble that vanishes into a night-room plate.
    """
    if kind == "outgoing":
        fill = _hex_to_rgb(palette.accent, field="accent")
        ink = _light_ink(palette) if _luma(fill) < 140 else _dark_ink(palette)
        return _Style(fill, 255, ink, None)
    if kind == "incoming":
        surface = _hex_to_rgb(palette.surface, field="surface")
        fill = surface if _luma(surface) >= 160 else _lerp_color(surface, (255, 255, 255), 0.88)
        ink = _dark_ink(palette)
        return _Style(fill, 255, ink, _lerp_color(fill, ink, 0.25))
    return _Style(_dark_ink(palette), round(255 * 0.85), _light_ink(palette), None)


@dataclass(frozen=True)
class _Layout:
    """Everything measured before frame zero: the font, the wrapped lines, and the geometry."""

    font: object
    lines: list[str]
    body: tuple[int, int, int, int]
    tail: tuple[tuple[int, int], ...]
    box: tuple[int, int, int, int]
    pad_x: int
    pad_y: int
    radius: int
    line_h: int
    band_bottom: int
    style: _Style


def _wrap_paragraphs(text: str, font, inner_w: int, probe: ImageDraw.ImageDraw) -> list[str]:
    """Greedy-wrap each hard-broken paragraph on its own; an empty paragraph keeps its line."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines.extend(_wrap(paragraph, font, inner_w, probe) or [""])
    return lines


def _fit_lines(
    face: Path, probe: ImageDraw.ImageDraw, bubble: _Bubble, *, w: int, h: int, pad_x: int
) -> tuple[object, list[str]]:
    """Wrap, then fit — in that order, for the reason `message-card` gives.

    Wrapping spends the width first; the fit is what refuses when even the line ceiling cannot
    hold the copy at the floor size. A hard break ("\\n") is honoured as a paragraph boundary,
    and each paragraph wraps on its own.

    A refusal names the smallest ``w_frac`` at which the copy WOULD fit, when one exists under
    the safe area. At 9:16 the frame is narrow and the type floor is not: the default width
    holds about fourteen characters a line, and a two-line message from a real shot list wrapped
    to six. "Shorten the copy" is one of two decisions; the other is to give the bubble the room
    the phone in frame can spare, and only the person looking at the plate knows which.
    """
    if bubble.kind == "chip":
        start_px, floor_px = round(h * 0.028), round(h * 0.022)
    else:
        start_px, floor_px = round(h * 0.034), round(h * 0.026)
    max_lines = _MAX_LINES[bubble.kind]
    inner_w = round(w * bubble.w_frac) - 2 * pad_x
    if inner_w <= 0:
        raise SceneError(f"chat bubble: w_frac {bubble.w_frac} leaves no room for text")

    missing = _missing_glyphs(_font(face, start_px), bubble.text)
    if missing:
        raise SceneError(
            f"chat bubble: the caption face has no glyph for {missing} — an emoji or symbol in "
            "the copy is a decision (vendor a face that carries it, or cut it), not a tofu box"
        )

    size = start_px
    font = _font(face, size)
    lines = _wrap_paragraphs(bubble.text, font, inner_w, probe)
    while len(lines) > max_lines and size > floor_px:
        size = max(floor_px, round(size * 0.92))
        font = _font(face, size)
        lines = _wrap_paragraphs(bubble.text, font, inner_w, probe)
    if len(lines) > max_lines:
        noun = "chip" if bubble.kind == "chip" else "bubble"
        remedy = "shorten the copy"
        # The widest a bubble may be is the safe area's own width; past that it is not beside
        # the phone, it is the frame.
        ceiling = round((1.0 - 2 * 0.06) * 100) / 100
        for step in range(int(bubble.w_frac * 100) + 2, int(ceiling * 100) + 1, 2):
            candidate = step / 100
            trial_w = round(w * candidate) - 2 * pad_x
            if len(_wrap_paragraphs(bubble.text, font, trial_w, probe)) <= max_lines:
                remedy += f", or set w_frac={candidate:.2f} (it fits there at the floor size)"
                break
        raise SceneError(
            f"chat bubble: the {noun} wraps to {len(lines)} lines at its {font.size}px floor, "
            f"past the {max_lines}-line ceiling — {remedy}"
        )
    for line in lines:
        if line:
            _fit_or_refuse(
                probe, face, line, inner_w, font.size, floor_px, where=f"chat bubble: {bubble.kind}"
            )
    return font, lines


def _layout(
    *, kit: dict, ratio: str, bubble: _Bubble, font_role: str, repo_root: Path | None
) -> _Layout:
    area = _resolve_area(ratio)
    palette = _load_palette(kit)
    try:
        faces = load_faces(kit, repo_root=repo_root)
        face = (
            faces.caption
            if font_role == "caption"
            else load_face(kit, role=font_role, repo_root=repo_root)
        )
    except FontMissing as exc:
        raise SceneError(str(exc)) from exc

    w, h = area.width, area.height
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    # Message-app proportions, not card proportions: a bubble's inset is roughly two thirds of
    # its type size across and half of it down. `message-card`'s `INSET["lg"]` is right for a
    # full-width card and wrong here — on a 9:16 frame it ate a fifth of the bubble's width.
    pad_x = px(INSET["sm"], h)
    pad_y = px(INSET["xs"], h)
    max_w = round(w * bubble.w_frac)

    font, lines = _fit_lines(face, probe, bubble, w=w, h=h, pad_x=pad_x)
    line_h = round(font.size * 1.30)
    text_w = max((probe.textlength(line, font=font) for line in lines if line), default=0.0)
    bubble_w = min(max_w, round(text_w) + 2 * pad_x)
    bubble_h = 2 * pad_y + line_h * len(lines)

    # The bubble hugs the safe area on its own side; the tail points toward the other, where the
    # phone is. `right` means the bubble sits at the right edge and its tail points LEFT.
    # `left`/`right`/`top`/`bottom` are EXCLUSIVE bounds — the rectangle a sidecar reader wants.
    if bubble.side == "right":
        right = w - round(w * area.right)
        left = right - bubble_w
    else:
        left = round(w * area.left)
        right = left + bubble_w

    # Vertically centred where the caller asked, then held inside the text band. The bottom of
    # that band is the caption's own rows, and two legible strings in the same pixels is the
    # defect `_text_band` exists to refuse — so a bubble long enough to reach them is lifted, and
    # its measured box (not the asked-for centre) is what the sidecar records.
    band_top, band_bottom = _text_band(h, area)
    if bubble_h > band_bottom - band_top:
        raise SceneError(
            f"chat bubble: the bubble is {bubble_h}px tall inside a {band_bottom - band_top}px "
            "text band — shorten the copy"
        )
    top = round(h * bubble.y_frac) - bubble_h // 2
    top = max(band_top, min(top, band_bottom - bubble_h))
    bottom = top + bubble_h

    # Pillow's rectangle and polygon primitives are INCLUSIVE of their far edge, so the drawn
    # coordinates stop one pixel short of the exclusive bounds. Measured, not assumed: with the
    # far edge at `bottom`, a bubble lifted to the band's floor painted its last row exactly on
    # the caption's first, and the box the sidecar recorded was one column narrower than the ink.
    body = (left, top, right - 1, bottom - 1)

    # A pill for the chip (half its height), a soft corner for a bubble.
    radius = bubble_h // 2 if bubble.kind == "chip" else px(RADIUS["md"], h)
    tail: tuple[tuple[int, int], ...] = ()
    box = (left, top, right, bottom)
    if bubble.kind != "chip":
        tail_w = px(INSET["sm"], h)
        tail_h = min(tail_w, radius)
        y_edge = bottom - 1
        if bubble.side == "right":
            tail = ((left + radius, y_edge), (left - tail_w, y_edge), (left, y_edge - tail_h))
            box = (left - tail_w, top, right, bottom)
        else:
            x_edge = right - 1
            tail = ((x_edge - radius, y_edge), (x_edge + tail_w, y_edge), (x_edge, y_edge - tail_h))
            box = (left, top, right + tail_w, bottom)

    return _Layout(
        font=font,
        lines=lines,
        body=body,
        tail=tail,
        box=box,
        pad_x=pad_x,
        pad_y=pad_y,
        radius=radius,
        line_h=line_h,
        band_bottom=band_bottom,
        style=_style(bubble.kind, palette),
    )


def bubble_geometry(
    *,
    kit: dict,
    ratio: str,
    bubble: dict | None = None,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> dict[str, int]:
    """The pixel rectangle the bubble (body plus tail, not its shadow) will occupy at rest.

    Measured by the same layout the renderer draws from, so a sidecar built on it records where
    the bubble IS rather than where a caller guessed it would be. Raises the same `SceneError`
    the render would, for copy that will not fit.
    """
    spec = _resolve_bubble(bubble)
    layout = _layout(kit=kit, ratio=ratio, bubble=spec, font_role=font_role, repo_root=repo_root)
    x0, y0, x1, y1 = layout.box
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _draw_layer(w: int, h: int, layout: _Layout) -> Image.Image:
    """The bubble at rest, fully opaque, on a transparent frame — drawn ONCE per scene.

    Nothing in it moves between frames except its whole-element alpha and travel, so it is built
    here and scaled per frame the way `_paste_logo` scales a mark: on the alpha band, never by
    blending toward a flat colour, because the shadow's own soft edge would flatten into a hard
    grey rectangle at partial fade.
    """
    x0, y0, x1, y1 = layout.body
    style = layout.style
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # A soft drop shadow UNDER the bubble, not a scrim over the frame: the picture this lands on
    # has to stay visible, because the phone and the hand holding it are the shot.
    offset = max(1, round(h * 0.004))
    blur = max(1, round(h * 0.006))
    shadow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x0, y0 + offset, x1, y1 + offset), radius=layout.radius, fill=round(255 * 0.35)
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    black = Image.new("L", (w, h), 0)
    layer.alpha_composite(Image.merge("RGBA", (black, black, black, shadow)))

    body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(body)
    fill = (*style.fill, style.fill_alpha)
    draw.rounded_rectangle(layout.body, radius=layout.radius, fill=fill)
    if layout.tail:
        draw.polygon(list(layout.tail), fill=fill)
    if style.outline is not None:
        draw.rounded_rectangle(
            layout.body,
            radius=layout.radius,
            outline=(*style.outline, 255),
            width=stroke_px("hairline", h),
        )
    text_top = y0 + layout.pad_y
    for n, line in enumerate(layout.lines):
        draw.text(
            (x0 + layout.pad_x, text_top + n * layout.line_h),
            line,
            font=layout.font,
            fill=(*style.ink, 255),
        )
    layer.alpha_composite(body)
    return layer


def render_chat_bubble_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    bubble: dict | None = None,
    repo_root: Path | None = None,
) -> int:
    """Write the numbered RGBA PNG sequence for one bubble arriving beside a phone. Returns the
    frame count.

    Frame zero is fully transparent by construction: the shot opens on its own picture, and the
    bubble lands at ``arrive_s`` — rising into place for a bubble (a message is sent), settling in
    place for a chip (a tap). The whole element arrives together; a message is read whole.
    """
    spec = _resolve_bubble(bubble)
    if spec.arrive_s >= duration_s:
        raise SceneError(
            f"chat bubble: arrive_s={spec.arrive_s} is not inside a {duration_s}s shot — the "
            "bubble would never appear"
        )
    area = _resolve_area(ratio)
    motion = resolve_motion(kit)
    layout = _layout(kit=kit, ratio=ratio, bubble=spec, font_role=font_role, repo_root=repo_root)
    w, h = area.width, area.height
    layer = _draw_layer(w, h, layout)
    bands = layer.split()

    reveal = _entrance(
        0,
        motion=motion,
        duration_s=duration_s,
        h=h,
        start=spec.arrive_s / duration_s,
        cls="scale_in" if spec.kind == "chip" else "fade_up",
        room=layout.band_bottom - layout.box[3],
    )

    frames: list[Image.Image] = []
    n_frames = _frame_count(fps, duration_s)
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        a, dy = _arrive(reveal, t)
        if a > 0:
            out.alpha_composite(layer if a >= 1 else _faded(bands, a), dest=(0, max(0, dy)))
        frames.append(out)

    return _write_frames(frames, out_dir, prefix="chat-bubble", alpha=True)


__all__ = [
    "_BUBBLE_DEFAULTS",
    "_MAX_LINES",
    "bubble_geometry",
    "render_chat_bubble_frames",
    "resolve_bubble",
]
