from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ...captions import FontMissing, load_faces
from ...design_tokens import RADIUS
from ..base import SceneError
from ..draw import (
    CAPTION_BAND_TOP_FRAC,
    _backdrop,
    _font,
    _tracked_w,
)
from ..fit import _PAYLOAD_MIN_H_FRAC, _fit_tracked
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area
from .caller_row_frame import _CallerRowLayout, _compose_caller_row_frame

# ── caller-row — the whole of Act 1 in one composition ────────────────────────────────────────
#
# WHY THIS EXISTS. The 2026-08-29 cut alternated a caller with a record card, four times: a face,
# then a database, then a face, then a database. The operator's note on 2026-09-04 was that the
# cards "not useful in the beginning section" — and the count is the argument, 63% of Act 1 was
# screen UI and every card cut AWAY from the person whose call it was describing. The outcome of
# each call belongs inside the call frame, not on a card after it.
#
# So the four callers stop taking turns and share one frame. Three sit dimmed while the fourth is
# lit and live, and the row order never changes, which is what lets the composition state a fact
# no card has to assert: these are four separate businesses being rung, not one queue.
#
# THE LABEL IS THE LOAD-BEARING PART, and it is worth saying why, because the first sketch got it
# wrong. A single aura serving all four reads as ONE agent fielding four calls — the opposite of
# the film's claim. Each caller's agent therefore carries a POSSESSIVE label naming whose it is
# ("THE BANK'S AGENT"), re-badged per beat. A category label ("AGENT · A BANK") does not do this;
# it reads as one agent wearing four hats. Colour cannot do it either — the kit has one accent,
# and four hues would fight the brand kit to make a point the words already make.
#
# LAYERING. This scene draws the GROUND: the field, the three dimmed thumbs, the active tile's
# frame, the aura and the labels. The live caller is composited INTO the active tile afterwards by
# `video_finish.inset_video`, which is why the active tile is drawn as a lit, empty frame here —
# something lands on it. The reverse layering (`overlay_frames`) cannot serve this: an RGBA
# sequence always goes on top, and footage cannot be drawn into a PNG.


#: FIXED LAYOUT, 2026-09-04. The v9 row put the lit caller INSIDE the strip, so the three dimmed
#: ones sat either side of it and their positions moved every beat — the operator asked for them
#: to stay put. Now nothing moves: one large tile, a strip of four thumbs beneath it in name order
#: with the live one ringed, and the agent's panel to the right. The speaker appears twice, which
#: is what every video-call UI does and what makes a participant rail readable.
#:
#: Stacking the strip UNDER the large tile rather than beside it also BUYS face size instead of
#: costing it: the tile no longer shares its row with four thumbs, so it goes 796 -> 1020px wide
#: and the face lands at ~47px on a 400px phone against v9's ~36px.
_ACTIVE_W_FRAC = 1020 / 1920
_THUMB_W_FRAC = 88 / 1920
_GUTTER_FRAC = 18 / 1920
_STRIP_GAP_FRAC = 18 / 1080
_TILE_AR = 9 / 16

#: The gap between the callers and the agent, and the agent panel's width. THIS GAP IS THE POINT:
#: an aura under the lit caller read as that person's nameplate. A call has two ends, so the frame
#: has two zones with a rule between them.
_ZONE_GAP_FRAC = 60 / 1920
_AGENT_W_FRAC = 420 / 1920


#: Thumbs are centre-cropped to the face before scaling. A whole 16:9 room at 200px reads as
#: noise; the crop is what makes three small tiles read as three PEOPLE waiting their turn.
_THUMB_CROP_W_FRAC = 0.62
_THUMB_CROP_TOP_FRAC = 0.06

#: How far an inactive caller is pushed down. Desaturated to a quarter of its colour and dimmed to
#: just under half — present, plainly not the subject. Both are needed: dimming alone leaves a
#: colourful tile that still competes, desaturating alone leaves a bright grey one that competes
#: harder.
_DIM_COLOR = 0.25
_DIM_BRIGHT = 0.45


@dataclass(frozen=True)
class _CallerRowSpec:
    prefix: str
    #: Which slot of the row is live, 0-based, in the row's fixed name order.
    active: int
    #: The SECTOR this agent belongs to, never a real brand. The panel supplies the
    #: possessive with its "AGENT FOR" overline, which is what lets the payload line stay
    #: short enough to hold the type floor in a 356px column.
    org: str
    index: int
    #: Seconds already elapsed when the shot starts, so four calls do not all read 00:00.
    start_s: float
    of: int = 4


_ROW_BANK = _CallerRowSpec(prefix="caller-row-bank", active=0, org="A BANK", index=1, start_s=6)
_ROW_CLINIC = _CallerRowSpec(
    prefix="caller-row-clinic", active=1, org="A CLINIC", index=2, start_s=11
)
_ROW_HOTEL = _CallerRowSpec(prefix="caller-row-hotel", active=2, org="A HOTEL", index=3, start_s=8)
_ROW_TELCO = _CallerRowSpec(
    prefix="caller-row-telco",
    active=3,
    org="A PHONE COMPANY",
    index=4,
    start_s=14,
)


