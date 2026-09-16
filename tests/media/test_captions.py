"""gtm_core.captions — PIL-rendered caption PNGs, never ffmpeg drawtext/ass (F1). Real Pillow,
real TTF, no ffmpeg required.

The fixture font is matplotlib's bundled DejaVuSans.ttf — matplotlib is already a declared
gtm_core dependency, so this needs no new vendored asset, and (unlike a tenant's real brand font)
it carries no tenant identity, keeping this file safe for the release-mode de-brand lint.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import matplotlib
import pytest

from gtm_core import captions as cap
from gtm_core import video_lint as vl

REPO_ROOT = Path(__file__).resolve().parents[2]
_FONT_ABS = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")


def _kit(font=_FONT_ABS):
    return {"typography": {"font_files": {"caption": font}}}


# ── load_face ────────────────────────────────────────────────────────────────────────────


def test_load_face_resolves_an_absolute_configured_path():
    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    assert face.is_file()
    assert face.name == "DejaVuSans.ttf"


def test_load_face_resolves_a_repo_relative_path(tmp_path):
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    shutil.copy(_FONT_ABS, fonts_dir / "test-face.ttf")
    kit = _kit("fonts/test-face.ttf")
    face = cap.load_face(kit, repo_root=tmp_path)
    assert face.is_file()
    assert face.name == "test-face.ttf"


def test_load_face_raises_font_missing_naming_the_path_when_key_absent():
    with pytest.raises(cap.FontMissing, match="typography.font_files"):
        cap.load_face({"typography": {}}, repo_root=REPO_ROOT)


def test_load_face_raises_font_missing_naming_the_path_when_file_absent():
    with pytest.raises(cap.FontMissing, match="nonexistent-font.ttf"):
        cap.load_face(_kit("fonts/nonexistent-font.ttf"), repo_root=REPO_ROOT)


def test_load_face_never_falls_back_to_a_bitmap_font():
    """No default/fallback path exists in the module at all — absence is always an exception."""
    assert not hasattr(cap, "DEFAULT_FONT")
    assert not hasattr(cap, "FALLBACK_FONT")


# ── fit ──────────────────────────────────────────────────────────────────────────────────


def test_fit_short_text_fits_at_brand_max_size():
    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    lines, font_px = cap.fit("hello world", area=vl.SAFE_AREAS["9:16"], face=face)
    assert font_px == cap.BRAND_MAX_PX
    assert lines == ["hello world"]


def test_fit_shrinks_for_longer_text_until_it_fits():
    """Longer text steps the font down — within the two-line ceiling.

    Rewritten 2026-08-20 (PRD W5): this used to feed 60 words to a single screen and assert it
    shrank to fit. It did, and that was the defect — 60 words on one screen is unreadable at any
    size, and the V7 reading budget targets ~4 words per screen. `fit` now refuses a third line
    rather than shrinking type nobody can read.
    """
    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    long_text = "cross-organizational authorization is the unsolved part"
    lines, font_px = cap.fit(long_text, area=vl.SAFE_AREAS["1:1"], face=face)
    assert font_px < cap.BRAND_MAX_PX
    assert font_px >= cap.ERROR_FLOOR_PX
    assert 1 < len(lines) <= cap.MAX_CAPTION_LINES


def test_fit_refuses_a_third_line_instead_of_shrinking_the_type():
    """Reap's spec and our own V7 reading budget agree: a third line means reading, not watching."""
    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    with pytest.raises(cap.DoesNotFit, match="ceiling is 2"):
        cap.fit(" ".join(["word"] * 60), area=vl.SAFE_AREAS["1:1"], face=face)


def test_wrap_respects_the_character_ceiling_not_just_pixel_width():
    """A narrow face clears the safe box at 40+ chars/line and is still a wall of text.

    Width protects the frame; character count protects the reader. Reap's published band is
    28-36 characters per line.
    """
    from PIL import Image, ImageDraw, ImageFont

    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    font = ImageFont.truetype(str(face), 24)  # small enough that pixel width never binds
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text = "authorization across organizational boundaries is the unsolved part"
    lines = cap._wrap_greedy(text, font, max_width_px=10_000, draw=draw)
    assert len(lines) > 1, "the character ceiling must break the line even when width does not"
    assert all(len(line) <= cap.CHARS_PER_LINE_MAX for line in lines), lines


