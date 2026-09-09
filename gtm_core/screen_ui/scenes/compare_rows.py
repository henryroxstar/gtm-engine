from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ...captions import FontMissing, load_face, load_faces
from ...design_tokens import INSET, RADIUS, SPACE, px, resolve_motion, stroke_px
from ..base import SceneError
from ..draw import (
    _FOOT_BAND_TOP_FRAC,
    _arrive,
    _backdrop,
    _dashed_rect,
    _entrance,
    _font,
    _lerp_color,
    _panel,
    _text_band,
    _text_tracked,
    _text_w,
)
from ..fit import _CITATION_MIN_H_FRAC, _PAYLOAD_MIN_H_FRAC, _SECONDARY_MIN_H_FRAC, _fit_or_refuse
from ..frames import _frame_count, _write_frames
from ..palette import _hex_to_rgb, _load_palette, _resolve_area
from .compare_rows_specs import _MAX_PANEL_ROWS, _Panel, _RowsSpec


def panel_insets(h: int) -> tuple[int, int]:
    """A panel's horizontal and vertical interior padding, FROM THE TOKEN LADDER.

    These were hand-set fractions giving 13px of horizontal padding on a 1596px-wide panel —
    below `INSET`'s smallest step (16px). The module's own comment recorded the deviation and
    left it: "a card this dense is below the scale the deck surface was tuned for, which is
    worth knowing rather than forcing." The operator reported the consequence twice
    (2026-08-31, 2026-09-04): every status chip read as welded to the panel border and each
    panel's caption was drawn straight through it.

    A padding a card may decline to honour is not padding. This returns ladder steps and nothing
    else, so a card that cannot afford them now fails its own band guard instead of quietly
    shipping at 13px — and the fix for that failure is less content, which is where the headroom
    always was. `tests/media/test_screen_ui.py` pins it to the ladder.
    """
    return px(INSET["md"], h), px(INSET["sm"], h)


