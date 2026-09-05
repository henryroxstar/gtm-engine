from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import INSET, RADIUS, STACK, px, resolve_motion, stroke_px
from ..base import SceneError
from ..draw import _arrive, _backdrop, _entrance, _font, _lerp_color, _panel, _text_band
from ..fit import _fit_or_refuse
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area

# ── message-card — one inbound message, arriving ───────────────────────────────────────────────
#
# The narrative counterpart to `title-card`: not the film's own statement but a message someone
# in the film has just received. A generation model asked for this produces letterform-shaped
# noise (`shots_lint._lint_no_in_frame_text` refuses the prompt for exactly that reason), so the
# card is drawn here and burned over the plate at finish time.
#
# THE COPY IS DATA, NOT ENGINE CONTENT. `title-card` learned this the expensive way — a hardcoded
# CTA URL meant every tenant's close card carried one company's domain until 2026-09-02. A
# message is film content by nature (a different film has a different sender saying a different
# thing), so the three strings arrive through `message=` and the defaults below exist only so the
# scene renders standalone. They are deliberately unremarkable.
#
# WHAT THE DEFAULTS MAY NEVER BECOME. This card dramatizes a real-looking inbound message, which
# makes it the one scene in this module where a careless string is a fabricated record of a real
# person contacting someone. Sender and role stay invented and generic; no real company, no real
# handle, no email address, no phone number. A caller who overrides them owns the same duty, and
# the shot list is where that gets recorded (`production.overlay.identity_rule`).

#: Generic, fictional, and numberless on purpose — a digit in this card reads as a real quantity
#: someone can be held to, and the card is a dramatization.
_MESSAGE_DEFAULTS: dict[str, str] = {
    "sender": "A. Mercer",
    "role": "Reader",
    "body": "Read what you wrote. Free for fifteen minutes this week?",
}

#: The card refuses rather than truncates past this many wrapped body lines. A message long enough
#: to need a fifth is not a message card any more, and silently dropping the tail is the failure
#: `_fit_or_refuse` exists to prevent, one axis over. Four rather than three because the ceiling
#: has to hold across FACES, not just across copy: the same sentence that sets in three lines on
#: the tenant's condensed caption face wraps to four on a wider one, and a cap tuned to one face
#: refuses real copy the moment a kit points somewhere else.
_MAX_BODY_LINES = 4


def _wrap(text: str, font, max_w: float, draw: ImageDraw.ImageDraw) -> list[str]:
    """Greedy wrap under a pixel width alone.

    Deliberately NOT ``captions._wrap_greedy``: that one also caps characters per line to Reap's
    published subtitle spec, which is a rule about a caption read while listening to the line it
    transcribes. A message card is read on its own, and a 28-character ceiling would break it
    into fragments for a reason that does not apply here.
    """
    lines: list[str] = []
    current: list[str] = []
    for word in text.split():
        candidate = " ".join([*current, word])
        if current and draw.textlength(candidate, font=font) > max_w:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


@dataclass(frozen=True)
class _Fitted:
    """Everything measured before frame zero: the fonts, the wrapped body, and the geometry."""

    name_font: object
    role_font: object
    body_font: object
    lines: list[str]
    header_left: int
    avatar_r: int