def test_fit_raises_does_not_fit_for_an_impossibly_long_unbreakable_token():
    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    token = "x" * 500  # one giant unbroken "word" — cannot wrap
    with pytest.raises(cap.DoesNotFit, match="500px floor\\b|floor"):
        cap.fit(token, area=vl.SAFE_AREAS["9:16"], face=face, min_px=cap.ERROR_FLOOR_PX)


@pytest.mark.parametrize(
    "text",
    [
        "אבג",  # Hebrew
        "السلام",  # Arabic
        "हिन्दी",  # Devanagari (Hindi)
    ],
)
def test_fit_raises_unshapeable_script_for_rtl_and_indic_text(text):
    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    with pytest.raises(cap.UnshapeableScript):
        cap.fit(text, area=vl.SAFE_AREAS["9:16"], face=face)


def test_fit_accepts_cjk_and_emoji_without_raising():
    face = cap.load_face(_kit(), repo_root=REPO_ROOT)
    lines, font_px = cap.fit("hello \U0001f600 world", area=vl.SAFE_AREAS["9:16"], face=face)
    assert lines


# ── split_screens ────────────────────────────────────────────────────────────────────────


def test_split_screens_groups_into_max_words_chunks():
    screens = cap.split_screens("one two three four five six seven", max_words=3)
    assert [s.text for s in screens] == ["one two three", "four five six", "seven"]


def test_split_screens_apportions_duration_evenly_when_total_s_given():
    screens = cap.split_screens("a b c d e f", max_words=2, total_s=9.0)
    assert len(screens) == 3
    assert screens[0].start_s == 0.0
    assert screens[-1].end_s == 9.0
    assert all(s.estimated for s in screens)


def test_split_screens_empty_text_returns_no_screens():
    assert cap.split_screens("   ") == []


# ── split_screens_from_word_timings (Phase E: real Reap-transcribe timing) ────────────────


def _wt(word, start, end):
    return cap.WordTiming(word=word, start_s=start, end_s=end)


def test_word_timings_group_into_max_words_chunks():
    timings = [_wt("one", 0.0, 0.2), _wt("two", 0.2, 0.4), _wt("three", 0.4, 0.7)]
    screens = cap.split_screens_from_word_timings(timings, max_words=2)
    assert [s.text for s in screens] == ["one two", "three"]


def test_word_timings_use_real_first_and_last_word_boundaries_not_apportionment():
    timings = [_wt("hello", 0.10, 0.35), _wt("world", 0.40, 0.90), _wt("again", 1.20, 1.55)]
    screens = cap.split_screens_from_word_timings(timings, max_words=2)
    assert screens[0].start_s == 0.10
    assert screens[0].end_s == 0.90  # end of "world", not an even split of a stated total
    assert screens[1].start_s == 1.20
    assert screens[1].end_s == 1.55


def test_word_timings_are_never_marked_estimated():
    timings = [_wt("real", 0.0, 0.3)]
    screens = cap.split_screens_from_word_timings(timings)
    assert all(s.estimated is False for s in screens)


def test_word_timings_empty_input_returns_no_screens():
    assert cap.split_screens_from_word_timings([]) == []


# ── render + overlay_filtergraph + write_sidecar ────────────────────────────────────────


def test_render_produces_one_png_per_screen_inside_the_safe_box(tmp_path):
    screens = cap.split_screens("short caption line here", total_s=4.0)
    rendered = cap.render(screens, ratio="9:16", kit=_kit(), out_dir=tmp_path, repo_root=REPO_ROOT)
    assert len(rendered) == len(screens)
    area = vl.SAFE_AREAS["9:16"]
    box_x, box_y, box_w, box_h = cap._safe_box_px(area)
    for r in rendered:
        assert r.png_path.is_file()
        assert box_x <= r.box["x"]
        assert r.box["x"] + r.box["w"] <= box_x + box_w
        assert box_y <= r.box["y"]
        assert r.box["y"] + r.box["h"] <= box_y + box_h


def test_render_unknown_ratio_raises():
    with pytest.raises(ValueError, match="unknown ratio"):
        cap.render([], ratio="21:9", kit=_kit(), out_dir=None, repo_root=REPO_ROOT)


