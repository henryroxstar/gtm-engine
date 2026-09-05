from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import stroke_px
from ..base import SceneError
from ..draw import _draw_cursor, _ease_in_out, _font, _lerp, _lerp_color, _window
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area

# ── shot 5 — request inspector: a valid session token, a blank "acting agent" field ─────────────
#
# Animation: the session-token line highlights green (it checked out), the cursor drops to the
# empty "acting agent" field beneath it, and that field pulses amber — the shot's whole argument
# is that the field is blank and nothing stopped the request anyway.


def render_request_inspector_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Write the numbered PNG sequence for shot 5 into ``out_dir``. Returns the frame count."""
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
    surface_rgb = _hex_to_rgb(palette.surface, field="surface")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")
    warn_rgb = _hex_to_rgb(palette.warn, field="warn")
    good_rgb = _hex_to_rgb(palette.good, field="good")

    w, h = area.width, area.height
    margin = round(w * 0.10)
    panel_left, panel_top = margin, round(h * 0.18)
    panel_w = w - 2 * margin
    field_h = round(h * 0.11)
    field_gap = round(field_h * 0.30)

    title_font = _font(faces.heading, round(h * 0.030))
    label_font = _font(faces.label, round(h * 0.016))
    value_font = _font(face, round(h * 0.021))
    placeholder_font = _font(faces.body, round(h * 0.021))

    fields = [
        ("SESSION TOKEN", "sess_live_8f2c…d91a  ·  valid"),
        ("USER", "member_4471  ·  verified"),
        ("ACTING AGENT", ""),  # the blank field the shot is about
    ]
    acting_index = 2

    n_frames = _frame_count(fps, duration_s)
    frames: list[Image.Image] = []

    HILITE_START, HILITE_END = 0.05, 0.35  # session line highlights green
    DROP_START, DROP_END = 0.30, 0.55  # cursor drops to the acting-agent field
    PULSE_START, PULSE_END = 0.55, 1.0  # blank field pulses amber, repeating

    field_top = {idx: panel_top + idx * (field_h + field_gap) for idx in range(len(fields))}
    cursor_y_top = panel_top + round(field_h * 0.5)
    cursor_y_bottom = field_top[acting_index] + round(field_h * 0.5)

    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = Image.new("RGB", (w, h), canvas_rgb)
        draw = ImageDraw.Draw(img)

        draw.text((margin, round(h * 0.09)), "Request Inspector", font=title_font, fill=text_rgb)

        hilite_t = _ease_in_out(_window(t, HILITE_START, HILITE_END))
        pulse_local = _window(t, PULSE_START, PULSE_END)
        # A slow repeating pulse rather than a one-shot fade — the field STAYS unattended for the
        # rest of the shot, so its emphasis should read as ongoing, not as a transition that ends.
        pulse_t = 0.5 + 0.5 * math.sin(pulse_local * 2 * math.pi * 1.5) if pulse_local > 0 else 0.0

        for idx, (label, value) in enumerate(fields):
            y = field_top[idx]
            is_session = idx == 0
            is_acting = idx == acting_index

            border = surface_rgb
            if is_session:
                border = _lerp_color(surface_rgb, good_rgb, hilite_t)
            elif is_acting and pulse_local > 0:
                border = _lerp_color(surface_rgb, warn_rgb, 0.35 + 0.65 * pulse_t)

            draw.rounded_rectangle(
                (panel_left, y, panel_left + panel_w, y + field_h),
                radius=round(field_h * 0.16),
                fill=surface_rgb,
                outline=border,
                width=stroke_px("emphasis", h),
            )
            draw.text(
                (panel_left + round(panel_w * 0.04), y + round(field_h * 0.16)),
                label,
                font=label_font,
                fill=dim_rgb,
            )
            if value:
                fill = _lerp_color(text_rgb, good_rgb, hilite_t) if is_session else text_rgb
                draw.text(
                    (panel_left + round(panel_w * 0.04), y + round(field_h * 0.46)),
                    value,
                    font=value_font,
                    fill=fill,
                )
            else:
                placeholder_color = (
                    _lerp_color(dim_rgb, warn_rgb, pulse_t) if pulse_local > 0 else dim_rgb
                )
                draw.text(
                    (panel_left + round(panel_w * 0.04), y + round(field_h * 0.46)),
                    "— not set —",
                    font=placeholder_font,
                    fill=placeholder_color,
                )

        cursor_t = _ease_in_out(_window(t, DROP_START, DROP_END))
        cursor_y = round(_lerp(cursor_y_top, cursor_y_bottom, cursor_t))
        cursor_x = panel_left + panel_w - round(panel_w * 0.06)
        _draw_cursor(draw, cursor_x, cursor_y, accent_rgb)

        frames.append(img)

    return _write_frames(frames, out_dir, prefix="shot5-inspector")