#: Interior padding for the agent panel, from the token ladder's inset scale rather than a
#: literal — the `compare_rows` cards shipped at 13px on a 1596px panel and every badge read as
#: welded to the border.
_AGENT_PAD_FRAC = 0.030


def _wrap_tracked(draw, text: str, font, max_w: int) -> list[str]:
    """Greedy word wrap measured the way `_text_tracked` DRAWS — tracking adds width the plain
    text metric cannot see, which is the same mismatch `_fit_tracked` exists to close."""
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = f"{cur} {word}".strip()
        if cur and _tracked_w(draw, trial, font) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _block_left(w: int) -> int:
    """Left edge of the whole composition — callers plus gap plus agent — centred as one block."""
    block = round(w * _ACTIVE_W_FRAC) + round(w * _ZONE_GAP_FRAC) + round(w * _AGENT_W_FRAC)
    return round((w - block) / 2)


def _content_top(w: int, h: int) -> int:
    """Top of the tile stack, centred in the band a burned caption leaves.

    Centred, not hung from the top: the stack's height changes with the thumb strip, and a hung
    layout would leave all the slack at the bottom where the caption already lives.
    """
    top = round(h * 0.080)
    bottom = round(h * CAPTION_BAND_TOP_FRAC)
    stack = _active_h(w) + round(h * _STRIP_GAP_FRAC) + _thumb_h(w)
    return top + max(0, (bottom - top - stack) // 2)


def _active_h(w: int) -> int:
    return round(round(w * _ACTIVE_W_FRAC) * _TILE_AR)


def _thumb_h(w: int) -> int:
    return round(round(w * _THUMB_W_FRAC) * _TILE_AR)


def active_rect(w: int, h: int, active: int = 0) -> tuple[int, int, int, int]:
    """Where the live caller's footage is composited. FIXED — it does not depend on which caller
    is speaking, which is the whole point of the 2026-09-04 rework and what lets one inset rect
    serve all four beats."""
    return (_block_left(w), _content_top(w, h), round(w * _ACTIVE_W_FRAC), _active_h(w))


def _slots(w: int, h: int, active: int = 0) -> list[tuple[int, int, int, int]]:
    """The four thumb rects, left to right in NAME ORDER, beneath the large tile. Fixed for every
    beat: only which one is lit changes."""
    tw, th = round(w * _THUMB_W_FRAC), _thumb_h(w)
    gutter = round(w * _GUTTER_FRAC)
    y = _content_top(w, h) + _active_h(w) + round(h * _STRIP_GAP_FRAC)
    # Centred UNDER the large tile, not left-aligned to it: four 88px thumbs occupy 406 of the
    # tile's 1020px, and hanging them off the left edge reads as a strip that ran out rather than
    # as a participant rail.
    strip_w = 4 * tw + 3 * gutter
    x = _block_left(w) + (round(w * _ACTIVE_W_FRAC) - strip_w) // 2
    rects = []
    for _ in range(4):
        rects.append((x, y, tw, th))
        x += tw + gutter
    return rects


def agent_panel_rect(w: int, h: int, active: int = 0) -> tuple[int, int, int, int]:
    """The agent's own zone, right of the callers and of a divider rule. Shares the large tile's
    top and height so the two ends of the call read as equals."""
    x = _block_left(w) + round(w * _ACTIVE_W_FRAC) + round(w * _ZONE_GAP_FRAC)
    return (x, _content_top(w, h), round(w * _AGENT_W_FRAC), _active_h(w))


def _tile_image(src: Path, box: tuple[int, int], *, dim: bool) -> Image.Image:
    """A caller still, cropped to the face and fitted to ``box``."""
    try:
        img = Image.open(src).convert("RGB")
    except OSError as exc:  # pragma: no cover — surfaced with the path, which is the useful part
        raise SceneError(f"caller still {src} could not be read: {exc}") from exc
    sw, sh = img.size
    cw = round(sw * _THUMB_CROP_W_FRAC)
    ch = round(cw * _TILE_AR)
    cx = round((sw - cw) / 2)
    cy = min(round(sh * _THUMB_CROP_TOP_FRAC), max(0, sh - ch))
    img = img.crop((cx, cy, cx + cw, cy + min(ch, sh - cy))).resize(box, Image.LANCZOS)
    if dim:
        img = ImageEnhance.Color(img).enhance(_DIM_COLOR)
        img = ImageEnhance.Brightness(img).enhance(_DIM_BRIGHT)
    return img


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius, fill=255)
    out = img.copy()
    out.putalpha(mask)
    return out