def test_overlay_filtergraph_chains_one_overlay_per_screen(tmp_path):
    screens = cap.split_screens("one two three four five six", max_words=2, total_s=6.0)
    rendered = cap.render(screens, ratio="9:16", kit=_kit(), out_dir=tmp_path, repo_root=REPO_ROOT)
    graph = cap.overlay_filtergraph(rendered)
    assert graph.count("overlay=") == len(rendered)
    assert graph.startswith("[0:v][1:v]overlay=")
    assert graph.endswith("[vout]")


def test_overlay_filtergraph_empty_list_is_empty_string():
    assert cap.overlay_filtergraph([]) == ""


def test_overlay_filtergraph_positions_at_the_frame_origin_not_the_text_box(tmp_path):
    """Regression: each caption PNG is a full-frame-sized canvas with the text already drawn at
    its box position INSIDE that canvas — overlaying at box['x']/box['y'] double-applies the
    offset and pushes wide lines off the right edge of the frame. The overlay must sit at (0, 0)
    regardless of where the text landed within the PNG."""
    screens = cap.split_screens(
        "this line has enough words to sit well off the left edge", total_s=3.0
    )
    rendered = cap.render(screens, ratio="9:16", kit=_kit(), out_dir=tmp_path, repo_root=REPO_ROOT)
    assert any(r.box["x"] > 0 for r in rendered)  # a non-trivial box offset actually exists
    graph = cap.overlay_filtergraph(rendered)
    assert "overlay=x=0:y=0:" in graph
    for r in rendered:
        assert f"overlay=x={r.box['x']}:y={r.box['y']}:" not in graph


def test_write_sidecar_records_frame_safe_area_and_font_sha256(tmp_path):
    screens = cap.split_screens("caption text goes here", total_s=3.0)
    rendered = cap.render(screens, ratio="9:16", kit=_kit(), out_dir=tmp_path, repo_root=REPO_ROOT)
    out = cap.write_sidecar(rendered, ratio="9:16", out_dir=tmp_path)
    data = json.loads(out.read_text())
    assert data["frame"] == [1080, 1920]
    assert data["safe_area"] == {"left": 0.06, "right": 0.06, "top": 0.1, "bottom": 0.14}
    assert data["font_path"].endswith("DejaVuSans.ttf")
    assert len(data["font_sha256"]) == 64
    assert len(data["screens"]) == len(rendered)


def test_write_sidecar_matches_the_shape_video_lint_v3_expects(tmp_path):
    """Round-trip: captions.json's screens[].box feeds directly into video_lint's manifest arg."""
    screens = cap.split_screens("brand safe caption", total_s=2.0)
    rendered = cap.render(screens, ratio="9:16", kit=_kit(), out_dir=tmp_path, repo_root=REPO_ROOT)
    out = cap.write_sidecar(rendered, ratio="9:16", out_dir=tmp_path)
    manifest = json.loads(out.read_text())
    probe = vl.Probe(width=1080, height=1920, fps=30.0, duration_s=2.0, bit_rate=8_000_000)
    findings = vl.evaluate(probe, ratio="9:16", manifest=manifest)
    assert "V3" not in {f.tier for f in findings}


# ── keyword emphasis (Phase E: brand-accent coloring) ──────────────────────────────────────


def _kit_with_accent(accent="#FF6A00"):
    kit = _kit()
    kit["palette"] = {"accent": accent}
    return kit


def test_hex_to_rgba_parses_six_and_eight_digit_hex():
    assert cap._hex_to_rgba("#FF6A00") == (255, 106, 0, 255)
    assert cap._hex_to_rgba("FF6A00") == (255, 106, 0, 255)
    assert cap._hex_to_rgba("#FF6A0080") == (255, 106, 0, 128)


def test_hex_to_rgba_returns_none_for_malformed_input():
    assert cap._hex_to_rgba("not-a-color") is None
    assert cap._hex_to_rgba(None) is None
    assert cap._hex_to_rgba("#ZZZZZZ") is None


def test_resolve_accent_rgba_absent_palette_is_none():
    assert cap._resolve_accent_rgba(_kit()) is None


