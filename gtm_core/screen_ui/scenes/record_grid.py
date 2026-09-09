from __future__ import annotations

import math
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
    _lerp_color,
    _panel,
    _text_band,
    _text_w,
    _window,
)
from ..fit import _PAYLOAD_MIN_H_FRAC, _fit_or_refuse
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area
from .caller_record import _BANK, _CLINIC, _HOTEL, _TELCO, _RecordSpec

#: The four calls, in the order the film tells them. The grid scene reads this rather than keeping
#: a second copy of the content, so a wording change in one call cannot leave the payoff shot
#: quoting the old version of itself.
_CALLS: tuple[_RecordSpec, ...] = (_BANK, _CLINIC, _HOTEL, _TELCO)

#: The fraction of the shot across which all four cards arrive, one per call. MODULE-LEVEL, and
#: that is the point: the SFX pass times its four returning tones off "each renderer's own
#: animation schedule, not guessed", which is only possible if the schedule is a value another
#: module can read. It was a local until 2026-09-03, so the cue times were transcribed by hand —
#: and when the ported entrance changed when a card finishes arriving, the transcription was the
#: thing that would have silently stayed right-looking and wrong.
ARRIVE_SPAN = 0.42

#: The one question the grid hands to Act 2. Kept as a question rather than a claim: the card's
#: job is to close four calls that worked and open the three that ask why that is hard.
QUESTION = "So how do you build trust into a voice agent?"
#: Where the shared question resolves under all four, and where the four claims flare together.
ASK_WINDOW = (0.46, 0.72)
FLARE_START = 0.76


