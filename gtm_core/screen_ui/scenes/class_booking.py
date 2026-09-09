from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import stroke_px
from ..base import SceneError
from ..draw import _draw_cursor, _ease_in_out, _font, _lerp, _lerp_color, _text_w, _window
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area

# ── shot 2 — class-booking app: a full 6pm session with a waitlist beneath it ───────────────────
#
# Animation (matches content/<tenant>/scripts/2026-08-18-gym-incident-ciso-authz.shots.json #2):
# the cursor descends the list, the booked row flips from "Booked · Full" to "Cancelled", and the
# waitlist name beneath it slides up one place to fill the freed slot.


def render_class_booking_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Write the numbered PNG sequence for shot 2 into ``out_dir``. Returns the frame count."""
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
    good_rgb = _hex_to_rgb(palette.good, field="good")

    w, h = area.width, area.height
    # A phone-app screen inset within the frame, not a full-bleed UI — the shot list calls it "on
    # a phone screen", so the mockup should read as a device screen, not a desktop dashboard.
    margin = round(w * 0.10)
    panel_left, panel_top = margin, round(h * 0.16)
    panel_w, panel_h = w - 2 * margin, round(h * 0.70)

    row_h = round(panel_h * 0.15)
    row_gap = round(row_h * 0.22)
    rows = [
        ("9:00 AM", "Morning Flow", "6 / 12 spots"),
        ("6:00 PM", "Full Body HIIT", "12 / 12 spots"),  # the row that flips
        ("7:30 PM", "Evening Stretch", "3 / 15 spots"),
    ]
    booked_row_index = 1
    waitlist_name = "Waitlist: J. Reyes"
    # Reserved vertical space beneath the booked row for the waitlist line — the shot's own
    # premise ("a full 6pm session with a waitlist beneath it") needs a dedicated slot from
    # frame 0, and this is what keeps that line from colliding with either neighboring card as it
    # moves. Sized to clear a `waitlist_font`-height line with padding on both sides.
    waitlist_strip_h = round(row_h * 0.62)

    # Row tops, precomputed so the strip only widens the gap AFTER the booked row.
    row_tops: list[int] = []
    top = panel_top
    for idx in range(len(rows)):
        row_tops.append(top)
        top += row_h + row_gap
        if idx == booked_row_index:
            top += waitlist_strip_h

    title_font = _font(faces.heading, round(h * 0.032))
    row_title_font = _font(face, round(h * 0.024))
    row_sub_font = _font(faces.body, round(h * 0.017))
    badge_font = _font(faces.label, round(h * 0.016))
    waitlist_font = _font(faces.body, round(h * 0.016))

    n_frames = _frame_count(fps, duration_s)
    frames: list[Image.Image] = []

    # Phase windows over the shot's global progress t in [0, 1].
    CURSOR_START, CURSOR_END = 0.05, 0.55  # descend to the 6pm row
    FLIP_START, FLIP_END = 0.55, 0.80  # Booked -> Cancelled, badge color accent -> dim
    SLIDE_START, SLIDE_END = 0.72, 0.98  # waitlist name moves up within its strip, dim -> good

    booked_row_top = row_tops[booked_row_index]
    cursor_y_top = panel_top + round(row_h * 0.5)
    cursor_y_bottom = booked_row_top + round(row_h * 0.5)

    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = Image.new("RGB", (w, h), canvas_rgb)
        draw = ImageDraw.Draw(img)

        draw.text((margin, round(h * 0.09)), "Studio Booking", font=title_font, fill=text_rgb)

        flip_t = _ease_in_out(_window(t, FLIP_START, FLIP_END))
        slide_t = _ease_in_out(_window(t, SLIDE_START, SLIDE_END))

        for idx, (time_label, name, spots) in enumerate(rows):
            y = row_tops[idx]
            is_target = idx == booked_row_index
            draw.rounded_rectangle(
                (panel_left, y, panel_left + panel_w, y + row_h),
                radius=round(row_h * 0.18),
                fill=surface_rgb,
            )
            draw.text(
                (panel_left + round(panel_w * 0.04), y + round(row_h * 0.18)),
                time_label,
                font=row_sub_font,
                fill=dim_rgb,
            )
            draw.text(
                (panel_left + round(panel_w * 0.04), y + round(row_h * 0.46)),
                name,
                font=row_title_font,
                fill=text_rgb,
            )

            badge_w, badge_h = round(panel_w * 0.30), round(row_h * 0.34)
            badge_x = panel_left + panel_w - badge_w - round(panel_w * 0.04)
            badge_y = y + round((row_h - badge_h) / 2)
            if is_target:
                badge_color = _lerp_color(accent_rgb, dim_rgb, flip_t)
                badge_text = "Cancelled" if flip_t > 0.5 else "Booked · Full"
            else:
                # A quiet, always-legible outline/text pair — distinct from the target row's
                # accent so the one row that changes state is the one row that reads as "live".
                badge_color = dim_rgb
                badge_text = spots
            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                radius=round(badge_h * 0.5),
                outline=badge_color,
                width=stroke_px("hairline", h),
            )
            btw = _text_w(draw, badge_text, badge_font)
            draw.text(
                (badge_x + (badge_w - btw) / 2, badge_y + round(badge_h * 0.18)),
                badge_text,
                font=badge_font,
                fill=badge_color,
            )

            if is_target:
                # Confined entirely to the dedicated strip below this row — never the row itself
                # or the row that follows it, at any point in the animation.
                strip_top = y + row_h
                wl_y_bottom = strip_top + waitlist_strip_h - round(waitlist_strip_h * 0.30)
                wl_y_top = strip_top + round(waitlist_strip_h * 0.22)
                wl_y = round(_lerp(wl_y_bottom, wl_y_top, slide_t))
                wl_color = _lerp_color(dim_rgb, good_rgb, slide_t)
                draw.text(
                    (panel_left + round(panel_w * 0.04), wl_y),
                    waitlist_name,
                    font=waitlist_font,
                    fill=wl_color,
                )

        cursor_t = _ease_in_out(_window(t, CURSOR_START, CURSOR_END))
        cursor_y = round(_lerp(cursor_y_top, cursor_y_bottom, cursor_t))
        cursor_x = panel_left + panel_w - round(panel_w * 0.06)
        _draw_cursor(draw, cursor_x, cursor_y, accent_rgb)

        frames.append(img)

    return _write_frames(frames, out_dir, prefix="shot2-booking")