def test_resolve_accent_rgba_reads_palette_accent():
    assert cap._resolve_accent_rgba(_kit_with_accent()) == (255, 106, 0, 255)


def test_render_with_emphasis_produces_the_same_geometry_as_without(tmp_path):
    """Emphasis coloring must never change fit/wrap/box geometry — it is a paint-time-only pass
    over the SAME lines fit() already computed."""
    screens = cap.split_screens("this word right here matters most today", total_s=3.0)
    plain = cap.render(
        screens, ratio="9:16", kit=_kit_with_accent(), out_dir=tmp_path / "a", repo_root=REPO_ROOT
    )
    emphasized = cap.render(
        screens,
        ratio="9:16",
        kit=_kit_with_accent(),
        out_dir=tmp_path / "b",
        repo_root=REPO_ROOT,
        emphasis=frozenset({"matters"}),
    )
    for p, e in zip(plain, emphasized, strict=True):
        assert p.box == e.box
        assert p.font_px == e.font_px
        assert p.lines == e.lines


def test_render_with_emphasis_but_no_accent_color_still_renders(tmp_path):
    """No [palette].accent configured degrades to plain white text, never a raise."""
    screens = cap.split_screens("short caption text", total_s=2.0)
    rendered = cap.render(
        screens,
        ratio="9:16",
        kit=_kit(),  # no palette at all
        out_dir=tmp_path,
        repo_root=REPO_ROOT,
        emphasis=frozenset({"short"}),
    )
    assert all(r.png_path.is_file() for r in rendered)


def test_render_with_emphasis_actually_colors_a_pixel_differently(tmp_path):
    """The one true end-to-end check: an emphasized word's glyph pixels carry the accent RGB
    somewhere in the rendered PNG, a non-emphasized-only render does not."""
    from PIL import Image

    screens = cap.split_screens("ordinary special ordinary", total_s=3.0)
    accent = (255, 106, 0, 255)
    emphasized = cap.render(
        screens,
        ratio="9:16",
        kit=_kit_with_accent(),
        out_dir=tmp_path / "e",
        repo_root=REPO_ROOT,
        emphasis=frozenset({"special"}),
    )
    plain = cap.render(
        screens, ratio="9:16", kit=_kit_with_accent(), out_dir=tmp_path / "p", repo_root=REPO_ROOT
    )

    def _has_accent_pixel(png_path):
        img = Image.open(png_path).convert("RGBA")
        return accent[:3] in {px[:3] for px in img.getdata()}

    assert any(_has_accent_pixel(r.png_path) for r in emphasized)
    assert not any(_has_accent_pixel(r.png_path) for r in plain)


# --- with_disclosure (Phase 16: burn disclosure onto the asset's own final seconds, never a
# separately appended black-card segment) ---


def test_with_disclosure_appends_one_screen_at_the_tail():
    screens = cap.split_screens("one two three four five six seven eight", total_s=10.0)
    n_before = len(screens)
    out = cap.with_disclosure(screens, "Made with AI.", total_s=10.0)
    assert len(out) == n_before + 1
    last = out[-1]
    assert last.text == "Made with AI."
    assert last.end_s == 10.0
    assert last.start_s == 10.0 - cap.DEFAULT_DISCLOSURE_HOLD_S
    assert last.estimated is False


def test_with_disclosure_custom_hold():
    out = cap.with_disclosure([], "disclosed", total_s=20.0, hold_s=5.0)
    assert len(out) == 1
    assert out[0].start_s == 15.0
    assert out[0].end_s == 20.0


def test_with_disclosure_no_line_leaves_screens_unchanged():
    screens = cap.split_screens("some caption text here", total_s=5.0)
    out = cap.with_disclosure(screens, "", total_s=5.0)
    assert out == screens
    assert out is not screens  # a new list, never a mutated reference


def test_with_disclosure_none_line_leaves_screens_unchanged():
    screens = cap.split_screens("some caption text here", total_s=5.0)
    out = cap.with_disclosure(screens, None, total_s=5.0)
    assert out == screens