def _fit_card(
    face: Path,
    probe: ImageDraw.ImageDraw,
    copy: dict[str, str],
    *,
    h: int,
    card_w: int,
    pad: int,
) -> _Fitted:
    """Fit the three strings and wrap the body.

    Split out of the render function so the frame loop reads as drawing. Everything here happens
    exactly once, and every refusal it raises is about copy that will not fit — never about a frame.
    """
    inner_w = card_w - 2 * pad
    name_px = round(h * 0.036)
    role_px = round(h * 0.026)
    body_px = round(h * 0.042)
    body_floor_px = round(h * 0.030)
    avatar_r = round(h * 0.026)

    header_left = pad + 2 * avatar_r + px(INSET["md"], h)
    header_w = card_w - header_left - pad
    name_font = _fit_or_refuse(
        probe,
        face,
        copy["sender"],
        header_w,
        name_px,
        round(h * 0.026),
        where="message card: sender",
    )
    role_font = _fit_or_refuse(
        probe, face, copy["role"], header_w, role_px, round(h * 0.020), where="message card: role"
    )

    # The body is WRAPPED and then fitted, in that order: shrinking a one-line font until a long
    # sentence fits would set the message in type smaller than the payload floor while the card
    # still had width to give it. Wrapping spends the width first; the fit is what refuses when
    # even three lines cannot hold it.
    body_font = _font(face, body_px)
    lines = _wrap(copy["body"], body_font, inner_w, probe)
    while len(lines) > _MAX_BODY_LINES and body_font.size > body_floor_px:
        body_px = max(body_floor_px, round(body_px * 0.92))
        body_font = _font(face, body_px)
        lines = _wrap(copy["body"], body_font, inner_w, probe)
    if len(lines) > _MAX_BODY_LINES:
        raise SceneError(
            f"message card: the body wraps to {len(lines)} lines at its "
            f"{body_font.size}px floor, past the {_MAX_BODY_LINES}-line card — shorten the message"
        )
    for line in lines:
        _fit_or_refuse(
            probe, face, line, inner_w, body_font.size, body_floor_px, where="message card: body"
        )

    return _Fitted(name_font, role_font, body_font, lines, header_left, avatar_r)


def _resolve_message(message: dict[str, str] | None) -> dict[str, str]:
    """The three strings, with unset keys keeping the generic defaults.

    Same posture as `checkpoint-flow`'s `--label`: an unknown key is a caller error and says so,
    rather than being dropped into a dict nobody reads.
    """
    resolved = dict(_MESSAGE_DEFAULTS)
    for key, value in (message or {}).items():
        if key not in _MESSAGE_DEFAULTS:
            raise SceneError(
                f"message card: unknown key {key!r} — expected one of {sorted(_MESSAGE_DEFAULTS)}"
            )
        text = str(value).strip()
        if not text:
            raise SceneError(f"message card: {key} was set to an empty string")
        resolved[key] = text
    return resolved


