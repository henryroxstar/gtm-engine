from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import RADIUS, stroke_px
from ..base import SceneError
from ..draw import _ease_in_out, _font, _text_w, _window
from ..fit import _PAYLOAD_MIN_H_FRAC, _fit_trial
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area

# ── call-ui — the wayfinding chip that composites OVER a caller shot ───────────────────────────
#
# WHY THIS EXISTS, and why it is not decoration. The 2026-08-29 cut opened cold on four people
# talking straight to camera in four rooms, with nothing on screen saying who they were, who they
# had rung, or that a call was happening at all. The script had specified a corner chip on every
# caller shot; it was never built, and could not have been by the record-card scenes, because it
# has to land on top of a HeyGen render rather than on a card of its own. The result read as four
# testimonials instead of four calls — "the videos don't give context of random calls" (operator,
# 2026-08-30).
#
# Three facts have to arrive in the first second of each call, and none of them survive being left
# to the dialogue: WHICH business was rung, that a MACHINE answered, and that this is one of a set.
# The elapsed timer is what makes it read as live rather than as a lower-third caption.
#
# It sits top-left by construction. The caption band starts at CAPTION_BAND_TOP_FRAC and the face
# occupies the centre, so the top-left corner is the one region of a 16:9 caller plate that is
# reliably free of both.


#: The chip's RIGHT EDGE may not pass this fraction of frame width — a position, not a width,
#: because what matters is where the panel ends, not how wide it is. Wayfinding that lands on
#: someone's face is not wayfinding.
#:
#: 0.46 was set against the claim that "a centred head starts around 0.70". That claim was never
#: measured and is wrong by nearly 0.3. Heads were measured on all four plates on 2026-08-30 and
#: start at **0.378–0.42** — so the old cap put the chip on every caller's face, not just the
#: hotel one where it happened to land on dark hair and show. 0.35 is the binding shot (0.378,
#: the hotel plate) less a 0.03 margin.
#:
#: A cap alone was NOT enough, and this is the part worth remembering: the sub-line hit its own
#: font floor before the cap could bind, so the panel stopped shrinking at 0.402 and the cap was
#: silently inert at every value from 0.33 to 0.40. A constraint that a fitter can quietly decline
#: to meet is not a constraint. Hence the sub-line is now two SHORT lines rather than one long
#: one — same three facts, a panel narrow enough for the cap to actually bind.
_CALL_UI_MAX_RIGHT_FRAC = 0.35


@dataclass(frozen=True)
class _CallUiSpec:
    prefix: str
    #: The SECTOR, never a real brand (see the script's rails). This is the headline because it
    #: is the fact the viewer actually needs; the call number is wayfinding and rides below.
    sector: str
    index: int
    #: Seconds already elapsed when the shot starts, so four calls don't all read 00:00 — and so
    #: the sequence reads as four separate calls rather than four takes of one.
    start_s: float = 4.0
    of: int = 4


_CALL_BANK = _CallUiSpec(prefix="call-ui-bank", sector="A BANK", index=1, start_s=6)
_CALL_CLINIC = _CallUiSpec(prefix="call-ui-clinic", sector="A CLINIC", index=2, start_s=11)
_CALL_HOTEL = _CallUiSpec(prefix="call-ui-hotel", sector="A HOTEL", index=3, start_s=8)
_CALL_TELCO = _CallUiSpec(prefix="call-ui-telco", sector="A PHONE COMPANY", index=4, start_s=14)


