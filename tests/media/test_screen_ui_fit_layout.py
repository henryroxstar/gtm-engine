"""``gtm_core.screen_ui.fit_layout`` — the caption-band footprint, the hero/phone content-box
measurement, and the size solver (`glittery-sleeping-yao` changes #4/#5).

Real Pillow, real fixture fonts (matplotlib's bundled DejaVuSans — a declared dependency, no
tenant identity), fictional synthesized stills — same convention ``test_screen_ui.py`` uses.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pytest
from PIL import Image, ImageDraw

from gtm_core import captions
from gtm_core.screen_ui import fit_layout as fl

_FONT = next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans.ttf")), None)
pytestmark = pytest.mark.skipif(_FONT is None, reason="no bundled TTF fixture found")


def _kit(**captions_section) -> dict:
    kit = {
        "palette": {
            "canvas": "#0B1A2E",
            "surface": "#1A2B3C",
            "ink": "#FFFFFF",
            "accent": "#00C6C6",
        },
        "typography": {"font_files": {"caption": str(_FONT)}},
    }
    if captions_section:
        kit["captions"] = captions_section
    return kit


def _still(
    path: Path, *, size: tuple[int, int] = (1080, 1920), bg=(11, 26, 46), content=(200, 200, 200),
    top_frac: float = 0.085, bottom_frac: float = 0.832,
) -> Path:  # fmt: skip
    """A synthesized still: a flat background with one rectangular "content" block occupying a
    known vertical span — a stand-in for a phone-app screenshot's real content."""
    w, h = size
    im = Image.new("RGB", (w, h), bg)
    top, bottom = round(h * top_frac), round(h * bottom_frac)
    ImageDraw.Draw(im).rectangle((round(w * 0.1), top, round(w * 0.9), bottom), fill=content)
    im.save(path)
    return path


def _screen(path: Path, *, size: tuple[int, int] = (240, 522)) -> Path:
    Image.new("RGB", size, (246, 246, 244)).save(path)
    return path


# ── caption_band: measured, not formula-asserted ──────────────────────────────────────────────


@pytest.mark.parametrize("ratio", ["9:16", "4:5", "1:1", "16:9"])
@pytest.mark.parametrize(
    "n_lines,text",
    [
        (1, "one short line"),
        (2, "a caption long enough that it wraps onto two full lines of real text"),
    ],
)
def test_caption_band_contains_every_alpha_pixel_of_a_real_rendered_caption_lower(
    tmp_path, ratio, n_lines, text
):
    """The guard against the band formula silently drifting from render()'s real output: measure
    the ACTUAL alpha bbox of a rendered caption PNG at >=1% alpha, not the formula in isolation."""
    kit = _kit()
    screens = [captions.Screen(text=text, start_s=0.0, end_s=1.0)]
    rendered = captions.render(screens, ratio=ratio, kit=kit, out_dir=tmp_path, placement="lower")
    img = Image.open(rendered[0].png_path)
    bbox = img.split()[-1].point(lambda p: 255 if p >= round(255 * 0.01) else 0).getbbox()
    assert bbox is not None
    top, bottom = fl.caption_band(kit, ratio=ratio, lines=len(rendered[0].lines))
    assert top <= bbox[1] and bbox[3] <= bottom, (
        f"{ratio} lower {n_lines}L: measured ({bbox[1]},{bbox[3]}) not inside band ({top:.1f},{bottom:.1f})"
    )


@pytest.mark.parametrize("ratio", ["4:5", "1:1"])  # the only two where upper has any room
def test_caption_band_contains_every_alpha_pixel_of_a_real_rendered_caption_upper(tmp_path, ratio):
    kit = _kit(placement="upper")
    text = "a caption long enough that it wraps onto two full lines of real text"
    screens = [captions.Screen(text=text, start_s=0.0, end_s=1.0)]
    rendered = captions.render(screens, ratio=ratio, kit=kit, out_dir=tmp_path, placement="upper")
    img = Image.open(rendered[0].png_path)
    bbox = img.split()[-1].point(lambda p: 255 if p >= round(255 * 0.01) else 0).getbbox()
    assert bbox is not None
    top, bottom = fl.caption_band(kit, ratio=ratio, lines=len(rendered[0].lines))
    assert top <= bbox[1] and bbox[3] <= bottom


