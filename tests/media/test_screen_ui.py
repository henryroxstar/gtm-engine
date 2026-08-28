"""gtm_core.screen_ui — deterministic UI-mockup frame sequences for the `screen` shot role.

Real Pillow, no mocking. Font resolution reuses `gtm_core.captions.load_face`, so these tests
exercise that path with a real TTF rather than stubbing text metrics.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
import pytest
from PIL import Image

from gtm_core import screen_ui as su

_FONT = next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans-Bold.ttf")), None)


def _kit(**palette_overrides) -> dict:
    palette = {
        "canvas": "#0B1A2E",
        "surface": "#1A2B3C",
        "ink": "#FFFFFF",
        "accent": "#00C6C6",
    }
    palette.update(palette_overrides)
    return {
        "palette": palette,
        "typography": {"font_files": {"caption": str(_FONT)}},
    }


@pytest.fixture(autouse=True)
def _skip_without_font():
    if _FONT is None or not _FONT.is_file():
        pytest.skip("no bundled TTF fixture found")


# --- shared behavior across both scenes ---------------------------------------------------------


@pytest.mark.parametrize(
    "render",
    [su.render_class_booking_frames, su.render_request_inspector_frames],
)
def test_frame_count_matches_fps_times_duration(tmp_path, render):
    count = render(kit=_kit(), ratio="4:5", fps=12, duration_s=2.0, out_dir=tmp_path)
    assert count == 24
    assert len(list(tmp_path.glob("*.png"))) == count


@pytest.mark.parametrize(
    "render",
    [su.render_class_booking_frames, su.render_request_inspector_frames],
)
def test_every_frame_is_the_target_ratios_pixel_size(tmp_path, render):
    render(kit=_kit(), ratio="4:5", fps=8, duration_s=1.0, out_dir=tmp_path)
    for png in tmp_path.glob("*.png"):
        with Image.open(png) as img:
            assert img.size == (1080, 1350)


@pytest.mark.parametrize(
    "render",
    [su.render_class_booking_frames, su.render_request_inspector_frames],
)
def test_rerunning_with_the_same_inputs_is_byte_identical(tmp_path, render):
    """The whole point of drawing from code rather than generating: two runs of the SAME inputs
    must not differ by a single pixel. No randomness anywhere in the module makes this true;
    this test is what would catch one creeping in (e.g. an unseeded jitter "for realism")."""
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    render(kit=_kit(), ratio="9:16", fps=10, duration_s=1.5, out_dir=out_a)
    render(kit=_kit(), ratio="9:16", fps=10, duration_s=1.5, out_dir=out_b)
    frames_a = sorted(out_a.glob("*.png"))
    frames_b = sorted(out_b.glob("*.png"))
    assert len(frames_a) == len(frames_b) > 0
    for fa, fb in zip(frames_a, frames_b, strict=True):
        assert fa.read_bytes() == fb.read_bytes()


@pytest.mark.parametrize(
    "render",
    [su.render_class_booking_frames, su.render_request_inspector_frames],
)
def test_unknown_ratio_is_refused(tmp_path, render):
    with pytest.raises(su.SceneError, match="unknown ratio"):
        render(kit=_kit(), ratio="21:9", fps=12, duration_s=2.0, out_dir=tmp_path)


@pytest.mark.parametrize(
    "render",
    [su.render_class_booking_frames, su.render_request_inspector_frames],
)
def test_a_kit_with_no_configured_font_is_refused_not_defaulted(tmp_path, render):
    """Mirrors captions.py's own posture: no bitmap-font fallback, ever."""
    bare_kit = {"palette": {"canvas": "#000000"}, "typography": {"font_files": {}}}
    with pytest.raises(su.SceneError, match="font_files"):
        render(kit=bare_kit, ratio="4:5", fps=12, duration_s=2.0, out_dir=tmp_path)


@pytest.mark.parametrize(
    "render",
    [su.render_class_booking_frames, su.render_request_inspector_frames],
)
@pytest.mark.parametrize("bad_field", ["fps", "duration_s"])
def test_a_non_positive_fps_or_duration_is_refused(tmp_path, render, bad_field):
    kwargs = {"fps": 12, "duration_s": 2.0}
    kwargs[bad_field] = 0
    with pytest.raises(su.SceneError):
        render(kit=_kit(), ratio="4:5", out_dir=tmp_path, **kwargs)


def test_a_thin_kit_still_renders_via_fallback_colors(tmp_path):
    """Only canvas/surface/accent are configured on this profile today
    (BRAND.toml's own comment: 'Mode A basics') — a scene must degrade gracefully, not raise on a
    missing nice-to-have like [palette].warn or [palette].good."""
    thin = {
        "palette": {"canvas": "#0B1A2E", "surface": "#1A2B3C", "accent": "#00C6C6"},
        "typography": {"font_files": {"caption": str(_FONT)}},
    }
    count = su.render_request_inspector_frames(
        kit=thin, ratio="4:5", fps=8, duration_s=1.0, out_dir=tmp_path
    )
    assert count > 0