def render_caller_row_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    spec: _CallerRowSpec = _ROW_BANK,
    stills: Sequence[Path] | None = None,
    speak_from_s: float | None = None,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """The ground for one call beat: four tiles, three dimmed, one lit and awaiting its footage.

    ``stills`` is the four callers' frames in ROW ORDER — required, and refused if not exactly
    four. A missing still would otherwise ship as a blank tile that looks deliberate in QA.

    ``speak_from_s`` is when the agent starts answering; the aura idles before it and breathes at
    full amplitude after. Defaults to the moment the caller's own line ends being unknown here, so
    the caller passes it.
    """
    if stills is None or len(stills) != 4:
        raise SceneError(
            f"{spec.prefix}: needs exactly 4 caller stills in row order, got "
            f"{0 if stills is None else len(stills)}"
        )
    area = _resolve_area(ratio)
    palette = _load_palette(kit)
    try:
        faces = load_faces(kit, repo_root=repo_root)
        face = faces.caption if font_role == "caption" else getattr(faces, font_role)
    except FontMissing as exc:
        raise SceneError(str(exc)) from exc

    w, h = area.width, area.height
    accent = _hex_to_rgb(palette.accent, field="accent")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")

    thumbs_rects = _slots(w, h)
    ax, ay, aw, ah = active_rect(w, h)
    radius = round(h * RADIUS["sm"])
    ground = _backdrop(w, h, palette)

    # Composed ONCE and pasted: a LANCZOS resize of five stills per frame is minutes of wall clock
    # for a picture that never changes.
    tw, th = thumbs_rects[0][2], thumbs_rects[0][3]
    tiles = [
        _rounded(_tile_image(Path(stills[i]), (tw, th), dim=i != spec.active), round(h * 0.008))
        for i in range(4)
    ]
    # The large tile is covered by footage from `SETTLE_S` on, so what is drawn here only ever
    # shows during the settle: blurred and pushed down, which reads as a call connecting.
    connecting = _rounded(
        ImageEnhance.Brightness(
            _tile_image(Path(stills[spec.active]), (aw, ah), dim=False).filter(
                ImageFilter.GaussianBlur(max(2, round(h * 0.012)))
            )
        ).enhance(0.35),
        radius,
    )

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    gx, gy, gw, gh = agent_panel_rect(w, h)
    pad = round(h * _AGENT_PAD_FRAC)
    content_w = gw - pad * 2

    over_font = _font(face, round(h * 0.028))
    meta_font = _font(face, round(h * 0.028))
    label_font = _fit_tracked(
        probe,
        face,
        max(spec.org.split(), key=len),
        content_w,
        round(h * _PAYLOAD_MIN_H_FRAC),
        round(h * _PAYLOAD_MIN_H_FRAC),
    )
    lines = _wrap_tracked(probe, spec.org, label_font, content_w)
    # The floor is passed as BOTH the start and the floor above, so `_fit_tracked` cannot shrink
    # its way out of a column that is too narrow — it returns the floor and this refuses. The org
    # line is the payload of the whole composition (it is the only statement of whose agent this
    # is), so a copy change is the fix, never smaller type. The first build learned this one row
    # lower down, where "THE PHONE COMPANY'S AGENT" drew straight through the live timer.
    widest = max(_tracked_w(probe, ln, label_font) for ln in lines)
    if widest > content_w:
        raise SceneError(
            f"{spec.prefix}: {spec.org!r} has a word too long for the {content_w}px agent column "
            f"at the payload floor ({round(h * _PAYLOAD_MIN_H_FRAC)}px) — shorten the sector name"
        )

    # The stack is CENTRED in the space above the timer rather than hung from the panel's top.
    # Hung, a two-line sector ("A PHONE / COMPANY") grew downward into the timer while a one-line
    # one ("A BANK") left a hole — the panel looked like two different designs depending on which
    # call was live. Centring makes the four beats read as one component.
    aura_r = round(h * 0.052)
    gap_a = round(h * 0.036)
    gap_b = round(h * 0.020)
    line_h = label_font.size * 1.18
    stack_h = aura_r * 2 + gap_a + over_font.size + gap_b + line_h * len(lines)
    meta_top = gy + gh - pad - meta_font.size
    avail_top, avail_bot = gy + pad, meta_top - round(h * 0.024)
    stack_top = avail_top + max(0, (avail_bot - avail_top - stack_h) / 2)
    aura_cy = round(stack_top + aura_r)
    label_top = stack_top + aura_r * 2 + gap_a
    speak_at = duration_s if speak_from_s is None else speak_from_s

    geo = _CallerRowLayout(
        w=w,
        h=h,
        spec=spec,
        palette=palette,
        accent=accent,
        text_rgb=text_rgb,
        dim_rgb=dim_rgb,
        ground=ground,
        tiles=tiles,
        connecting=connecting,
        thumbs_rects=thumbs_rects,
        active_rect=(ax, ay, aw, ah),
        radius=radius,
        agent_rect=(gx, gy, gw, gh),
        rule_x=gx - round(w * _ZONE_GAP_FRAC) // 2,
        over_font=over_font,
        meta_font=meta_font,
        label_font=label_font,
        lines=lines,
        aura_r=aura_r,
        gap_b=gap_b,
        line_h=line_h,
        label_top=label_top,
        meta_top=meta_top,
        aura_cy=aura_cy,
        speak_at=speak_at,
    )
    n_frames = _frame_count(fps, duration_s)
    frames = [_compose_caller_row_frame(i / fps, geo) for i in range(n_frames)]
    return _write_frames(frames, out_dir, prefix=spec.prefix)