def test_with_disclosure_hold_longer_than_total_clamps_to_zero():
    """A very short asset still gets full disclosure coverage rather than a negative start_s."""
    out = cap.with_disclosure([], "disclosed", total_s=1.0, hold_s=2.5)
    assert len(out) == 1
    assert out[0].start_s == 0.0
    assert out[0].end_s == 1.0


def test_with_disclosure_rejects_non_positive_total_s():
    with pytest.raises(ValueError, match="total_s must be positive"):
        cap.with_disclosure([], "disclosed", total_s=0.0)
    with pytest.raises(ValueError, match="total_s must be positive"):
        cap.with_disclosure([], "disclosed", total_s=-3.0)


def test_with_disclosure_screen_actually_renders(tmp_path):
    """End-to-end: the appended disclosure screen is not just data — it renders to a real PNG
    like any other screen, inside the same safe area."""
    screens = cap.with_disclosure(
        cap.split_screens("first line of dialogue here", total_s=8.0),
        "Made with AI. Posted by a human.",
        total_s=8.0,
    )
    rendered = cap.render(screens, ratio="9:16", kit=_kit(), out_dir=tmp_path, repo_root=REPO_ROOT)
    assert len(rendered) == len(screens)
    assert rendered[-1].screen.text == "Made with AI. Posted by a human."
    assert rendered[-1].png_path.is_file()