def test_two_line_band_is_taller_than_one_line_band():
    kit = _kit()
    t1, b1 = fl.caption_band(kit, ratio="9:16", lines=1)
    t2, b2 = fl.caption_band(kit, ratio="9:16", lines=2)
    assert (b2 - t2) > (b1 - t1)


def test_upper_placement_inverts_the_constraint_band_sits_near_the_top():
    kit = _kit(placement="upper")
    top, bottom = fl.caption_band(kit, ratio="4:5", lines=1)
    assert top < 400 and bottom < 700  # near the top of a 1350-tall frame, not the bottom


def test_the_naive_blur_formula_would_undersize_the_real_footprint(tmp_path):
    """Documents WHY the 3x correction exists: the naive round(px*0.18) (no 3x) formula, measured
    against the same real render this module checks against, misses real alpha pixels."""
    kit = _kit()
    screens = [captions.Screen(text="one short line", start_s=0.0, end_s=1.0)]
    rendered = captions.render(screens, ratio="9:16", kit=kit, out_dir=tmp_path, placement="lower")
    img = Image.open(rendered[0].png_path)
    bbox = img.split()[-1].point(lambda p: 255 if p >= round(255 * 0.01) else 0).getbbox()

    from PIL import ImageFont

    from gtm_core.captions import LINE_SPACING, SAFE_AREAS, _block_top, _safe_box_px

    area = SAFE_AREAS["9:16"]
    _bx, box_y, _bw, box_h = _safe_box_px(area)
    font_px = rendered[0].font_px
    font = ImageFont.truetype(str(_FONT), font_px)
    ascent, descent = font.getmetrics()
    line_height = (ascent + descent) * LINE_SPACING
    block_top = _block_top(box_y, box_h, line_height, "lower")
    pad_y = round(font_px * 0.34)
    naive_bottom = block_top + line_height + pad_y + round(font_px * 0.18)
    assert bbox[3] > naive_bottom, "the naive (1x) formula should undersize the real footprint"


def test_caption_band_refuses_an_unknown_ratio():
    with pytest.raises(fl.FitLayoutError, match="unknown ratio"):
        fl.caption_band(_kit(), ratio="21:9", lines=1)


# ── hero-reveal content box ────────────────────────────────────────────────────────────────────


def test_hero_content_box_flat_background_free_still_is_the_whole_image(tmp_path):
    src = tmp_path / "flat.png"
    Image.new("RGB", (400, 600), (20, 20, 20)).save(src)
    box_src, size, layout = fl._hero_box_src(src, None, 1.0)
    assert box_src == (0, 0, 400, 600)
    assert size == (400, 600)
    assert layout is None


def test_hero_content_box_rgba_still_is_flattened_first(tmp_path):
    src = tmp_path / "rgba.png"
    Image.new("RGBA", (300, 300), (0, 0, 0, 0)).save(src)
    box_src, size, _layout = fl._hero_box_src(src, None, 1.0)
    assert size == (300, 300)  # loaded without raising


def test_hero_content_box_non_square_still_uses_non_uniform_scale(tmp_path):
    """The aspect-distorted resize is where a uniform-scale bbox mapping would be silently
    wrong — a wide, short still exercises x_scale != y_scale directly."""
    src = tmp_path / "wide.png"
    _still(src, size=(1600, 400), top_frac=0.1, bottom_frac=0.9)
    box_src, src_size, _layout = fl._hero_box_src(src, None, 1.0)
    frame = fl._hero_box_frame(box_src, src_size, (1080, 1920), scale=1.0, cx_frac=0.5, cy_frac=0.5)
    # At scale=1.0 the layer is (1080, 1920) regardless of the source's own (1600, 400) shape —
    # a uniform mapping would preserve the source's near-4:1 aspect; this must not.
    assert (frame[2] - frame[0]) / (frame[3] - frame[1]) != pytest.approx(1600 / 400, rel=0.05)


def test_dim_rect_and_ring_extend_the_box_past_the_stills_own_content_bbox(tmp_path):
    src = tmp_path / "s.png"
    _still(src, size=(1000, 1000), top_frac=0.4, bottom_frac=0.6)  # narrow content band
    box_no_actions, _size, _layout = fl._hero_box_src(src, None, 1.0)

    actions = {
        "version": 1,
        "actions": [
            {
                "type": "highlight",
                "start_s": 0.0,
                "end_s": 1.0,
                "rect": [10, 10, 90, 90],  # well outside the content band, near the top-left
                "dim_rect": [0, 0, 120, 120],
            }
        ],
    }
    box_with, _size2, _layout2 = fl._hero_box_src(src, actions, 1.0)
    assert box_with[1] < box_no_actions[1], "dim_rect/ring did not extend the box upward"


