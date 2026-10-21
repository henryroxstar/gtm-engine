from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import RADIUS, resolve_motion, stroke_px
from ..base import SceneError
from ..draw import (
    _arrive,
    _backdrop,
    _ease_in_out,
    _entrance,
    _font,
    _glow,
    _lerp_color,
    _panel,
    _text_band,
    _text_tracked,
    _text_w,
    _window,
)
from ..fit import _PAYLOAD_MIN_H_FRAC
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area


@dataclass(frozen=True)
class _RecordSpec:
    """One call's record. Fictional throughout, and named by SECTOR only — never an organisation.

    Inventing a plausible company name to sit on a record that is then shown mishandling a
    customer risks colliding with a real one, and the sector is the whole point anyway: the
    argument is that the same shape recurs across industries.
    """

    prefix: str
    sector: str
    systems: tuple[tuple[str, str], ...]  # (name, note) — note may be ""
    field_label: str
    old_value: str
    new_value: str
    wrote_index: int  # which system receives the write; -1 for a held/read-only variant
    claim: str = "they said so"
    #: The doubt `claim` answers. Defaulted to the original wording so every existing spec is
    #: unchanged; only a spec whose card isn't about a caller's identity (record-agent-identity)
    #: needs to override it — the question was hardcoded until this shipped a card where "how do
    #: we know it's really them?" answered by "not shared, not guessed" didn't pair up.
    ask: str = "how do we know it's really them?"


_BANK = _RecordSpec(
    prefix="record-bank",
    sector="CUSTOMER SYSTEMS",
    systems=(("CUSTOMER RECORDS", ""),),
    field_label="MAILING ADDRESS",
    old_value="14 Prior Lane, Ashford",
    new_value="2 Ferndale Court, Bexley",
    wrote_index=0,
    claim="verified · may change details",
)

_CLINIC = _RecordSpec(
    prefix="record-clinic",
    sector="PATIENT SYSTEMS",
    systems=(("PATIENT RECORD", ""), ("PRESCRIPTION ON FILE", "")),
    field_label="REPEAT PRESCRIPTION",
    old_value="last issued 6 months ago",
    new_value="renewed — collect today",
    wrote_index=1,
    claim="verified · repeats only",
)

_HOTEL = _RecordSpec(
    prefix="record-hotel",
    sector="GUEST SYSTEMS",
    systems=(("LOYALTY", "platinum · on record"), ("BOOKINGS", "")),
    field_label="CHECKOUT TIME",
    old_value="11:00",
    new_value="16:00 — no charge",
    wrote_index=1,
    claim="verified · platinum on record",
)

_TELCO = _RecordSpec(
    prefix="record-telco",
    sector="ACCOUNT SYSTEMS",
    systems=(("ACCOUNTS", ""), ("UPGRADES", "prestige tier"), ("STOCK", "")),
    field_label="ORDER PLACED",
    old_value="no order on file",
    # "ships" not "shipping": the grid renders all four values at one size, and this was the only
    # one that did not fit its cell at the payload floor — so `_fit_font` returned the floor and
    # let it run past the cell edge. Shorter copy, not smaller type.
    new_value="new handset — ships tonight",
    wrote_index=2,
    claim="verified · may sign",
)

#: The clean counterpart: the same record, nothing pending, and the identity line ANSWERED. It is
#: the only variant with ``wrote_index=-1`` — nothing is written, which is what makes it the
#: control shot the other four are read against.
_CLEAN = _RecordSpec(
    prefix="record-clean",
    sector="YOUR SYSTEMS",
    systems=(("YOUR RECORDS", "where your customer's real life is kept"),),
    field_label="STATUS",
    old_value="",
    new_value="nothing pending",
    wrote_index=-1,
    claim="checked, not assumed",
)

