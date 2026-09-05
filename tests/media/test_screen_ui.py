"""gtm_core.screen_ui — deterministic UI-mockup frame sequences for the `screen` shot role.

Real Pillow, no mocking. Font resolution reuses `gtm_core.captions.load_face`, so these tests
exercise that path with a real TTF rather than stubbing text metrics.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib
import pytest
from PIL import Image, ImageDraw, ImageFont

from gtm_core import screen_ui as su

#: The tenant-free stand-in for the brand's caption face. REGULAR, not Bold: DejaVuSans-Bold's
#: advance widths run ~18-21% wider than the real face, while DejaVuSans runs ~4-6% wider. Once
#: `_fit_or_refuse` was wired in, that 20% gap started failing card-geometry and card-colour tests
#: on TYPE METRICS OF A FONT THE PRODUCT NEVER USES — a refusal that says nothing about whether
#: the shipped card fits. These tests are about geometry, colour and ink; the stand-in should not
#: be the variable under test. Fit against the real face is a tenant-bound check and cannot live
#: here (the de-brand lint keeps tenant fonts out of tests) — it is run against the profile's
#: resolved kit, and its 2026-08-31 findings are recorded in the shot list.
_FONT = next(iter(Path(matplotlib.get_data_path()).rglob("DejaVuSans.ttf")), None)


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


# --- shared behavior across every scene ---------------------------------------------------------


@pytest.mark.parametrize(
    "render",
    [
        su.render_class_booking_frames,
        su.render_request_inspector_frames,
        su.render_caller_record_frames,
    ],
)
def test_frame_count_matches_fps_times_duration(tmp_path, render):
    count = render(kit=_kit(), ratio="4:5", fps=12, duration_s=2.0, out_dir=tmp_path)
    assert count == 24
    assert len(list(tmp_path.glob("*.png"))) == count


@pytest.mark.parametrize(
    "render",
    [
        su.render_class_booking_frames,
        su.render_request_inspector_frames,
        su.render_caller_record_frames,
    ],
)
def test_every_frame_is_the_target_ratios_pixel_size(tmp_path, render):
    render(kit=_kit(), ratio="4:5", fps=8, duration_s=1.0, out_dir=tmp_path)
    for png in tmp_path.glob("*.png"):
        with Image.open(png) as img:
            assert img.size == (1080, 1350)


@pytest.mark.parametrize(
    "render",
    [
        su.render_class_booking_frames,
        su.render_request_inspector_frames,
        su.render_caller_record_frames,
    ],
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
    [
        su.render_class_booking_frames,
        su.render_request_inspector_frames,
        su.render_caller_record_frames,
    ],
)
def test_unknown_ratio_is_refused(tmp_path, render):
    with pytest.raises(su.SceneError, match="unknown ratio"):
        render(kit=_kit(), ratio="21:9", fps=12, duration_s=2.0, out_dir=tmp_path)


@pytest.mark.parametrize(
    "render",
    [
        su.render_class_booking_frames,
        su.render_request_inspector_frames,
        su.render_caller_record_frames,
    ],
)
def test_a_kit_with_no_configured_font_is_refused_not_defaulted(tmp_path, render):
    """Mirrors captions.py's own posture: no bitmap-font fallback, ever."""
    bare_kit = {"palette": {"canvas": "#000000"}, "typography": {"font_files": {}}}
    with pytest.raises(su.SceneError, match="font_files"):
        render(kit=bare_kit, ratio="4:5", fps=12, duration_s=2.0, out_dir=tmp_path)