def render_record_grid_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """The payoff of act one: all four calls at once, then the one field none of them filled.

    Built from ``_CALLS`` rather than from its own copy of the four outcomes — a summary shot that
    drifts out of step with the calls it summarises is not a cosmetic error, it is a payoff that
    no longer matches what the audience just watched.
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
    gx0 = round(w * area.left)
    gx1 = w - round(w * area.right)
    # The grid's bottom row held text down to 0.88h, so the burned caption crossed both lower
    # cells. See `_text_band` — the grid now packs into the clear band above it.
    gy0, gy1 = _text_band(h, area)
    gap = round(h * 0.026)
    # One travel step is RESERVED at the foot of the band rather than clamped away. The bottom
    # row's cards rise into place, and a grid that filled the band edge-to-edge would have to
    # either arrive without moving — losing the fix this card needed most — or carry its own type
    # down into the rows a caption is burned into. Giving the motion room costs 4% of the band,
    # which keeps the card at ~0.92 of it: inside the [layout].safe_box_fill_min..max window the
    # tenant's kit declares, so the grid still reads as the same depth as its neighbours.
    motion = resolve_motion(kit)
    travel_room = _entrance(0, motion=motion, duration_s=max(0.1, duration_s), h=h).travel_px
    cell_w = (gx1 - gx0 - gap) // 2
    # ONE QUESTION UNDER THE GRID, not four inside it (operator, 2026-09-04). Every cell used to
    # carry "who asked?" against its own answer, which read as an odd little interrogation
    # repeated four times — and once Act 1's calls became verified calls, four separate questions
    # about four settled outcomes had nothing left to ask. The grid states what happened; one
    # question underneath turns it into the hand-off to Act 2's three.
    question_h = round(h * 0.105)
    cell_h = (gy1 - gy0 - gap - travel_room - question_h) // 2
    # 0.038, not 0.045 — and a PARTIAL mitigation, not a fix. Say so plainly, because the number
    # looks like a solution and is not.
    #
    # The four cells share one type size but not one string length. Measured 2026-08-31 at 16:9
    # with `audit-fit`: the longest outcome ('new handset — ships tonight') needs 749px at the
    # 54px payload floor. At inset 0.045 its box was 774px — tolerance x1.033, i.e. a face 3.3%
    # wider refuses it, which is luck holding, not a margin. At 0.038 the box is 786px and the
    # tolerance is x1.049. Better; still the tightest string in the whole deck, where everything
    # else clears x1.22.
    #
    # NEITHER LEVER REACHES x1.15, and this is why no future inset tweak should be attempted:
    #   * horizontally, x1.15 needs an 861px box and the ENTIRE cell is 850px — impossible at
    #     inset zero;
    #   * vertically, wrapping the value to a second line needs ~62px (54px floor plus leading)
    #     and the cell has 35px of slack above the 'who asked?' rule.
    # So the value copy and a 2x2 grid at the payload floor are in genuine conflict. The fix is
    # shorter values or a different layout — a content/design call, made against these numbers,
    # not another 6px shaved off the inset. `_PAYLOAD_MIN_H_FRAC` is what stops "make it fit"
    # from quietly costing legibility and is the last thing to move.
    inset = round(cell_w * 0.038)
    # The cell's internal offsets used to be fixed fractions of FRAME height while the cell's own
    # height came from the band. Shortening the band to clear the caption therefore left the
    # "who asked?" divider sitting on top of the value it was supposed to sit under. These are the
    # measured stops, and the guard below refuses rather than overprinting.
    dy_old_v = round(h * 0.048)
    dy_new_v = round(h * 0.090)

    over_font = _font(faces.label, round(h * 0.034))
    old_grid_font = _font(faces.body, round(h * 0.036))
    ask_font = _font(faces.body, round(h * 0.036))
    # Four cells share one type size, but not one string length — the longest outcome would
    # otherwise run past its cell edge and clip, which is what the first build did. Shrink to fit,
    # floored at the payload minimum so "make it fit" can never quietly cost legibility.
    val_px = round(h * 0.056)
    min_val_px = round(h * _PAYLOAD_MIN_H_FRAC)

    value_bottom_v = inset + dy_new_v + val_px
    if cell_h - inset <= value_bottom_v:
        raise SceneError(
            f"record-grid: a {cell_h / h:.3f}h cell cannot hold its value stack "
            f"({value_bottom_v / h:.3f}h) — the grid needs a taller band or shorter values"
        )

    n_frames = _frame_count(fps, duration_s)
    # All four claims flare together at the end (FLARE_START above). Sequential would read as
    # four separate observations; simultaneous is the point — it is ONE observation about all.
    ASK_START, ASK_END = ASK_WINDOW

    ground = _backdrop(w, h, palette)
    frames: list[Image.Image] = []
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = ground.copy()
        draw = ImageDraw.Draw(img)

        flare_local = _window(t, FLARE_START, 1.0)
        flare = 0.5 + 0.5 * math.sin(flare_local * 2 * math.pi * 1.5) if flare_local > 0 else 0.0

        for idx, spec in enumerate(_CALLS):
            row, col = divmod(idx, 2)
            cx = gx0 + col * (cell_w + gap)
            cy = gy0 + row * (cell_h + gap)
            share = ARRIVE_SPAN / len(_CALLS)
            # The BEAT is authored (one card per call, cut against the VO); the ENTRANCE is the
            # ported tempo. `dy` is what this card never had — it faded in place, so four cards
            # arriving read as four opacity ramps rather than as four records landing.
            rev = _entrance(0, motion=motion, duration_s=duration_s, h=h, start=idx * share)
            arrive, dy = _arrive(rev, t)
            if arrive <= 0:
                continue
            cy += dy

            _panel(
                img,
                (cx, cy, cx + cell_w, cy + cell_h),
                palette,
                alpha=arrive,
                radius_frac=RADIUS["md"],
            )
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (cx, cy, cx + cell_w, cy + cell_h),
                radius=round(h * RADIUS["md"]),
                fill=None,
                outline=_lerp_color(canvas_rgb, _lerp_color(canvas_rgb, text_rgb, 0.18), arrive),
                width=stroke_px("rule", h),
            )
            draw.text(
                (cx + inset, cy + inset),
                spec.sector,
                font=over_font,
                fill=_lerp_color(canvas_rgb, dim_rgb, arrive),
            )
            # Both values, so each cell shows the CHANGE rather than just an outcome — and so the
            # cell is filled by content instead of by a gap.
            if spec.old_value:
                oy = cy + inset + dy_old_v
                draw.text(
                    (cx + inset, oy),
                    spec.old_value,
                    font=old_grid_font,
                    fill=_lerp_color(canvas_rgb, dim_rgb, arrive),
                )
                ow = _text_w(draw, spec.old_value, old_grid_font)
                sy = oy + round(h * 0.021)
                draw.line(
                    (cx + inset, sy, cx + inset + round(ow), sy),
                    fill=_lerp_color(canvas_rgb, dim_rgb, arrive),
                    width=stroke_px("emphasis", h),
                )
            draw.text(
                (cx + inset, cy + inset + dy_new_v),
                spec.new_value,
                font=_fit_or_refuse(
                    draw,
                    face,
                    spec.new_value,
                    cell_w - 2 * inset,
                    val_px,
                    min_val_px,
                    where="record card: value",
                ),
                fill=_lerp_color(canvas_rgb, text_rgb, arrive),
            )

        ask_t = _ease_in_out(_window(t, ASK_START, ASK_END))
        if ask_t > 0:
            qf = _fit_or_refuse(
                draw,
                faces.body,
                QUESTION,
                gx1 - gx0,
                ask_font.size,
                round(h * _PAYLOAD_MIN_H_FRAC),
                where="record grid: closing question",
            )
            qw = _text_w(draw, QUESTION, qf)
            # Brightens toward ink rather than crossing to the warn colour: lerping teal to amber
            # passes through green, and green reads as "checked out fine" — the wrong note under a
            # question that is handing off to three unanswered ones.
            draw.text(
                (gx0 + (gx1 - gx0 - qw) / 2, gy1 - question_h + round(h * 0.020)),
                QUESTION,
                font=qf,
                fill=_lerp_color(
                    canvas_rgb, _lerp_color(accent_rgb, text_rgb, 0.55 * flare), ask_t
                ),
            )

        frames.append(img)

    return _write_frames(frames, out_dir, prefix="record-grid")