def render_call_ui_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    spec: _CallUiSpec = _CALL_BANK,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """A live-call chip on a transparent field, for compositing over a caller shot."""
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

    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")
    panel_rgb = _hex_to_rgb(palette.canvas, field="canvas")

    w, h = area.width, area.height
    # The label is payload: it carries the only statement of which business was called, so it is
    # held at the same floor every other payload string in this module is held to.
    label_font = _font(face, round(h * _PAYLOAD_MIN_H_FRAC))
    sub_font = _font(faces.body, round(h * 0.038))

    pad_x, pad_y = round(h * 0.030), round(h * 0.024)
    dot_r = round(h * 0.011)
    gap = round(h * 0.018)

    px = round(w * area.left) + round(w * 0.025)
    py = round(h * area.top) + round(h * 0.030)

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    # Two short lines, not one long one. Carries the same three facts the single line did — that
    # an AI answered, which call of four, and how long it has been running — in a panel that can
    # actually meet the width cap. `00:00` is a placeholder for measurement; the live timer is
    # formatted per frame below.
    sub_one = "answered by AI"
    sub_two = f"call {spec.index} of {spec.of}  ·  00:00"
    # Shrink to the cap rather than growing the panel: a chip that overruns into the frame is a
    # worse failure than one whose type is a few points down, and the floor is still respected
    # for the sector line unless the cap makes that impossible.
    #
    # The cap is a HARD guarantee, so it cannot be left to `_fit_font` alone: that returns its
    # floor when even the floor overruns, which is how a 0.46 cap silently produced a 0.402-wide
    # panel at every setting between 0.33 and 0.40. Two levers are tried in order — slide the chip
    # toward the safe-area edge, then drop to the absolute type floors — and if the copy still
    # will not fit, this raises rather than drawing onto a face.
    chrome = pad_x * 2 + dot_r * 2 + gap
    cap_px = round(w * _CALL_UI_MAX_RIGHT_FRAC)
    strings = (spec.sector, sub_one, sub_two)

    def _fit_at(left: int, label_floor: int, sub_floor: int):
        body = cap_px - left - chrome
        lf, lf_ok = _fit_trial(
            probe, face, spec.sector, body, round(h * _PAYLOAD_MIN_H_FRAC), label_floor
        )
        sf, sf_ok = _fit_trial(
            probe, face, max((sub_one, sub_two), key=len), body, round(h * 0.038), sub_floor
        )
        widest = max(
            _text_w(probe, spec.sector, lf),
            _text_w(probe, sub_one, sf),
            _text_w(probe, sub_two, sf),
        )
        # Both halves must fit their own body width AND the panel must clear the right cap.
        # `lf_ok`/`sf_ok` are the part `_fit_font` used to swallow: a floor-sized line that still
        # overruns used to report success here through a width check that measured the same
        # overrunning font.
        fits = lf_ok and sf_ok and left + chrome + widest <= cap_px
        return lf, sf, widest, fits

    for left, lfloor, sfloor in (
        (px, round(h * 0.040), round(h * 0.028)),
        (round(w * area.left), round(h * 0.040), round(h * 0.028)),
        (round(w * area.left), round(h * 0.034), round(h * 0.024)),
    ):
        label_font, sub_font, body_w, ok = _fit_at(left, lfloor, sfloor)
        px = left
        if ok:
            break
    else:
        longest = max(strings, key=lambda s: _text_w(probe, s, label_font))
        raise SceneError(
            f"{spec.prefix}: {longest!r} cannot fit left of the "
            f"{_CALL_UI_MAX_RIGHT_FRAC} width cap even at the type floor — shorten the chip's "
            f"copy rather than letting it draw onto the caller's face"
        )
    panel_w = round(pad_x * 2 + dot_r * 2 + gap + body_w)
    line_h = round(h * _PAYLOAD_MIN_H_FRAC * 1.18)
    sub_line_h = round(h * 0.044)
    panel_h = round(pad_y * 2 + line_h + sub_line_h * 2)

    n_frames = _frame_count(fps, duration_s)
    frames: list[Image.Image] = []
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        secs = spec.start_s + (i / fps)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        arrive = _ease_in_out(_window(t, 0.0, 0.14))
        if arrive <= 0:
            frames.append(img)
            continue
        # Slides up a little as it fades in, so it reads as UI arriving rather than a title
        # dissolving on.
        oy = round((1 - arrive) * h * 0.020)

        draw.rounded_rectangle(
            (px, py - oy, px + panel_w, py + panel_h - oy),
            radius=round(h * RADIUS["sm"]),
            fill=(*panel_rgb, round(196 * arrive)),
            outline=(*accent_rgb, round(90 * arrive)),
            width=stroke_px("hairline", h),
        )

        # A 1.6s pulse on the dot. Nothing else in frame moves on a caller shot except the
        # speaker, so this is what says "still connected" rather than "a badge was placed here".
        pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.cos(2 * math.pi * (secs % 1.6) / 1.6))
        cx = px + pad_x + dot_r
        cy = py + pad_y + line_h // 2 - oy
        draw.ellipse(
            (cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r),
            fill=(*accent_rgb, round(255 * arrive * pulse)),
        )

        tx = px + pad_x + dot_r * 2 + gap
        draw.text(
            (tx, py + pad_y - oy),
            spec.sector,
            font=label_font,
            fill=(*text_rgb, round(255 * arrive)),
        )
        draw.text(
            (tx, py + pad_y + line_h - oy),
            sub_one,
            font=sub_font,
            fill=(*dim_rgb, round(235 * arrive)),
        )
        draw.text(
            (tx, py + pad_y + line_h + sub_line_h - oy),
            f"call {spec.index} of {spec.of}  ·  {int(secs) // 60:02d}:{int(secs) % 60:02d}",
            font=sub_font,
            fill=(*dim_rgb, round(235 * arrive)),
        )
        frames.append(img)

    return _write_frames(frames, out_dir, prefix=spec.prefix, alpha=True)