# ── phone-walkthrough box ──────────────────────────────────────────────────────────────────────


def test_phone_box_at_rest_is_inside_the_frame(tmp_path):
    screen = _screen(tmp_path / "screen.png")
    from gtm_core.screen_ui.scenes.phone_chassis import PHONE_H_FRAC

    box = fl._phone_box_frame(
        Image.open(screen).size,
        None,
        1.0,
        (1080, 1920),
        phone_h_frac=PHONE_H_FRAC,
        center_y_frac=0.5,
    )
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= 1080
    assert 0 <= y0 < y1 <= 1920


def test_zoom_action_pushes_the_phone_box_further_than_without_it(tmp_path):
    """Zoom is CHECKED, not exempted: the box with a large zoom must be strictly taller than the
    at-rest chassis+shadow box alone."""
    screen = _screen(tmp_path / "screen.png")
    size = Image.open(screen).size
    rest = fl._phone_box_frame(size, None, 1.0, (1080, 1920), phone_h_frac=0.73, center_y_frac=0.5)
    actions = {
        "version": 1,
        "actions": [
            {
                "type": "zoom",
                "start_s": 0.0,
                "end_s": 1.0,
                "rect": [16, 40, 224, 130],
                "scale": 2.8,
                "ease_s": 0.3,
            }
        ],
    }
    zoomed = fl._phone_box_frame(
        size, actions, 1.0, (1080, 1920), phone_h_frac=0.73, center_y_frac=0.5
    )
    rest_h = rest[3] - rest[1]
    zoomed_h = zoomed[3] - zoomed[1]
    assert zoomed_h > rest_h, "a 2.8x zoom did not extend the measured box at all"


# ── report / solve ─────────────────────────────────────────────────────────────────────────────


def test_report_refuses_an_unresolvable_font(tmp_path):
    src = tmp_path / "s.png"
    _still(src)
    bare_kit = {"palette": {"canvas": "#000000"}, "typography": {"font_files": {}}}
    with pytest.raises(fl.FitLayoutError, match="font_files"):
        fl.report(for_scene="hero-reveal", kit=bare_kit, ratio="9:16", image=src, caption_lines=1)


def test_report_refuses_a_bad_for_scene(tmp_path):
    src = tmp_path / "s.png"
    _still(src)
    with pytest.raises(fl.FitLayoutError, match="hero-reveal or phone-walkthrough"):
        fl.report(for_scene="bogus", kit=_kit(), ratio="9:16", image=src, caption_lines=1)


def test_report_refuses_a_missing_image():
    with pytest.raises(fl.FitLayoutError, match="--image is required"):
        fl.report(for_scene="hero-reveal", kit=_kit(), ratio="9:16", image=None, caption_lines=1)


def test_report_hero_default_full_bleed_content_overlapping_the_band_fails(tmp_path):
    """Regression shape: content spans nearly the whole still (8.5%-83.2% of height) at the
    scene's default scale=1.0/cy=0.5 — it must clip the caption band, not silently pass."""
    src = tmp_path / "s.png"
    _still(src)
    doc = fl.report(for_scene="hero-reveal", kit=_kit(), ratio="9:16", image=src, caption_lines=1)
    assert doc["ok"] is False
    assert doc["current"]["caption_clearance_px"] < 0
    assert doc["recommended"] is not None and doc["recommended"]["ok"] is True


def test_report_hero_recommended_layout_actually_passes_a_second_check(tmp_path):
    """Property: the recommended layout, re-measured, must itself report ok — a solver that
    recommends a layout it would then refuse is worse than no recommendation."""
    src = tmp_path / "s.png"
    _still(src)
    doc = fl.report(for_scene="hero-reveal", kit=_kit(), ratio="9:16", image=src, caption_lines=1)
    rec = doc["recommended"]["layout"]
    reverify = fl.report(
        for_scene="hero-reveal", kit=_kit(), ratio="9:16", image=src, caption_lines=1,
        actions={"version": 1, "actions": [{"type": "enter", "start_s": 0.0, "end_s": 0.1, "from": "bottom"}], "layout": rec},
    )  # fmt: skip
    assert reverify["current"]["ok"] is True