def test_balanced_wrap_breaks_at_a_sentence_end_instead_of_orphaning_a_line():
    """Greedy wrapping fills the first line and gives the remainder to the last, which on a
    two-line caption reliably produces a long line over an orphan. The shape is most of what a
    viewer registers before they read a word."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(_FONT_ABS, 64)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    text = "I build all week. Nobody hears about it."
    greedy = cap._wrap_greedy(text, font, 860, draw)
    balanced = cap._wrap_balanced(text, font, 860, draw)
    assert len(balanced) == len(greedy), "the line ceiling is a legibility rule, not a preference"
    assert balanced[0].endswith("."), f"did not break at the sentence end: {balanced}"
    assert min(len(line.split()) for line in balanced) > 1, f"left an orphan: {balanced}"


def test_balanced_wrap_never_adds_a_line():
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(_FONT_ABS, 64)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for text in (
        "I'd rather say nothing than sound like a machine.",
        "It was finding the one thing worth saying.",
        "I posted. Someone read it.",
        "It never posts for me.",
    ):
        assert len(cap._wrap_balanced(text, font, 860, draw)) == len(
            cap._wrap_greedy(text, font, 860, draw)
        ), text


# ── glyph colour is derived from MEASURED luminance, never from what a palette key is named ──
#
# The shipped defect (2026-09-11): a captions-only film whose type was near-black on a dark
# picture. The tenant's company kit is a DARK-THEME kit — `canvas` is near-black, `primary` is
# near-white, and there is no `ink` key — and the resolver read `canvas` as the light glyph and
# `ink` (absent, so the near-black fallback) as the dark one, so BOTH branches of the backdrop
# decision returned near-black type. A cream kit with a dark `ink` only ever worked by luck.


def _dark_theme_kit():
    kit = _kit()
    kit["palette"] = {"canvas": "#0E0E0E", "primary": "#E8E8E8", "accent": "#5AC8FA"}
    return kit


def _cream_kit():
    kit = _kit()
    kit["palette"] = {"canvas": "#EDE8DC", "ink": "#1E1C16"}
    return kit


def _lum(rgba):
    return cap._relative_luminance(rgba[:3])


def test_a_dark_theme_kit_gets_light_glyphs_and_a_dark_stroke_on_a_dark_backdrop():
    glyph, stroke = cap._resolve_glyph_rgba(_dark_theme_kit(), 0.05)
    assert _lum(glyph) >= 0.60, glyph
    assert _lum(stroke) <= 0.20, stroke


def test_a_dark_theme_kit_gets_dark_glyphs_and_a_light_stroke_on_a_light_backdrop():
    glyph, stroke = cap._resolve_glyph_rgba(_dark_theme_kit(), 0.9)
    assert _lum(glyph) <= 0.20, glyph
    assert _lum(stroke) >= 0.60, stroke


def test_a_cream_kit_uses_cream_on_a_dark_backdrop_and_ink_on_a_light_one():
    glyph, stroke = cap._resolve_glyph_rgba(_cream_kit(), 0.05)
    assert (glyph[:3], stroke[:3]) == ((0xED, 0xE8, 0xDC), (0x1E, 0x1C, 0x16))
    glyph, stroke = cap._resolve_glyph_rgba(_cream_kit(), 0.9)
    assert (glyph[:3], stroke[:3]) == ((0x1E, 0x1C, 0x16), (0xED, 0xE8, 0xDC))


def test_explicit_caption_glyph_keys_win_over_the_derived_pair():
    kit = _dark_theme_kit()
    kit["captions"] = {"glyph_light": "#FFF8E7", "glyph_dark": "#101010"}
    glyph, stroke = cap._resolve_glyph_rgba(kit, 0.05)
    assert (glyph[:3], stroke[:3]) == ((0xFF, 0xF8, 0xE7), (0x10, 0x10, 0x10))
    glyph, stroke = cap._resolve_glyph_rgba(kit, 0.9)
    assert (glyph[:3], stroke[:3]) == ((0x10, 0x10, 0x10), (0xFF, 0xF8, 0xE7))


def test_a_palette_with_no_pole_in_either_direction_falls_back_to_white_and_near_black():
    """Mid-tones are not glyph colours: neither pole is 4.5:1 from the middle."""
    kit = _kit()
    # Relative luminance 0.26-0.35: the sRGB curve makes "#777777" read as 0.18, a real pole.
    kit["palette"] = {"canvas": "#9A9A9A", "ink": "#8C8C8C", "primary": "#A0A0A0"}
    glyph, stroke = cap._resolve_glyph_rgba(kit, 0.05)
    assert (glyph, stroke) == (cap._FALLBACK_LIGHT_GLYPH, cap._FALLBACK_DARK_GLYPH)


def test_an_unmeasured_backdrop_still_gets_a_light_glyph_over_a_dark_stroke():
    glyph, stroke = cap._resolve_glyph_rgba(_dark_theme_kit(), None)
    assert _lum(glyph) >= 0.60 and _lum(stroke) <= 0.20


def test_rendered_captions_from_a_dark_theme_kit_clear_aa_on_a_dark_picture(tmp_path):
    """Pixel-level negative control — the check that would have caught the shipped defect.

    Renders a real screen with the dark-theme kit against a measured-dark backdrop, composites
    it over a synthetic near-black frame, crops the recorded box and measures the type's WCAG
    contrast there, with the glyph colour the renderer itself recorded. Against the pre-fix
    resolver this measured ~1.0:1 (near-black on near-black); AA wants 4.5:1.
    """
    import io

    from PIL import Image

    kit = _dark_theme_kit()
    screens = cap.split_screens("the whole argument", total_s=2.0)
    rendered = cap.render(
        screens, ratio="9:16", kit=kit, out_dir=tmp_path, repo_root=REPO_ROOT, backdrop_luma=0.05
    )
    assert rendered
    for r in rendered:
        assert r.glyph_rgb is not None
        frame = Image.new("RGBA", (1080, 1920), (18, 18, 18, 255))
        frame.alpha_composite(Image.open(r.png_path).convert("RGBA"))
        box = r.box
        crop = frame.crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="PNG")
        measured = cap.contrast_against_backdrop(buf.getvalue(), glyph_rgb=r.glyph_rgb)
        assert measured is not None
        assert measured["ratio"] >= 4.5, measured


def test_the_sidecar_records_the_glyph_colour_the_type_was_drawn_in(tmp_path):
    """`measure_caption_contrast` can only judge the type it is told about. A sidecar that
    carries geometry but not colour leaves the contrast tier measuring an assumed-white glyph —
    which reads near-black-on-dark as a pass."""
    screens = cap.split_screens("caption text", total_s=2.0)
    rendered = cap.render(
        screens,
        ratio="9:16",
        kit=_dark_theme_kit(),
        out_dir=tmp_path,
        repo_root=REPO_ROOT,
        backdrop_luma=0.05,
    )
    payload = cap.sidecar_payload(rendered, ratio="9:16")
    assert payload["screens"][0]["glyph_rgb"] == list(rendered[0].glyph_rgb)
    assert cap._relative_luminance(tuple(payload["screens"][0]["glyph_rgb"])) >= 0.60