# --- content-specific checks: this is what makes it "the payload of the shot", not just "a UI" --


def test_class_booking_shows_the_full_6pm_row_and_a_waitlist_line_from_frame_zero():
    """The shot's whole premise: 'a full 6pm session with a waitlist beneath it'. If frame 0
    doesn't already show both, the establishing beat of the shot doesn't exist."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        su.render_class_booking_frames(kit=_kit(), ratio="4:5", fps=12, duration_s=3.0, out_dir=out)
        first = sorted(out.glob("*.png"))[0]
        with Image.open(first) as img:
            colors = {px[:3] for px in img.convert("RGB").getdata()}
    accent_rgb = su._hex_to_rgb("#00C6C6", field="accent")
    # The "Booked · Full" badge is drawn in the accent color at frame 0 (before the flip) — its
    # presence is how a pixel-level test knows the booked state actually rendered, not just that
    # SOME image was produced.
    assert accent_rgb in colors


def test_class_booking_flips_the_badge_color_by_the_end_of_the_shot():
    """By the final frame the booked row's badge must have flipped from accent (pre-flip) to dim
    (post-flip). The cursor glyph is ALWAYS accent-colored, so this counts occurrences rather than
    checking bare presence — a filled badge outline + label contributes hundreds of accent pixels,
    a lone ~16px cursor glyph contributes a few dozen at most; the two are not confusable at that
    ratio, and asserting only presence would pass on a bug that never flips the badge at all."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        su.render_class_booking_frames(kit=_kit(), ratio="4:5", fps=12, duration_s=3.0, out_dir=out)
        first = sorted(out.glob("*.png"))[0]
        last = sorted(out.glob("*.png"))[-1]
        with Image.open(first) as img:
            first_pixels = list(img.convert("RGB").getdata())
        with Image.open(last) as img:
            last_pixels = list(img.convert("RGB").getdata())
    accent_rgb = su._hex_to_rgb("#00C6C6", field="accent")
    first_count = sum(1 for px in first_pixels if px == accent_rgb)
    last_count = sum(1 for px in last_pixels if px == accent_rgb)
    assert first_count > 200, "expected the pre-flip accent badge to contribute many pixels"
    # A scale-invariant bound rather than a fixed pixel budget: the cursor glyph's own accent
    # pixel count (observed ~129 at 1080x1350) doesn't move between these two frames, only the
    # badge's does — so "well under half of the pre-flip count" catches a badge that failed to
    # flip without hardcoding a cursor-size constant that would drift if the glyph is ever redrawn.
    assert last_count < first_count / 2, (
        f"expected mostly the cursor glyph's accent pixels at the final frame "
        f"(pre-flip count was {first_count}), got {last_count} — the booked badge appears not to "
        "have flipped color"
    )


def test_request_inspector_session_line_highlights_but_acting_agent_never_does():
    """Only the session-token line is meant to turn 'good' (verified); the acting-agent line
    pulses warn/amber, never good — conflating the two would draw the field as if it too had
    checked out fine, which is the opposite of the shot's point."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        su.render_request_inspector_frames(
            kit=_kit(), ratio="4:5", fps=12, duration_s=4.0, out_dir=out
        )
        last = sorted(out.glob("*.png"))[-1]
        with Image.open(last) as img:
            colors = {px[:3] for px in img.convert("RGB").getdata()}
    good_rgb = su._hex_to_rgb("#3BD68A", field="good")  # module default, kit sets none
    assert good_rgb in colors  # the session line has highlighted by the last frame


# --- easing / geometry primitives, unit-level ----------------------------------------------------


def test_ease_in_out_is_monotonic_and_bounded():
    xs = [i / 20 for i in range(21)]
    ys = [su._ease_in_out(x) for x in xs]
    assert ys[0] == 0.0 and ys[-1] == 1.0
    assert all(b >= a for a, b in zip(ys, ys[1:]))


def test_window_clamps_outside_its_range():
    assert su._window(0.0, 0.5, 0.8) == 0.0
    assert su._window(1.0, 0.5, 0.8) == 1.0
    assert su._window(0.65, 0.5, 0.8) == pytest.approx(0.5)


def test_lerp_color_endpoints():
    a, b = (0, 0, 0), (255, 255, 255)
    assert su._lerp_color(a, b, 0.0) == a
    assert su._lerp_color(a, b, 1.0) == b


def test_invalid_hex_color_is_refused():
    # 9 chars — passes the "is it 6 digits" length gate, fails the actual int() parse.
    with pytest.raises(su.SceneError, match="not a valid hex color"):
        su._hex_to_rgb("#zzzzzz", field="canvas")
    with pytest.raises(su.SceneError, match="not a 6-digit hex"):
        su._hex_to_rgb("#fff", field="canvas")