def test_report_hero_recommended_scale_is_maximal_one_step_up_fails(tmp_path):
    """Property (bounded search, not hypothesis): scale + 0.01 must fail — the solver reports the
    LARGEST fitting scale, not merely a fitting one."""
    src = tmp_path / "s.png"
    _still(src)
    doc = fl.report(for_scene="hero-reveal", kit=_kit(), ratio="9:16", image=src, caption_lines=1)
    solved = doc["recommended"]["layout"]["scale"]
    box_src, src_size, _layout = fl._hero_box_src(src, None, 1.0)
    area = {"w": 1080.0, "h": 1920.0}
    band_top, band_bottom = fl.caption_band(_kit(), ratio="9:16", lines=1)
    free_top, free_bottom = fl._free_band(
        "lower", h=area["h"], band_top=band_top, band_bottom=band_bottom, margin=40
    )
    over = round(solved + 0.01, 2)
    base = fl._hero_box_frame(
        box_src, src_size, (area["w"], area["h"]), scale=over, cx_frac=0.5, cy_frac=0.5
    )
    cy = fl._centred_cy_frac(base, frame_h=area["h"], free_top=free_top, free_bottom=free_bottom)
    box = fl._hero_box_frame(
        box_src, src_size, (area["w"], area["h"]), scale=over, cx_frac=0.5, cy_frac=cy
    )
    assert not fl._fits(box, w=area["w"], margin=40, free_top=free_top, free_bottom=free_bottom)


def test_report_phone_default_layout_can_fail_and_recommends_one_that_passes(tmp_path):
    screen = _screen(tmp_path / "screen.png")
    doc = fl.report(
        for_scene="phone-walkthrough", kit=_kit(), ratio="9:16", image=screen, caption_lines=2
    )
    if not doc["current"]["ok"]:
        assert doc["recommended"] is not None
        assert doc["recommended"]["ok"] is True
        assert doc["recommended"]["caption_clearance_px"] > 0


def test_report_phone_zoom_action_that_overflows_is_detected_not_exempted(tmp_path):
    screen = _screen(tmp_path / "screen.png")
    actions = {
        "version": 1,
        "actions": [
            {"type": "enter", "start_s": 0.0, "end_s": 0.4, "from": "bottom"},
            {
                "type": "zoom", "start_s": 0.6, "end_s": 2.0,
                "rect": [16, 40, 224, 130], "scale": 2.8, "ease_s": 0.3,
            },
        ],
        "layout": {"phone_h_frac": 0.78, "center_y_frac": 0.50},
    }  # fmt: skip
    doc = fl.report(
        for_scene="phone-walkthrough", kit=_kit(), ratio="9:16", image=screen,
        actions=actions, caption_lines=2,
    )  # fmt: skip
    assert doc["ok"] is False
    assert doc["current"]["caption_clearance_px"] < 0


# ── run_cli / exit codes ────────────────────────────────────────────────────────────────────────


def test_run_cli_missing_for_scene_is_exit_2():
    import argparse

    args = argparse.Namespace(for_scene=None)
    doc, code = fl.run_cli(args, _kit())
    assert code == 2 and "for-scene" in doc["error"]


def test_run_cli_exit_0_when_it_fits_exit_1_when_it_clips(tmp_path):
    import argparse

    src = tmp_path / "narrow.png"
    _still(src, top_frac=0.30, bottom_frac=0.55)  # a small content band, should fit easily
    args = argparse.Namespace(
        for_scene="hero-reveal", ratio="9:16", image=src, actions=None,
        caption_lines=1, margin_px=40, repo_root=None,
    )  # fmt: skip
    doc, code = fl.run_cli(args, _kit())
    assert code == 0
    assert doc["ok"] is True

    full = tmp_path / "full.png"
    _still(full)  # the near-full-bleed regression shape
    args2 = argparse.Namespace(
        for_scene="hero-reveal", ratio="9:16", image=full, actions=None,
        caption_lines=1, margin_px=40, repo_root=None,
    )  # fmt: skip
    doc2, code2 = fl.run_cli(args2, _kit())
    assert code2 == 1
    assert doc2["ok"] is False