def render_message_card_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    message: dict[str, str] | None = None,
    repo_root: Path | None = None,
) -> int:
    """Write the numbered PNG sequence for one arriving message card. Returns the frame count.

    The card lands first, then the body settles under it — the reading order a real notification
    has, and the reason the two use different reveal classes rather than one shared fade.
    """
    area = _resolve_area(ratio)
    palette = _load_palette(kit)
    motion = resolve_motion(kit)
    copy = _resolve_message(message)
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
    band_top, band_bottom = _text_band(h, area)

    margin = round(w * area.left) + round(w * 0.04)
    card_w = w - 2 * margin
    pad = px(INSET["lg"], h)

    ground = _backdrop(w, h, palette)
    probe = ImageDraw.Draw(ground)
    fitted = _fit_card(face, probe, copy, h=h, card_w=card_w, pad=pad)
    name_font = fitted.name_font
    role_font = fitted.role_font
    body_font = fitted.body_font
    lines = fitted.lines
    header_left = fitted.header_left
    avatar_r = fitted.avatar_r
    name_px = round(h * 0.036)
    role_px = round(h * 0.026)

    line_h = round(body_font.size * 1.34)
    header_h = max(2 * avatar_r, name_px + px(STACK["xs"], h) + role_px)
    rule_gap = px(STACK["md"], h)
    card_h = pad + header_h + rule_gap + px(STACK["md"], h) + line_h * len(lines) + pad
    if card_h > band_bottom - band_top:
        raise SceneError(
            f"message card: the card is {card_h}px tall inside a "
            f"{band_bottom - band_top}px text band — shorten the message"
        )
    card_top = band_top + (band_bottom - band_top - card_h) // 2
    box = (margin, card_top, margin + card_w, card_top + card_h)

    #: A message ARRIVES; it does not ease in. The first cut of this scene started the body at 0.22
    #: of shot progress, which on a 3.5s hook put the proposition — the only reason to keep watching
    #: — on screen at about 1.2s. On a cold scroll that is 35% of the beat spent on a stranger's name
    #: before the sentence that matters exists. The card lands first (a notification does), the body
    #: follows immediately behind it, and the reveal order is kept only so the eye has somewhere to
    #: start.
    BODY_START = 0.05
    card_reveal = _entrance(
        0, motion=motion, duration_s=duration_s, h=h, cls="scale_in", room=band_bottom - box[3]
    )
    body_reveals = [
        _entrance(i, motion=motion, duration_s=duration_s, h=h, start=BODY_START, cls="soft_fade")
        for i in range(len(lines))
    ]

    #: The card is drawn onto an opaque ground so `_panel` can blend against the tenant canvas the
    #: way every other scene does, then MASKED to its own rounded rectangle and emitted RGBA. That
    #: is the whole reason this scene differs from `title-card`: a title card IS the frame, and a
    #: message card lands on top of a shot that has to stay visible around it. A flattened RGB
    #: sequence composites through `video_finish.overlay_frames` as an opaque rectangle — the
    #: silent way to black out the picture underneath, which `_write_frames` warns about.
    radius = round(h * RADIUS["sm"])
    scrim_rgb = (0, 0, 0)

    frames: list[Image.Image] = []
    n_frames = _frame_count(fps, duration_s)
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = ground.copy()
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        card_a, card_dy = _arrive(card_reveal, t)
        if card_a > 0:
            shifted = (box[0], box[1] + card_dy, box[2], box[3] + card_dy)
            # `active=False` deliberately: `_panel`'s focal bloom spills OUTSIDE the box, and the
            # mask below cuts it at the card edge — a clipped glow reads as a hard rectangle of
            # light, which is worse than no glow. A card over footage gets its separation from
            # the scrim instead.
            _panel(img, shifted, palette, alpha=card_a, active=False)
            draw = ImageDraw.Draw(img)

            cx = shifted[0] + pad + avatar_r
            cy = shifted[1] + pad + header_h // 2
            draw.ellipse(
                (cx - avatar_r, cy - avatar_r, cx + avatar_r, cy + avatar_r),
                fill=_lerp_color(canvas_rgb, accent_rgb, card_a * 0.55),
            )

            x = shifted[0] + header_left
            draw.text(
                (x, shifted[1] + pad),
                copy["sender"],
                font=name_font,
                fill=_lerp_color(canvas_rgb, text_rgb, card_a),
            )
            draw.text(
                (x, shifted[1] + pad + name_px + px(STACK["xs"], h)),
                copy["role"],
                font=role_font,
                fill=_lerp_color(canvas_rgb, dim_rgb, card_a),
            )

            rule_y = shifted[1] + pad + header_h + rule_gap
            draw.line(
                (shifted[0] + pad, rule_y, shifted[2] - pad, rule_y),
                fill=_lerp_color(canvas_rgb, dim_rgb, card_a * 0.5),
                width=stroke_px("hairline", h),
            )

            body_top = rule_y + px(STACK["md"], h)
            for n, line in enumerate(lines):
                body_a, body_dy = _arrive(body_reveals[n], t)
                if body_a <= 0:
                    continue
                draw.text(
                    (shifted[0] + pad, body_top + n * line_h + body_dy),
                    line,
                    font=body_font,
                    fill=_lerp_color(canvas_rgb, text_rgb, min(1.0, body_a * card_a)),
                )

            # A scrim under the whole frame, not just behind the card: the footage this lands on
            # is a lit room, and a cream card floating on an unmodified plate loses its edge
            # wherever the plate is bright. Scaled by the card's own arrival so the picture is
            # untouched before the message exists.
            scrim = Image.new("RGBA", (w, h), (*scrim_rgb, round(255 * 0.45 * card_a)))
            out.alpha_composite(scrim)

            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                shifted, radius=radius, fill=round(255 * min(1.0, card_a))
            )
            out.paste(img.convert("RGBA"), (0, 0), mask)

        frames.append(out)

    return _write_frames(frames, out_dir, prefix="message-card", alpha=True)


__all__ = ["_MAX_BODY_LINES", "_MESSAGE_DEFAULTS", "render_message_card_frames"]