#: Replaces a real console screenshot at h07 (2026-08-31). The screenshot was a tight crop of a
#: live identity record whose visible DID literally carried the product name — naming the
#: product at 1:48 into a film that doesn't introduce it until 3:02, and doing it as a barely-
#: legible fragment of selected text rather than a composed shot. This card carries the same
#: fact (one agent, its own identity, an attributable log) with nothing borrowed from any real
#: console and nothing that pre-empts the reveal.
_AGENT_IDENTITY = _RecordSpec(
    prefix="record-agent-identity",
    sector="AGENT IDENTITY",
    systems=(("AUDIT LOG", "every action attributed to it"),),
    field_label="THIS AGENT",
    old_value="",
    new_value="20 actions logged",
    wrote_index=-1,
    claim="not shared, not guessed",
    ask="how do you tell one agent from another?",
)


def render_caller_record_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    spec: _RecordSpec = _BANK,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Write the numbered PNG sequence for one call's record beat. Returns the frame count.

    Four phases, each its own ``_window`` so they overlap the way a real interaction does rather
    than queueing: the systems light READ in sequence, the outgoing value strikes through, the
    incoming value types itself in under a caret, and the written system flips to WROTE. The
    identity line resolves last — it is the punchline, so it lands after the change it qualifies.
    """
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

    canvas_rgb = _hex_to_rgb(palette.canvas, field="canvas")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")

    w, h = area.width, area.height
    # Width is the caption safe area; HEIGHT is measured from the content and then centred in the
    # safe band. A card stretched to fill the safe area leaves a dead zone between the value and
    # the systems row whose size depends on how many systems the variant happens to have — which
    # is how the first cut earned "lots of white space". Fitting the card to its own stack makes
    # that structural rather than a value to keep re-tuning per variant.
    card_l = round(w * area.left)
    card_r = w - round(w * area.right)
    # Was `h - h*area.bottom` (0.88h at 16:9), which is the frame edge and not where a caption
    # lands. That is what put this card's own closing question at 0.73h, directly under the burned
    # caption. See `_text_band`.
    band_t, band_b = _text_band(h, area)
    pad = round((card_r - card_l) * 0.045)
    x0 = card_l + pad
    x1 = card_r - pad
    inner_w = x1 - x0

    overline_font = _font(faces.label, round(h * 0.042))
    badge_font = _font(faces.label, round(h * 0.040))
    label_font = _font(faces.label, round(h * 0.046))
    old_font = _font(faces.body, round(h * 0.062))
    new_font = _font(faces.heading, round(h * 0.078))
    sys_font = _font(face, round(h * _PAYLOAD_MIN_H_FRAC))
    note_font = _font(faces.body, round(h * 0.040))
    ask_font = _font(faces.body, round(h * 0.046))

    held = spec.wrote_index < 0
    n_frames = _frame_count(fps, duration_s)
    n_sys = len(spec.systems)

    # ── the content stack, measured once ────────────────────────────────────────────────────
    # Offsets are relative to the card's top edge, so the same numbers drive both the card's
    # height and every element's position — they cannot drift apart the way two hand-kept
    # coordinate sets do.
    sys_h = round(h * 0.124)
    sys_gap = round(h * 0.018)
    box_w = (inner_w - sys_gap * (n_sys - 1)) // n_sys
    dy_overline = pad
    dy_rule = dy_overline + round(h * 0.086)
    dy_label = dy_rule + round(h * 0.046)
    dy_old = dy_label + round(h * 0.070)
    dy_new = dy_old + (round(h * 0.098) if spec.old_value else round(h * 0.030))
    dy_value_end = dy_new + round(h * 0.090)
    dy_sys = dy_value_end + round(h * 0.052)  # the reach the connectors draw across
    dy_ask = dy_sys + sys_h + round(h * 0.040)
    card_h = dy_ask + round(h * 0.050) + pad

    # The card must stay INSIDE the caption safe band, not merely start at its top: a stack that
    # overruns pushes its own border down through the band and a burned caption then lands on the
    # card. Overflow is squeezed out of the inter-element gaps, which is where the slack is —
    # never out of the type, which is floored by _PAYLOAD_MIN_H_FRAC for a reason.
    band_h = band_b - band_t
    if card_h > band_h:
        squeeze = band_h / card_h
        dy_rule = round(dy_rule * squeeze)
        dy_label = round(dy_label * squeeze)
        dy_old = round(dy_old * squeeze)
        dy_new = round(dy_new * squeeze)
        dy_value_end = round(dy_value_end * squeeze)
        dy_sys = round(dy_sys * squeeze)
        dy_ask = round(dy_ask * squeeze)
        card_h = band_h

    card_t = band_t + max(0, (band_h - card_h) // 2)
    card_b = card_t + card_h

    # The value's row is reserved from frame zero so nothing reflows when it arrives — which means
    # the row sits empty until TYPE_START, and that emptiness is the frame's weakest moment. Hence
    # a tight read band: the incoming value is on screen by roughly the halfway point of even a
    # 3-second shot, rather than the two-thirds mark the first timing produced.
    READ_START, READ_SPAN = 0.03, 0.28  # systems light in sequence across this band
    STRIKE_START, STRIKE_END = 0.30, 0.44
    TYPE_START, TYPE_END = 0.40, 0.76
    WROTE_START, WROTE_END = 0.54, 0.66
    ASK_START, ASK_END = 0.68, 0.90

    motion = resolve_motion(kit)
    ground = _backdrop(w, h, palette)
    frames: list[Image.Image] = []
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = ground.copy()
        _panel(img, (card_l, card_t, card_r, card_b), palette, alpha=1.0, radius_frac=RADIUS["md"])
        draw = ImageDraw.Draw(img)

        y = card_t + dy_overline
        _text_tracked(draw, (x0, y), spec.sector, overline_font, dim_rgb)

        wrote_t = _ease_in_out(_window(t, WROTE_START, WROTE_END))
        if not held:
            word = "WROTE" if wrote_t > 0.5 else "READ"
            bw = _text_w(draw, word, badge_font)
            bx = x1 - bw - round(h * 0.024)
            by = y - round(h * 0.008)
            fill = _lerp_color(canvas_rgb, accent_rgb, 0.18 + 0.82 * wrote_t)
            draw.rounded_rectangle(
                (bx - round(h * 0.020), by, x1, by + round(h * 0.062)),
                radius=round(h * RADIUS["xs"]),
                fill=fill,
                outline=accent_rgb,
                width=stroke_px("hairline", h),
            )
            draw.text((bx, by + round(h * 0.010)), word, font=badge_font, fill=text_rgb)

        y = card_t + dy_rule
        draw.line(
            (x0, y, x1, y),
            fill=_lerp_color(canvas_rgb, text_rgb, 0.16),
            width=stroke_px("hairline", h),
        )

        # ── the field: the one thing that changes, and the largest thing in frame ───────────
        _text_tracked(draw, (x0, card_t + dy_label), spec.field_label, label_font, dim_rgb)

        y = card_t + dy_old
        if spec.old_value:
            draw.text((x0, y), spec.old_value, font=old_font, fill=dim_rgb)
            strike = _ease_in_out(_window(t, STRIKE_START, STRIKE_END))
            if strike > 0:
                ow = _text_w(draw, spec.old_value, old_font)
                sy = y + round(h * 0.034)
                draw.line(
                    (x0, sy, x0 + round(ow * strike), sy),
                    fill=dim_rgb,
                    width=stroke_px("heavy", h),
                )

        y = card_t + dy_new
        type_t = _ease_in_out(_window(t, TYPE_START, TYPE_END))
        shown = spec.new_value[: round(len(spec.new_value) * type_t)]
        dot_r = round(h * 0.012)
        if type_t > 0:
            cy = y + round(h * 0.044)
            draw.ellipse((x0, cy - dot_r, x0 + 2 * dot_r, cy + dot_r), fill=accent_rgb)
        tx = x0 + round(h * 0.048)
        if shown:
            draw.text((tx, y), shown, font=new_font, fill=text_rgb)
        # A caret only while the value is still arriving — a blinking caret on a settled field
        # reads as a form waiting for input, which is the opposite of what this shot says.
        if 0 < type_t < 1:
            cx = tx + round(_text_w(draw, shown, new_font)) + round(h * 0.008)
            draw.rectangle(
                (cx, y + round(h * 0.010), cx + round(h * 0.008), y + round(h * 0.076)),
                fill=accent_rgb,
            )
        # ── the systems of record, beneath: how many places this one answer touched ─────────
        value_bottom = card_t + dy_value_end
        sys_y = card_t + dy_sys
        unlit_edge = _lerp_color(canvas_rgb, text_rgb, 0.16)
        for idx, (name, note) in enumerate(spec.systems):
            bx = x0 + idx * (box_w + sys_gap)
            # Staggered so the reads are sequential, not simultaneous: an agent that touched three
            # systems did so one after another, and the beat is how MANY, not how fast.
            share = READ_SPAN / max(1, n_sys)
            # A system box is a sibling arriving inside a card that is already on screen, so it
            # takes `scale_in` from the ported vocabulary rather than the panel's `fade_up`. The
            # authored READ cadence still says WHEN — the tempo only says how it arrives.
            lit, sys_dy = _arrive(
                _entrance(
                    idx,
                    motion=motion,
                    duration_s=duration_s,
                    h=h,
                    start=READ_START + idx * share,
                    cls="scale_in",
                ),
                t,
            )
            if held:
                lit, sys_dy = 0.0, 0
            is_target = idx == spec.wrote_index
            target_t = wrote_t if is_target else 0.0

            # A reach DOWN from the field to each system, drawn as that system is read. Without it
            # the first half of the shot is a static card with a badge changing in the corner —
            # and the whitespace it crosses is the frame's emptiest region, so the one element
            # that carries meaning through the dead zone is also the one that fills it.
            if lit > 0:
                lx = bx + box_w // 2
                y_from = value_bottom
                y_to = sys_y
                draw.line(
                    (lx, y_from, lx, y_from + round((y_to - y_from) * lit)),
                    fill=_lerp_color(canvas_rgb, accent_rgb, 0.30 + 0.70 * lit),
                    width=stroke_px("emphasis", h),
                )

            edge = _lerp_color(unlit_edge, accent_rgb, lit)
            fill = _lerp_color(canvas_rgb, accent_rgb, 0.22 * lit)
            if target_t > 0:
                edge = _lerp_color(edge, text_rgb, target_t)
                fill = _lerp_color(fill, text_rgb, 0.14 * target_t)
            # The system being read is the live element, so it — and only it — blooms, UNDER its
            # own box. Applied per-lit-box rather than to the row, which is what keeps the accent
            # meaning "this one" instead of decorating every panel equally.
            if max(lit, target_t) > 0.5:
                _glow(
                    img,
                    (bx, sys_y + sys_dy, bx + box_w, sys_y + sys_dy + sys_h),
                    accent_rgb,
                    strength=0.22 * max(lit, target_t),
                    spread=0.018,
                )
                draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (bx, sys_y + sys_dy, bx + box_w, sys_y + sys_dy + sys_h),
                radius=round(h * RADIUS["sm"]),
                fill=fill,
                outline=edge,
                width=(
                    stroke_px("emphasis", h) if max(lit, target_t) > 0.5 else stroke_px("rule", h)
                ),
            )
            _text_tracked(
                draw,
                (bx + round(h * 0.022), sys_y + sys_dy + round(h * 0.020)),
                name,
                sys_font,
                _lerp_color(dim_rgb, text_rgb, max(lit, target_t)),
            )
            if note:
                draw.text(
                    (bx + round(h * 0.022), sys_y + sys_dy + round(h * 0.072)),
                    note,
                    font=note_font,
                    fill=_lerp_color(dim_rgb, accent_rgb, 0.5 * lit),
                )

        # ── the identity line — the punchline, last ─────────────────────────────────────────
        ask_t = _ease_in_out(_window(t, ASK_START, ASK_END))
        if ask_t > 0:
            ay = card_t + dy_ask
            draw.text(
                (x0, ay), spec.ask, font=ask_font, fill=_lerp_color(canvas_rgb, dim_rgb, ask_t)
            )
            aw = _text_w(draw, spec.claim, ask_font)
            draw.text(
                (x1 - aw, ay),
                spec.claim,
                font=ask_font,
                fill=_lerp_color(canvas_rgb, accent_rgb, ask_t),
            )

        frames.append(img)

    return _write_frames(frames, out_dir, prefix=spec.prefix)