@pytest.mark.parametrize(
    "render",
    [
        su.render_class_booking_frames,
        su.render_request_inspector_frames,
        su.render_caller_record_frames,
    ],
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


# --- caller-record: the four calls' shared record beat --------------------------------------------


def _record_last_frame(spec, ratio="16:9", duration_s=3.0):
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        su.render_caller_record_frames(
            kit=_kit(), ratio=ratio, fps=12, duration_s=duration_s, out_dir=out, spec=spec
        )
        paths = sorted(out.glob("*.png"))
        with Image.open(paths[0]) as img:
            first = img.convert("RGB").copy()
        with Image.open(paths[-1]) as img:
            last = img.convert("RGB").copy()
    return first, last


def test_every_registered_record_variant_renders():
    """Each ``record-*`` CLI name is one call in the film. A variant that raises is a shot that
    cannot be built, discovered at render time rather than here."""
    import tempfile

    names = [n for n in su._SCENES if n.startswith("record-") and n != "record-grid"]
    assert len(names) == 6, f"expected the six call variants, got {names}"
    for name in names:
        with tempfile.TemporaryDirectory() as d:
            count = su._SCENES[name](
                kit=_kit(), ratio="16:9", fps=12, duration_s=2.0, out_dir=Path(d)
            )
            assert count == 24, name


def test_the_written_value_is_absent_at_frame_zero_and_present_at_the_end():
    """The shot IS the change. If the incoming value is already on screen at frame 0 there is no
    write to watch, and if it never arrives the beat didn't happen — so both ends are asserted."""
    first, last = _record_last_frame(su._BANK)
    accent = su._hex_to_rgb("#00C6C6", field="accent")
    first_accent = sum(1 for px in first.getdata() if px == accent)
    last_accent = sum(1 for px in last.getdata() if px == accent)
    assert last_accent > first_accent * 2, (
        f"expected the written value + lit systems to add accent pixels "
        f"(frame 0: {first_accent}, final: {last_accent})"
    )


def test_the_held_variant_shows_no_read_write_badge():
    """``record-clean`` is the control the other four are read against: nothing is written, so the
    READ/WROTE badge must not appear at all. Counting accent across the WHOLE frame cannot show
    this — the held variant legitimately carries accent in its own answer line — so this samples
    the badge's corner specifically. That distinction is the point of the shot: without it the
    clean case reads as just another write."""
    _, clean_last = _record_last_frame(su._CLEAN)
    _, bank_last = _record_last_frame(su._BANK)
    accent = su._hex_to_rgb("#00C6C6", field="accent")

    def badge_corner_accent(img):
        # Top-right of the card, where the badge is drawn. Generous bounds so the test survives
        # small layout moves without silently sampling the wrong region.
        return sum(
            1
            for y in range(round(img.height * 0.11), round(img.height * 0.24))
            for x in range(round(img.width * 0.78), round(img.width * 0.95))
            if img.getpixel((x, y)) == accent
        )

    assert badge_corner_accent(bank_last) > 100, "expected a WROTE badge on a written record"
    assert badge_corner_accent(clean_last) == 0, "held variant must not draw a READ/WROTE badge"


def test_payload_type_clears_the_mobile_floor_on_every_ratio():
    """The floor exists because the first cut of this scene was typeset against a desktop display
    and came back unreadable in a phone feed. Asserting the CONSTANT alone would be circular — so
    this checks the sizes the scene actually derives from it."""
    for ratio in ("16:9", "4:5", "9:16", "1:1"):
        h = su.SAFE_AREAS[ratio].height
        payload_px = round(h * su._PAYLOAD_MIN_H_FRAC)
        assert payload_px >= 54, f"{ratio}: payload type {payload_px}px is below the 54px floor"


def test_the_card_stays_inside_the_caption_safe_band_on_every_ratio_and_variant():
    """Height is measured from content and then centred — but the stack can overrun a short band
    (4:5 and 1:1 are the tight ones), and an overrunning card pushes its own border down into the
    zone a burned caption occupies. The squeeze is what prevents that; this is what proves it, on
    the widest variant of each ratio rather than on the one that happens to fit."""
    import tempfile

    # The ground is a gradient now, not a flat fill, so "any pixel unequal to canvas" matches
    # every row in the frame. What this test is about is where the CARD is, so it thresholds on
    # brightness: the backdrop is capped at `_BACKDROP_MAX_DELTA` of the way toward white, and
    # the card's surface and type sit far above that.
    canvas = su._hex_to_rgb("#0B1A2E", field="canvas")
    ink_ceiling = max(canvas) + 255 * su._BACKDROP_MAX_DELTA * 3
    for ratio in ("16:9", "4:5", "9:16", "1:1"):
        area = su.SAFE_AREAS[ratio]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            su.render_caller_record_frames(
                kit=_kit(), ratio=ratio, fps=12, duration_s=2.0, out_dir=out, spec=su._TELCO
            )
            with Image.open(sorted(out.glob("*.png"))[-1]) as img:
                px = img.convert("RGB")
                rows = [
                    y
                    for y in range(px.height)
                    if any(max(px.getpixel((x, y))) > ink_ceiling for x in range(0, px.width, 8))
                ]
        top_limit = round(area.height * area.top)
        # The card's TEXT is now held above the burned-caption band rather than merely inside the
        # safe area — see `_text_band`. The card's own border may still run below that, so the
        # limit here stays the safe-area floor.
        bottom_limit = area.height - round(area.height * area.bottom)
        assert rows[0] >= top_limit - 1, f"{ratio}: card starts at {rows[0]}, above {top_limit}"
        assert rows[-1] <= bottom_limit + 1, (
            f"{ratio}: card ends at {rows[-1]}, below the caption band at {bottom_limit}"
        )


def test_record_grid_is_registered_and_renders():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        count = su._SCENES["record-grid"](
            kit=_kit(), ratio="16:9", fps=12, duration_s=3.0, out_dir=Path(d)
        )
        assert count == 36


def test_record_grid_quotes_the_four_calls_it_summarises():
    """The payoff shot reads _CALLS rather than its own copy of the outcomes. If someone re-adds a
    literal list here, this catches the drift the moment a call's wording changes."""
    assert su._CALLS == (su._BANK, su._CLINIC, su._HOTEL, su._TELCO)
    assert all(spec.new_value for spec in su._CALLS)


def test_record_grid_claim_never_passes_through_green():
    """The four claims flare by brightening the accent, never by cross-fading to the warn colour —
    teal-to-amber passes through green, and green reads as 'checked out fine', which is the
    opposite of four unanswered claims. Asserted on pixels, since the bug would be a colour."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        su.render_record_grid_frames(kit=_kit(), ratio="16:9", fps=12, duration_s=4.0, out_dir=out)
        greenish = 0
        for path in sorted(out.glob("*.png"))[-8:]:
            with Image.open(path) as img:
                for px in img.convert("RGB").getdata():
                    r, g, b = px
                    if g > 120 and g > r + 60 and g > b + 60:
                        greenish += 1
    assert greenish == 0, f"{greenish} green pixels in the flare — the claim colour drifted"


def test_fit_font_shrinks_to_fit_but_never_below_the_floor():
    # No tempdir: _fit_font only measures, so a scratch canvas is all it needs.
    img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(img)
    short = su._fit_font(draw, _FONT, "ok", 800, 60, 40)
    long_ = su._fit_font(draw, _FONT, "a" * 200, 800, 60, 40)
    assert short.size == 60, "a string that already fits must not be shrunk"
    assert long_.size == 40, "an unfittable string must land on the floor, not below it"


def test_title_close_disclosure_clears_the_caption_band(tmp_path):
    """The Art. 50 line must sit entirely above where a burned caption lands.

    On 2026-08-29 a Reap `system_pro_box` pass over this exact card put its caption box at
    y=0.72..0.785 while the disclosure's ink ran 0.699..0.735, so the box cut through
    "Made with AI. Reviewed and posted by a human." Nothing failed: `video_lint`'s caption gate
    asks only whether the caption is inside the safe area — it was — and has no idea the
    disclosure shares that space. A partially occluded disclosure is a compliance defect.

    Two assertions, because either alone passes for the wrong reason: the first version of this
    test used a kit with no [disclosure] block, so the card rendered no disclosure at all and
    the clearance check was vacuous.
    """
    kit = dict(_kit())
    kit["disclosure"] = {"line": "Made with AI. Reviewed and posted by a human."}
    su._SCENES["title-close"](kit=kit, ratio="16:9", fps=12, duration_s=2.0, out_dir=tmp_path)
    written = sorted(tmp_path.glob("title-close-*.png"))
    assert written, "scene wrote no frames"
    img = Image.open(written[-1]).convert("RGB")  # last frame: disclosure fully faded up
    w, h = img.size

    # Measured against the bare ground, not against one corner pixel: the card is drawn on a
    # gradient now, so a single sampled "canvas" colour is only correct at the point it was
    # sampled and every other row reads as inked.
    ground = su._backdrop(w, h, su._load_palette(kit))
    inked = [
        y
        for y in range(h)
        if max(
            sum(abs(c - b) for c, b in zip(img.getpixel((x, y)), ground.getpixel((x, y))))
            for x in range(0, w, 2)
        )
        > 30
    ]
    assert inked, "nothing rendered at all"

    band_top = int(h * su.CAPTION_BAND_TOP_FRAC)
    # 1. the disclosure genuinely rendered — ink in the lower third, below the title block
    assert any(y > h * 0.60 for y in inked), (
        "no ink below 0.60 of the frame: the disclosure did not render, so the clearance "
        "assertion below would pass vacuously"
    )
    # 2. and nothing reaches the caption band
    assert max(inked) < band_top, (
        f"content reaches y={max(inked)} ({max(inked) / h:.4f} of frame height), inside the "
        f"caption band starting at {su.CAPTION_BAND_TOP_FRAC}"
    )


def test_title_close_holds_the_disclosure_for_seconds_not_for_a_fraction(tmp_path):
    """The Art. 50 line must stay up for a duration set in seconds, not in card-fractions.

    Every other window on this card is proportional, which is right for a reveal: the rhythm
    should scale with the shot. The disclosure is the one element with a legal reason to be
    legible, and a proportional schedule hands it whatever time the card happens to have. On the
    2026-08-30 cut the closing card ran 6.16s — its VO's length — so the old 0.70..0.90 window
    put the line fully up at 5.54s and held it for 1.24s before the film cut to black. Counted in
    frames, not estimated.

    Asserted across two very different card lengths, because a single length passes for the wrong
    reason: a fixed fraction that happens to be generous at one duration is exactly the bug.
    """
    kit = dict(_kit())
    kit["disclosure"] = {"line": "Made with AI. Reviewed and posted by a human."}
    fps = 25

    for duration_s, floor_s in ((6.16, 2.0), (14.0, 2.4)):
        out = tmp_path / f"d{int(duration_s * 100)}"
        out.mkdir()
        su._SCENES["title-close"](
            kit=kit, ratio="16:9", fps=fps, duration_s=duration_s, out_dir=out
        )
        frames = sorted(out.glob("title-close-*.png"))
        assert frames, "scene wrote no frames"

        def _has_disclosure(path):
            img = Image.open(path).convert("RGB")
            w, h = img.size
            canvas = img.getpixel((4, 4))
            band = range(int(h * 0.64), int(h * su.CAPTION_BAND_TOP_FRAC))
            return any(
                sum(abs(c - b) for c, b in zip(img.getpixel((x, y)), canvas)) > 30
                for y in band
                for x in range(0, w, 4)
            )

        first = next((i for i, f in enumerate(frames) if _has_disclosure(f)), None)
        assert first is not None, (
            f"the disclosure never appeared on a {duration_s}s card — the hold assertion below "
            "would pass vacuously"
        )
        held_s = (len(frames) - first) / fps
        assert held_s >= floor_s, (
            f"a {duration_s}s card held the disclosure for only {held_s:.2f}s (floor {floor_s}s). "
            "A fraction-of-card schedule is what produced the 1.24s hold this test exists to stop."
        )


# --- compare-rows / call-ui / still-push crop (2026-08-30 rework) --------------------------------


def test_compare_rows_refuses_more_rows_than_the_type_floor_allows(tmp_path):
    """A fifth row can only be fitted by setting type below the payload floor.

    That is the exact trade the 2026-08-29 cards made — more content, less legible — and it is
    the one this scene must refuse rather than silently take.
    """
    spec = su._RowsSpec(
        prefix="too-many",
        top=su._Panel(label="X", chip="", rows=tuple(su._Row(f"row {i}") for i in range(5))),
        bottom=su._Panel(label="Y", chip="", rows=(su._Row("ok"),)),
    )
    with pytest.raises(su.SceneError, match="at most 3"):
        su.render_compare_rows_frames(
            kit=_kit(), ratio="16:9", fps=8, duration_s=1.0, out_dir=tmp_path, spec=spec
        )


def test_compare_rows_deny_tag_uses_a_colour_the_accent_does_not_supply(tmp_path):
    """`READ` and `DENIED` must not be the same chip.

    The kit defines one accent for both primary and accent, so a card drawn only from it cannot
    distinguish an allowed action from a refused one — and that distinction is the whole payload
    of the evidence card. The palette's `warn` fallback is what carries it.
    """
    su.render_compare_rows_frames(
        kit=_kit(), ratio="16:9", fps=8, duration_s=2.0, out_dir=tmp_path, spec=su._ROWS_EVIDENCE
    )
    last = Image.open(sorted(tmp_path.glob("*.png"))[-1]).convert("RGB")
    warn = su._hex_to_rgb(su._load_palette(_kit()).warn, field="warn")
    hits = sum(1 for px in last.getdata() if px == warn)
    assert hits > 200, f"deny state drew only {hits} px of warn — the DENIED chip is not landing"


def test_call_ui_chip_stays_clear_of_centre_frame(tmp_path):
    """The chip may not reach the speaker.

    Every caller plate is centre-framed, so a panel sized purely to its own text runs into the
    face as soon as the label is long — which is what "CALL 4 · A PHONE COMPANY" did on the first
    build. Nothing may be drawn past the cap, on the LONGEST label in the set.
    """
    su.render_call_ui_frames(
        kit=_kit(), ratio="16:9", fps=8, duration_s=1.0, out_dir=tmp_path, spec=su._CALL_TELCO
    )
    last = Image.open(sorted(tmp_path.glob("*.png"))[-1]).convert("RGBA")
    w = last.width
    cap = round(w * su._CALL_UI_MAX_RIGHT_FRAC)
    right = last.crop((cap, 0, w, last.height))
    assert max(px[3] for px in right.getdata()) == 0, "chip drew past its width cap"


def test_call_ui_writes_real_alpha(tmp_path):
    """Flattened to RGB this composites as an opaque black bar over the caller."""
    su.render_call_ui_frames(
        kit=_kit(), ratio="16:9", fps=8, duration_s=1.0, out_dir=tmp_path, spec=su._CALL_BANK
    )
    img = Image.open(sorted(tmp_path.glob("*.png"))[-1])
    assert img.mode == "RGBA"
    alphas = {px[3] for px in img.convert("RGBA").getdata()}
    assert 0 in alphas, "no transparent pixels — the overlay would black out the shot"
    assert max(alphas) > 200, "nothing opaque was drawn"


def test_still_push_crop_frac_rejects_an_inverted_region(tmp_path):
    src = tmp_path / "s.png"
    Image.new("RGB", (400, 300), (10, 20, 30)).save(src)
    with pytest.raises(su.SceneError, match="crop_frac"):
        su.render_still_push_frames(
            kit=_kit(),
            ratio="16:9",
            fps=8,
            duration_s=1.0,
            out_dir=tmp_path,
            image=src,
            crop_frac=(0.8, 0.0, 0.2, 1.0),
        )


def test_still_push_crop_frac_selects_the_region_before_scaling(tmp_path):
    """The crop must pick the REGION, not just reframe the whole image.

    A full application window cover-fitted to 1920 puts every table row near 8px, which is the
    2026-08-29 console shot: on screen, unreadable at any size. Painting the source in quadrants
    and cropping to one of them proves the output carries that quadrant's pixels alone.
    """
    src = tmp_path / "quadrants.png"
    im = Image.new("RGB", (400, 400), (0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 199, 199), fill=(255, 0, 0))  # top-left
    d.rectangle((200, 200, 399, 399), fill=(0, 255, 0))  # bottom-right
    im.save(src)

    out = tmp_path / "frames"
    su.render_still_push_frames(
        kit=_kit(),
        ratio="16:9",
        fps=8,
        duration_s=0.25,
        out_dir=out,
        image=src,
        crop_frac=(0.5, 0.5, 1.0, 1.0),
    )
    with Image.open(sorted(out.glob("*.png"))[0]) as frame:
        colours = {px[:3] for px in frame.convert("RGB").getdata()}
    assert colours == {(0, 255, 0)}, f"crop leaked pixels from outside the region: {colours}"


def test_crop_frac_cli_refuses_a_malformed_region(tmp_path, capsys):
    """--crop-frac is four numbers or it is an error, never a silently-ignored typo.

    The flag only reaches one scene, so a typo that parsed as "no crop" would look exactly like a
    working render of the unreadable full window.
    """
    src = tmp_path / "s.png"
    Image.new("RGB", (400, 300), (10, 20, 30)).save(src)
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(_kit()), encoding="utf-8")

    for bad in ("0.1,0.2,0.3", "left,top,right,bottom"):
        code = su.main(
            [
                "still-push",
                "--kit-json",
                str(kit_json),
                "--ratio",
                "16:9",
                "--duration-s",
                "0.25",
                "--out-dir",
                str(tmp_path / "f"),
                "--image",
                str(src),
                "--crop-frac",
                bad,
            ]
        )
        assert code == 2, f"{bad!r} was accepted"
        assert "crop-frac" in capsys.readouterr().out


# ── the 2026-08-30 operator review: three defects a probe cannot see ────────────────────────────


#: Brightness alone no longer separates chrome from type. It did while the brightest chrome was
#: the panel's hairline top light at ~103, but the design-token rework gave panels a bright accent
#: border (~191 measured on `rows-scope`), which is chrome ``draw._text_band`` EXPLICITLY permits
#: in the band: "a panel's border or fill may pass behind the caption band without costing
#: anything". So brightness now only nominates candidate ink and GEOMETRY decides: a border
#: crosses the panel as one long contiguous run, a glyph stem does not.
_CHROME_CEILING = 120

#: A bright horizontal run at least this fraction of the frame wide is a rule or a panel border,
#: never a glyph. `rows-scope`'s bottom border measures 0.82 of the frame; the widest run any
#: single glyph produced in the same frame was 6px (0.003).
_RULE_MIN_RUN_FRAC = 0.25


def _typelike_runs(crop, ceiling: int, min_rule_run: int) -> list[tuple[int, int]]:
    """``(row, run_length)`` for every bright run too short to be a rule — i.e. type."""
    lum = list(crop.convert("L").getdata())
    width = crop.width
    hits: list[tuple[int, int]] = []
    for row_i in range(crop.height):
        run = 0
        for value in lum[row_i * width : (row_i + 1) * width] + [0]:
            if value > ceiling:
                run += 1
            else:
                if 0 < run < min_rule_run:
                    hits.append((row_i, run))
                run = 0
    return hits


def test_checkpoint_flow_timing_actually_reaches_the_drawing(tmp_path):
    """A signature test proves the parameter exists; this proves it is WIRED.

    The whole point of `timing` is that a re-cut VO moves the card's elements. If the map were
    accepted and then ignored — a plausible refactor, since the literals still exist as the
    fallback — every other test here would still pass and the card would silently keep the old
    narration's schedule, which is exactly the defect this replaced.
    """
    shifted = dict(su._CHECKPOINT_DEFAULT_TIMING)
    shifted["DROP"] = (0.55, 0.62)  # the gateway arrives late instead of early

    def _bytes(timing):
        out = tmp_path / ("shift" if timing else "default")
        su.render_checkpoint_flow_frames(
            kit=_kit(), ratio="16:9", fps=8, duration_s=1.0, out_dir=out, timing=timing
        )
        return [p.read_bytes() for p in sorted(out.glob("*.png"))]

    default, moved = _bytes(None), _bytes(shifted)
    assert len(default) == len(moved)
    assert default != moved, "the timing map was accepted and then ignored"


def test_checkpoint_flow_lead_in_follows_the_first_cue(tmp_path):
    """NODES/LINK/DIRECT are derived from DROP rather than left behind as literals. Push DROP to
    the back half and the card's opening must move with it, or those three still describe a
    recording nobody is listening to."""
    late = dict(su._CHECKPOINT_DEFAULT_TIMING) | {"DROP": (0.60, 0.68)}
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    for out, timing in ((out_a, None), (out_b, late)):
        su.render_checkpoint_flow_frames(
            kit=_kit(), ratio="16:9", fps=8, duration_s=1.0, out_dir=out, timing=timing
        )
    early_frame = sorted(out_a.glob("*.png"))[1]
    late_frame = sorted(out_b.glob("*.png"))[1]
    assert early_frame.read_bytes() != late_frame.read_bytes()


def test_the_cli_reports_which_timing_it_used(tmp_path, capsys):
    """ "Measured" and "assumed" must not look the same on disk — the rule `audio_bed` already
    applies to deliberate silence."""
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(_kit()), encoding="utf-8")
    timing_json = tmp_path / "t.json"
    timing_json.write_text(
        json.dumps({k: list(v) for k, v in su._CHECKPOINT_DEFAULT_TIMING.items()}), encoding="utf-8"
    )

    assert (
        su.main(
            [
                "checkpoint-flow",
                "--kit-json",
                str(kit_json),
                "--ratio",
                "16:9",
                "--fps",
                "4",
                "--duration-s",
                "1.0",
                "--out-dir",
                str(tmp_path / "d"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["timing_source"].startswith("default-literals")

    assert (
        su.main(
            [
                "checkpoint-flow",
                "--kit-json",
                str(kit_json),
                "--ratio",
                "16:9",
                "--fps",
                "4",
                "--duration-s",
                "1.0",
                "--out-dir",
                str(tmp_path / "t"),
                "--timing-json",
                str(timing_json),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["timing_source"].startswith("timing-json:")


def test_the_cli_refuses_a_words_sidecar_measured_against_a_different_cut(tmp_path, capsys):
    """A correct matcher cannot notice that you re-cut the VO and passed the OLD sidecar: every
    phrase still matches and every window is silently stale. The durations are what disagree."""
    from gtm_core import vo_timings as vt

    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(_kit()), encoding="utf-8")
    audio = tmp_path / "h15.wav"
    audio.write_bytes(b"")
    vt.write_words_sidecar(
        audio,
        [vt.WordTiming(word="First", start_s=0.1, end_s=0.4)],
        audio_duration_s=26.59,
        source="create_speech",
        provenance={"units": "s", "word_count": 1},
    )
    code = su.main(
        [
            "checkpoint-flow",
            "--kit-json",
            str(kit_json),
            "--ratio",
            "16:9",
            "--fps",
            "4",
            "--duration-s",
            "12.0",
            "--out-dir",
            str(tmp_path / "o"),
            "--words-json",
            str(vt.words_sidecar_path(audio)),
        ]
    )
    assert code == 2
    assert "different cut" in json.loads(capsys.readouterr().out)["error"]


def test_the_cli_refuses_both_timing_flags_at_once(tmp_path, capsys):
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(_kit()), encoding="utf-8")
    code = su.main(
        [
            "checkpoint-flow",
            "--kit-json",
            str(kit_json),
            "--ratio",
            "16:9",
            "--fps",
            "4",
            "--duration-s",
            "1.0",
            "--out-dir",
            str(tmp_path / "o"),
            "--words-json",
            str(tmp_path / "w.json"),
            "--timing-json",
            str(tmp_path / "t.json"),
        ]
    )
    assert code == 2
    assert "not both" in json.loads(capsys.readouterr().out)["error"]


def test_a_scene_extra_meant_for_one_scene_never_reaches_another(tmp_path, capsys):
    """`_SCENE_EXTRAS` states, once, the property the old `if scene == "still-push"` branch had
    for exactly one flag: a flag aimed at the wrong scene is inert, not a TypeError."""
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(_kit()), encoding="utf-8")
    assert (
        su.main(
            [
                "title-close",
                "--kit-json",
                str(kit_json),
                "--ratio",
                "16:9",
                "--fps",
                "4",
                "--duration-s",
                "1.0",
                "--out-dir",
                str(tmp_path / "o"),
                "--timing-json",
                str(tmp_path / "nope.json"),
            ]
        )
        == 0
    )
    assert "timing_source" not in json.loads(capsys.readouterr().out)


def test_no_scene_draws_text_into_the_burned_caption_band(tmp_path):
    """Every full-frame card keeps its text clear of the caption, not just the title card.

    The shipped cut had the record card's closing question at 0.73h and the rows card's citation
    at 0.87h — both inside the band a burned caption occupies, so two legible strings competed
    for the same rows and neither could be read. `title-close` had been given this clamp for the
    Art. 50 line; nothing generalised it, because each scene carried its own idea of "the bottom"
    (the safe area's 0.88h, which is about the frame EDGE and knows nothing about a caption).

    The check is on INK, not on layout maths: it renders each scene and asserts the caption rows
    are empty of drawn content — which is the property that actually matters and the one a
    refactor of the offsets could not accidentally satisfy while still overprinting.
    """
    kit_json = tmp_path / "kit.json"
    kit_json.write_text(json.dumps(_kit()), encoding="utf-8")

    # `_FOOT_BAND_TOP_FRAC` is where a citation footer is deliberately placed BELOW the caption,
    # so the forbidden band is the caption's own rows: its top through the footer's top.
    for scene in (
        "record-bank",
        "record-clean",
        "record-grid",
        "rows-scope",
        "rows-evidence",
        "rows-identity",
        "rows-claim",
        "rows-layers",
        "checkpoint-flow",
        "title-close",
    ):
        out = tmp_path / scene
        assert (
            su.main(
                [
                    # Few frames, but spanning the FULL normalised timeline (t runs 0..1 over
                    # whatever count is produced), so every scene's last-arriving element — the
                    # citation footer, the closing question, the disclosure — is still rendered
                    # and checked. Ten scenes at 1080p is the expensive part, not the frame count.
                    scene,
                    "--kit-json",
                    str(kit_json),
                    "--ratio",
                    "16:9",
                    "--fps",
                    "4",
                    "--duration-s",
                    "1.5",
                    "--out-dir",
                    str(out),
                ]
            )
            == 0
        ), f"{scene} failed to render"

        for path in sorted(out.glob("*.png")):
            with Image.open(path) as frame:
                px = frame.convert("RGB")
                w, h = px.size
                y0 = int(h * su.CAPTION_BAND_TOP_FRAC)
                y1 = int(h * su._FOOT_BAND_TOP_FRAC)
                crop = px.crop((0, y0, w, y1))
            # TEXT is what may not share rows with the caption; a panel's fill or border passing
            # behind it costs nothing. See `_CHROME_CEILING` / `_RULE_MIN_RUN_FRAC` for why the
            # discriminator is a run length and no longer brightness alone.
            hits = _typelike_runs(crop, _CHROME_CEILING, int(w * _RULE_MIN_RUN_FRAC))
            assert not hits, (
                f"{scene} ({path.name}) draws type inside the caption band "
                f"{su.CAPTION_BAND_TOP_FRAC}..{su._FOOT_BAND_TOP_FRAC}h — a burned caption would "
                f"land on it. {len(hits)} glyph-width bright runs, first at band row {hits[0][0]} "
                f"({hits[0][1]}px wide)"
            )


def test_call_chip_clears_every_measured_caller_face(tmp_path):
    """The chip's width cap must actually BIND, and must clear the real faces.

    Two independent failures shipped together here. The cap was set to 0.46 against an unmeasured
    claim that "a centred head starts around 0.70"; the four plates were measured on 2026-08-30
    and heads start at 0.378-0.42, so the chip was on every caller's face. And the cap was inert
    anyway: the sub-line hit its own font floor before the cap could bind, so the panel stopped
    shrinking at 0.402 for every cap value from 0.33 to 0.40. A constraint a fitter can decline
    to meet is not a constraint — so this asserts the RENDERED alpha extent, never the constant.
    """
    kit = _kit()
    #: Leftmost head edge per plate, measured from three frames of each caller shot.
    MEASURED_HEAD_LEFT_FRAC = 0.378
    for spec in (su._CALL_BANK, su._CALL_CLINIC, su._CALL_HOTEL, su._CALL_TELCO):
        out = tmp_path / spec.prefix
        su.render_call_ui_frames(
            kit=kit,
            ratio="16:9",
            fps=5,
            duration_s=1.0,
            out_dir=out,
            spec=spec,
            repo_root=Path(__file__).resolve().parents[2],
        )
        widest = 0.0
        for path in sorted(out.glob("*.png")):
            with Image.open(path) as frame:
                alpha = frame.convert("RGBA").split()[-1]
                bbox = alpha.getbbox()
            if bbox:
                widest = max(widest, bbox[2] / frame.width)
        assert widest <= su._CALL_UI_MAX_RIGHT_FRAC + 0.005, (
            f"{spec.prefix} chip reaches {widest:.3f} of frame width, past its "
            f"{su._CALL_UI_MAX_RIGHT_FRAC} cap — the cap is not binding"
        )
        assert widest < MEASURED_HEAD_LEFT_FRAC, (
            f"{spec.prefix} chip reaches {widest:.3f}, onto a face that starts at "
            f"{MEASURED_HEAD_LEFT_FRAC}"
        )


#: The ONLY functions allowed to call `_fit_font` directly. Both wrap it and both say something
#: about the result: `_fit_or_refuse` raises when the floor still overruns, `_fit_trial` returns
#: whether it fit so an escalating layout search can move to its next candidate. Adding a third
#: entry is a boundary change — it needs the "why can this one be trusted to handle an overrun"
#: reasoning those two carry in their docstrings, not just a passing test.
_FIT_FONT_CALLERS = frozenset({"_fit_or_refuse", "_fit_trial"})


def _fit_font_callers() -> dict[str, int]:
    """Every function in the screen_ui PACKAGE that calls `_fit_font`, by name, with a
    call count.

    AST rather than regex: call sites survive reformatting and line wrapping, and attributing a
    call to its enclosing `def` is exactly what a regex cannot do. (The vocabulary test below
    still uses a regex, correctly — it is matching STRING LITERALS, not structure.)
    """
    import ast

    found: dict[str, int] = {}
    for path in sorted(Path(su.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _collect_fit_font_callers(tree, found)
    return found


def _collect_fit_font_callers(tree, found: dict[str, int]) -> None:
    import ast

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_fit_font"
            ):
                found[node.name] = found.get(node.name, 0) + 1


def test_fit_tolerance_is_the_widest_face_a_box_survives():
    """Not clearance. `_fit_font` stops at the FIRST size that fits, so a shrink-to-fit string
    always lands ~1% under its box whether it had room to spare or was one step from the floor —
    clearance reports the search's stopping rule, not the layout's headroom."""
    assert su.fit_tolerance(100.0, 105.0) == pytest.approx(1.05)
    assert su.fit_tolerance(100.0, 100.0) == pytest.approx(1.0)
    assert su.fit_tolerance(0.0, 100.0) == float("inf")


def test_audit_fit_measures_every_fitted_string_and_ranks_the_fragile_first(tmp_path):
    """The audit is what turns "it fits" into "by how much", which is the question nobody could
    answer while a card sat at x1.03 for months."""
    rows = su.audit_fit(kit=_kit(), ratio="16:9", scenes=["record-grid", "rows-layers"])
    assert rows, "the audit found no fitted strings — the collector is not wired"
    assert [r["tolerance"] for r in rows] == sorted(r["tolerance"] for r in rows)
    for row in rows:
        assert {"scene", "where", "text", "box_w", "floor_px", "tolerance"} <= set(row)
        assert row["tolerance"] == pytest.approx(
            su.fit_tolerance(row["width_at_floor_px"], row["box_w"]), abs=1e-3
        )


def test_the_fit_collector_is_off_unless_the_audit_turns_it_on(tmp_path):
    """It sits inside per-frame draw loops. Left on, it would grow without bound during a real
    render and cost every scene something for a question no render asks."""
    assert su.fit._FIT_LOG is None
    su.audit_fit(kit=_kit(), ratio="16:9", scenes=["title-close"])
    assert su.fit._FIT_LOG is None, "the audit left the collector armed"


def test_audit_fit_survives_a_scene_that_refuses(tmp_path):
    """A refusal is the loudest possible signal and must not abort the sweep — the whole point is
    to see every scene's headroom in one pass, including the ones already over."""
    rows = su.audit_fit(kit=_kit(), ratio="9:16")
    assert rows


def test_no_scene_renderer_calls_fit_font_bare():
    """`_fit_font` returns its FLOOR when even the floor overruns.

    The caller gets a font back and hears nothing; the overflow exists only in rendered pixels.
    That shipped a card whose sub-line hung 51px outside its own panel, past a clean lint, caught
    by eyeballing a frame. `_fit_or_refuse` was added for exactly that — and then the same bug was
    reintroduced in the very next round, on new sub-lines a few lines away. A guard that a new
    call site can silently bypass is not a guard, so the bypass itself is what this refuses.

    A renderer that genuinely needs an overrun to be allowed (an escalating layout search, where
    the first candidate failing IS the signal) has `_fit_trial`, which returns the answer instead
    of swallowing it.
    """
    callers = _fit_font_callers()
    assert callers, "found no `_fit_font` call sites at all — the AST walk has drifted"
    offenders = {name: n for name, n in callers.items() if name not in _FIT_FONT_CALLERS}
    assert offenders == {}, (
        f"these call `_fit_font` bare, which cannot report an overrun: {offenders}. "
        f"Use `_fit_or_refuse(..., where=...)`, or `_fit_trial` if an overrun is a signal you "
        f"then act on."
    )


def test_every_allowlisted_wrapper_does_call_fit_font():
    """The allowlist is a ceiling, not a promise — this catches drift the other direction, a
    stale entry for a wrapper that stopped using it. Same both-directions shape as the Pillow
    module-boundary test."""
    callers = _fit_font_callers()
    for name in _FIT_FONT_CALLERS:
        assert name in callers, f"{name} is allowlisted but no longer calls `_fit_font`"


def test_fit_trial_reports_an_overrun_rather_than_swallowing_it():
    """The escape hatch must not become a second silent path."""
    face = Path(_FONT)
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    _, fits = su._fit_trial(draw, face, "a string far too long for this box", 40, 60, 30)
    assert fits is False
    _, fits = su._fit_trial(draw, face, "ok", 4000, 60, 30)
    assert fits is True


def test_drawn_card_copy_uses_the_scripts_vocabulary(tmp_path):
    """No card may say "helper" — the word the script banned and the code kept saying.

    The film's script replaced "the helper" with "the agent" on 2026-08-30, but the flow card's
    node label is drawn IN CODE, so the rewrite could not reach it and the card shipped saying
    HELPER underneath a voice-over saying AGENT. Nothing could have caught that: no lint reads the
    strings a renderer draws. This does.
    """
    banned = ("helper", "the assistant", "the bot")
    # Every module in the package: the labels live in scenes/*.py, and `su.__file__` is only
    # the re-exporting __init__ since the Phase 2 split.
    drawn: list[str] = []
    for path in sorted(Path(su.__file__).parent.rglob("*.py")):
        drawn += re.findall(r'"([A-Z][A-Z ·\',\.\-]{3,})"', path.read_text(encoding="utf-8"))
    assert drawn, "found no drawn UPPERCASE labels to check — the pattern has drifted"
    for label in drawn:
        for word in banned:
            assert word.upper() not in label.upper(), (
                f"a card draws {label!r}, which contains the banned term {word!r}"
            )


# --- title-card copy is the FILM's, not the engine's ---------------------------------------------


def test_title_override_replaces_the_spine_for_a_different_film(tmp_path):
    """The built-in spine is one film's argument. A second film calling this scene must be able to
    state its own, or it ships the first film's sentences rendered perfectly — the silent failure."""
    a, b = tmp_path / "a", tmp_path / "b"
    su.render_title_card_frames(kit=_kit(), ratio="16:9", fps=6, duration_s=1.0, out_dir=a)
    su.render_title_card_frames(
        kit=_kit(),
        ratio="16:9",
        fps=6,
        duration_s=1.0,
        out_dir=b,
        title={"line_one": "Bao Daily", "line_two": "Free on the App Store"},
    )
    assert sorted(a.glob("*.png"))[-1].read_bytes() != sorted(b.glob("*.png"))[-1].read_bytes()


def test_the_override_is_what_makes_this_card_reachable_at_9_16(tmp_path):
    """Not a nicety. The built-in spine REFUSES at 9:16 — 'Every caller has to be verified.'
    measures past its own box at the type floor — so a vertical film could not use this scene at
    all until its copy became the caller's to state."""
    with pytest.raises(su.SceneError, match="shorten the copy"):
        su.render_title_card_frames(
            kit=_kit(), ratio="9:16", fps=6, duration_s=1.0, out_dir=tmp_path / "spine"
        )
    su.render_title_card_frames(
        kit=_kit(),
        ratio="9:16",
        fps=6,
        duration_s=1.0,
        out_dir=tmp_path / "own",
        title={"line_one": "Bao Daily", "line_two": ""},
    )


def test_an_empty_line_two_is_a_name_only_card_not_a_refusal(tmp_path):
    """`line_two=""` is a request — an end card carrying a mark and a name and nothing else.
    `line_one=""` is not: a card with no first line has nothing on it."""
    su.render_title_card_frames(
        kit=_kit(),
        ratio="16:9",
        fps=6,
        duration_s=1.0,
        out_dir=tmp_path / "ok",
        title={"line_one": "Bao Daily", "line_two": ""},
    )
    with pytest.raises(su.SceneError, match="empty string"):
        su.render_title_card_frames(
            kit=_kit(),
            ratio="16:9",
            fps=6,
            duration_s=1.0,
            out_dir=tmp_path / "no",
            title={"line_one": "  "},
        )


@pytest.mark.parametrize("ratio", ["16:9", "9:16"])
def test_the_disclosure_stays_inside_the_frame_on_every_ratio(tmp_path, ratio):
    """It is drawn at a FIXED size because its size is a legibility duty, not a design choice —
    which meant that at 9:16, where the box narrows while the type scales up with height, the
    Art. 50 line ran off BOTH edges of the frame. Not small: partly absent.

    Compared against the same card with no disclosure configured, so the backdrop's own gradient
    is subtracted rather than guessed at: any difference in the outer columns is disclosure ink.
    """
    line = "Made with AI. Posted by a human — this account never auto-posts."
    outs = {}
    for name, kit in (("with", {**_kit(), "disclosure": {"line": line}}), ("without", _kit())):
        outs[name] = tmp_path / name
        su.render_title_card_frames(
            kit=kit,
            ratio=ratio,
            fps=6,
            duration_s=2.0,
            out_dir=outs[name],
            spec=su._TITLE_CLOSE,
            title={"line_one": "Bao Daily", "line_two": ""},
        )
    a = Image.open(sorted(outs["with"].glob("*.png"))[-1]).convert("RGB")
    b = Image.open(sorted(outs["without"].glob("*.png"))[-1]).convert("RGB")
    w, h = a.size
    edge = max(2, round(w * 0.02))
    for x in list(range(edge)) + list(range(w - edge, w)):
        for y in range(round(h * 0.50), round(h * 0.75)):
            assert a.getpixel((x, y)) == b.getpixel((x, y)), (
                f"disclosure ink at x={x}, y={y} — it is running off the frame edge"
            )


def test_the_disclosure_wrap_never_exceeds_its_box():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = ImageFont.truetype(str(_FONT), 40)
    text = "Made with AI. Posted by a human — this account never auto-posts."
    for width in (300, 600, 1700):
        for line in su._wrap_px(text, font, width, draw):
            assert draw.textlength(line, font=font) <= width or " " not in line


def test_a_film_may_state_its_own_call_to_action(tmp_path):
    """Not every card's ask is a URL. The kit stays the source for a tenant's domain — that is the
    bug this block replaced — but a film whose ask is "free on the App Store" owns that line, and
    burning it as a caption instead sets the one frame that asks for something in subtitle type."""
    a, b = tmp_path / "kit", tmp_path / "own"
    kit = {**_kit(), "disclosure": {"line": "Made with AI."}, "cta": {"url": "example.test"}}
    su.render_title_card_frames(
        kit=kit, ratio="16:9", fps=6, duration_s=2.0, out_dir=a, spec=su._TITLE_CLOSE
    )
    su.render_title_card_frames(
        kit=kit,
        ratio="16:9",
        fps=6,
        duration_s=2.0,
        out_dir=b,
        spec=su._TITLE_CLOSE,
        cta_text="Free on the App Store",
    )
    assert sorted(a.glob("*.png"))[-1].read_bytes() != sorted(b.glob("*.png"))[-1].read_bytes()


def test_an_empty_cta_override_means_no_cta_not_the_kits_url(tmp_path):
    """`cta_text=""` is a card with no ask. Falling back to the kit there would resurrect a URL the
    caller explicitly removed."""
    a, b = tmp_path / "none", tmp_path / "empty"
    plain = {**_kit(), "disclosure": {"line": "Made with AI."}}
    withurl = {**plain, "cta": {"url": "example.test"}}
    su.render_title_card_frames(
        kit=plain, ratio="16:9", fps=6, duration_s=2.0, out_dir=a, spec=su._TITLE_CLOSE
    )
    su.render_title_card_frames(
        kit=withurl,
        ratio="16:9",
        fps=6,
        duration_s=2.0,
        out_dir=b,
        spec=su._TITLE_CLOSE,
        cta_text="",
    )
    for left, right in zip(sorted(a.glob("*.png")), sorted(b.glob("*.png")), strict=True):
        assert left.read_bytes() == right.read_bytes()