def render_compare_rows_frames(
    *,
    kit: dict,
    ratio: str,
    fps: int,
    duration_s: float,
    out_dir: Path,
    spec: _RowsSpec,
    font_role: str = "caption",
    repo_root: Path | None = None,
) -> int:
    """Two stacked record panels: the top states the problem, the bottom answers it."""
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

    for panel in (spec.top, spec.bottom):
        if len(panel.rows) > _MAX_PANEL_ROWS:
            raise SceneError(
                f"{spec.prefix}: panel {panel.label!r} has {len(panel.rows)} rows; the payload "
                f"type floor allows at most {_MAX_PANEL_ROWS} per panel at this ratio"
            )

    canvas_rgb = _hex_to_rgb(palette.canvas, field="canvas")
    surface_rgb = _hex_to_rgb(palette.surface, field="surface")
    text_rgb = _hex_to_rgb(palette.text, field="text")
    dim_rgb = _hex_to_rgb(palette.text_dim, field="text_dim")
    accent_rgb = _hex_to_rgb(palette.accent, field="accent")
    warn_rgb = _hex_to_rgb(palette.warn, field="warn")
    good_rgb = _hex_to_rgb(palette.good, field="good")

    w, h = area.width, area.height
    x0 = round(w * area.left) + round(w * 0.035)
    x1 = w - round(w * area.right) - round(w * 0.035)
    inner = x1 - x0

    label_font = _font(faces.label, round(h * 0.040))
    row_font = _font(face, round(h * _PAYLOAD_MIN_H_FRAC))
    chip_font = _font(faces.label, round(h * 0.030))
    note_font = _font(faces.body, round(h * _SECONDARY_MIN_H_FRAC))
    attr_font = _font(faces.body, round(h * _SECONDARY_MIN_H_FRAC))

    # Tightened 2026-08-30 so two panels plus their captions clear the caption band. `row_h` is
    # the one that cannot go much lower: the row box is `row_h - 0.010h` and must still contain
    # `row_font`, which is pinned at the payload floor.
    # Tightened 2026-08-30 so two three-row panels plus their captions clear the caption band.
    # `row_h` is the one that cannot go lower: the row box is `row_h - 0.010h` and must still
    # contain `row_font`, which is pinned at the payload floor — 0.062h leaves 2px of margin.
    pad, pad_y = panel_insets(h)
    row_h = round(h * 0.065)
    label_h = label_font.size + px(SPACE["2xs"], h)
    # The row's BOX, and the slot text is centred in. Offsets used to be three separate hand-set
    # fractions (0.012 / 0.014 / 0.019), which is why the row text, its tag and the box edge each
    # sat at a slightly different height and, once the rows tightened, overlapped their
    # neighbours. One box height, one centring rule, applied to all three.
    row_box_h = row_h - round(h * 0.006)

    def panel_h(p: _Panel) -> int:
        return pad_y * 2 + label_h + row_h * len(p.rows)

    gap_y = px(SPACE["xs"], h)
    # The citation is a FOOTER and is laid out below the caption, not above it. A burned caption
    # occupies 0.72..0.79 (measured); text can sit either side of that, and there is not room for
    # panels AND a citation above it at this ratio. Stacking it under the panels is what pushed
    # the attribution to 0.87h on the shipped cut, where the caption ran straight through it.
    foot_line = round(h * 0.042)
    foot_top = round(h * _FOOT_BAND_TOP_FRAC)
    stack = panel_h(spec.top) + gap_y + panel_h(spec.bottom)
    # Footnotes are the lowest text on this card and the attribution lower still, so the same
    # caption-band rule the record card needed applies here. See `_text_band`.
    band_t, band_b = _text_band(h, area)
    band_h = band_b - band_t
    top_y = band_t + max(0, (band_h - stack) // 2)

    # Two guards, because both of these failed silently on the shipped cut and neither is visible
    # in a probe — only in a frame.
    if stack > band_h:
        raise SceneError(
            f"{spec.prefix}: the panel stack is {stack / h:.3f}h but only {band_h / h:.3f}h is "
            f"clear above the caption band — drop a row or a caption rather than shrinking type"
        )
    foot_lines = len(spec.footnotes) + (1 if spec.attribution else 0)
    foot_bottom = foot_top + foot_line * foot_lines
    if spec.footnotes and foot_bottom > h - round(h * area.bottom):
        raise SceneError(
            f"{spec.prefix}: the citation footer needs {foot_lines} lines ending at "
            f"{foot_bottom / h:.3f}h, past the safe-area floor at "
            f"{1 - area.bottom:.3f}h — quote fewer fragments"
        )

    motion = resolve_motion(kit)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    n_frames = _frame_count(fps, duration_s)
    PANELS = ((0.04, 0.44), (0.46, 0.86))
    # Only a START now: how long the footer takes to arrive is the tempo's business,
    # not this scene's. The 0.97 end that used to live here was a per-scene duration —
    # exactly the hand-tuning `--tempo-duration` exists to replace.
    FOOT_START = 0.86

    ground = _backdrop(w, h, palette)
    frames: list[Image.Image] = []
    for i in range(n_frames):
        t = i / (n_frames - 1) if n_frames > 1 else 1.0
        img = ground.copy()
        draw = ImageDraw.Draw(img)
        py = top_y

        for pi, panel in enumerate((spec.top, spec.bottom)):
            start, end = PANELS[pi]
            ph = panel_h(panel)
            # The panel BEAT is authored against the VO; the entrance mechanic is the ported
            # tempo. `_arrive` returns the travel alongside the opacity so a caller cannot fade
            # something into place without moving it — which is what both panels used to do.
            arrive, p_dy = _arrive(
                _entrance(
                    pi,
                    motion=motion,
                    duration_s=duration_s,
                    h=h,
                    start=start,
                    room=band_b - (py + ph),
                ),
                t,
            )
            panel_y = py + p_dy
            if arrive > 0:
                # The BOTTOM panel is the one being argued for on every one of these cards — it
                # is the answer to the top panel's problem. Marking it active (edge + bloom)
                # states that in the design instead of leaving two identical rectangles for the
                # viewer to tell apart by reading them. Two same-weight stacked panels was the
                # "identical card grid" failure, and the reason these read as undesigned.
                _panel(
                    img, (x0, panel_y, x1, panel_y + ph), palette, alpha=arrive, active=(pi == 1)
                )
                draw = ImageDraw.Draw(img)
                _text_tracked(
                    draw,
                    (x0 + pad, panel_y + pad_y),
                    panel.label,
                    label_font,
                    _lerp_color(canvas_rgb, dim_rgb, arrive),
                )
                if panel.chip:
                    cw = _text_w(probe, panel.chip, chip_font)
                    cx1 = x1 - pad
                    cx0 = round(cx1 - cw - pad)
                    draw.rounded_rectangle(
                        (cx0, panel_y + pad_y - 4, cx1, panel_y + pad_y + round(h * 0.040)),
                        radius=round(h * RADIUS["xs"]),
                        fill=_lerp_color(canvas_rgb, accent_rgb, 0.85 * arrive),
                    )
                    draw.text(
                        (cx0 + pad // 2, panel_y + pad_y + 2),
                        panel.chip,
                        font=chip_font,
                        fill=canvas_rgb,
                    )

            ry = panel_y + pad_y + label_h
            span = (end - start) * 0.62 / max(1, len(panel.rows))
            for ri, row in enumerate(panel.rows):
                # A row is a sibling inside a panel that has already arrived, so it takes the
                # `soft_fade` reveal (0.35 travel, 0.85 duration) rather than the panel's full
                # `fade_up`. Its START is still the authored, VO-cut cadence — the tempo's own
                # 55ms stagger is for a list arriving as one gesture, and these rows are read
                # aloud one at a time.
                lit, r_dy = _arrive(
                    _entrance(
                        ri,
                        motion=motion,
                        duration_s=duration_s,
                        h=h,
                        start=start + (end - start) * 0.20 + ri * span,
                        cls="soft_fade",
                    ),
                    t,
                )
                if lit <= 0:
                    continue
                bx0, bx1 = x0 + pad, x1 - pad
                by0, by1 = ry + r_dy, ry + r_dy + row_box_h

                if row.state == "gap":
                    _dashed_rect(
                        draw,
                        (bx0, by0, bx1, by1),
                        _lerp_color(surface_rgb, warn_rgb, 0.85 * lit),
                        width=stroke_px("emphasis", h),
                        dash=round(h * 0.013),
                    )
                    gf = _fit_or_refuse(
                        probe,
                        face,
                        row.text,
                        bx1 - bx0 - pad * 2,
                        row_font.size,
                        30,
                        where="compare rows: gap row",
                    )
                    draw.text(
                        (bx0 + pad, by0 + (row_box_h - gf.size) // 2),
                        row.text,
                        font=gf,
                        fill=_lerp_color(surface_rgb, warn_rgb, lit),
                    )
                    ry += row_h
                    continue

                if row.state == "hot":
                    draw.rounded_rectangle(
                        (bx0, by0, bx1, by1),
                        radius=round(h * RADIUS["xs"]),
                        fill=_lerp_color(surface_rgb, accent_rgb, 0.20 * lit),
                        outline=_lerp_color(surface_rgb, accent_rgb, 0.90 * lit),
                        width=stroke_px("hairline", h),
                    )

                tag_w = 0
                if row.tag:
                    tw = _text_w(probe, row.tag, chip_font)
                    tag_w = round(tw + pad * 1.4)
                    tcol = warn_rgb if row.tag_kind == "deny" else good_rgb
                    tx1 = bx1 - round(pad * 0.6)
                    tx0 = tx1 - tag_w
                    tag_h = round(h * 0.042)
                    tag_y = by0 + (row_box_h - tag_h) // 2
                    draw.rounded_rectangle(
                        (tx0, tag_y, tx1, tag_y + tag_h),
                        radius=round(h * RADIUS["xs"]),
                        fill=(
                            _lerp_color(surface_rgb, tcol, 0.90 * lit)
                            if row.tag_kind == "deny"
                            else None
                        ),
                        outline=_lerp_color(surface_rgb, tcol, 0.90 * lit),
                        width=stroke_px("hairline", h),
                    )
                    draw.text(
                        (tx0 + round(pad * 0.7), tag_y + (tag_h - chip_font.size) // 2),
                        row.tag,
                        font=chip_font,
                        fill=(
                            canvas_rgb
                            if row.tag_kind == "deny"
                            else _lerp_color(surface_rgb, tcol, lit)
                        ),
                    )

                base = {"dim": dim_rgb, "strike": dim_rgb}.get(row.state, text_rgb)
                avail = (bx1 - bx0) - pad * 2 - tag_w
                rf = _fit_or_refuse(
                    probe,
                    face,
                    row.text,
                    avail,
                    row_font.size,
                    round(h * 0.038),
                    where="compare rows: row",
                )
                tx = bx0 + pad
                ty = by0 + (row_box_h - rf.size) // 2
                draw.text((tx, ty), row.text, font=rf, fill=_lerp_color(surface_rgb, base, lit))
                if row.state == "strike":
                    sw = _text_w(probe, row.text, rf)
                    sy = ty + round(rf.size * 0.56)
                    draw.line(
                        (tx, sy, tx + round(sw), sy),
                        fill=_lerp_color(surface_rgb, dim_rgb, lit),
                        width=stroke_px("emphasis", h),
                    )
                ry += row_h

            py += ph + gap_y

        if spec.footnotes:
            fa, f_dy = _arrive(
                _entrance(
                    0,
                    motion=motion,
                    duration_s=duration_s,
                    h=h,
                    start=FOOT_START,
                    cls="soft_fade",
                ),
                t,
            )
            if fa > 0:
                fy = foot_top + f_dy
                for note in spec.footnotes:
                    nf = _fit_or_refuse(
                        probe,
                        face,
                        note,
                        inner,
                        note_font.size,
                        round(h * _CITATION_MIN_H_FRAC),
                        where="compare rows: footnote",
                    )
                    draw.text(
                        (x0, fy), note, font=nf, fill=_lerp_color(canvas_rgb, text_rgb, 0.88 * fa)
                    )
                    fy += foot_line
                if spec.attribution:
                    # Fitted, not drawn raw: the shipped attribution was a fixed size and ran
                    # off the right edge of the frame mid-word.
                    af = _fit_or_refuse(
                        probe,
                        face,
                        spec.attribution,
                        inner,
                        attr_font.size,
                        # The attribution names the source of the quote above it — same class as
                        # the quote, same floor. Truncating a citation to fit is the same defect
                        # as editing one.
                        round(h * _CITATION_MIN_H_FRAC),
                        where="compare rows: attribution",
                    )
                    draw.text(
                        (x0, fy),
                        spec.attribution,
                        font=af,
                        fill=_lerp_color(canvas_rgb, dim_rgb, fa),
                    )

        frames.append(img)

    return _write_frames(frames, out_dir, prefix=spec.prefix)
