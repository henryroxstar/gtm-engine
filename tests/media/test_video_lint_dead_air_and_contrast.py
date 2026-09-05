"""V10 (dead air), V11 (caption contrast), and the per-ratio face band that V3 was missing.

All three defects shipped in `three-questions-p1` (2026-08-28) past a fully green lint:

  - 39% of the runtime was digital silence, in runs of 10.4s / 25.1s / 4.1s. Ten of eighteen
    shots were concatenated with no audio stream at all.
  - `Probe.has_audio` had been captured since V1 shipped, read by no rule, and was itself
    unconditionally False because `probe()` passed `-select_streams v:0`.
  - 64 caption screens sat at one fixed y across the presenter's forehead, and V3's face band —
    calibrated on a 4:5 medium-close-up — started 0.22 down the frame, exactly where the eyes do.

These run against a fabricated `Probe` and hand-written context dicts, so they assert the pure
layer with no ffmpeg and no media on the machine (the tier-B pattern the module was built for).
"""

from __future__ import annotations

import pytest

from gtm_core import captions as cap
from gtm_core import video_lint as vl


def _probe(**over) -> vl.Probe:
    base = {
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration_s": 100.0,
        "bit_rate": 8_000_000,
        "has_audio": True,
        "pix_fmt": "yuv420p",
    }
    return vl.Probe(**{**base, **over})


def _tiers(findings, tier: str) -> list[vl.Finding]:
    return [f for f in findings if f.tier == tier]


# ── V10 — dead air ─────────────────────────────────────────────────────────────────────────


def test_no_audio_stream_at_all_is_an_error():
    found = _tiers(vl.evaluate(_probe(has_audio=False), ratio="9:16"), "V10")
    assert [f.rule for f in found] == ["no_audio_stream"]
    assert found[0].severity == vl.ERROR


def test_the_shipped_defect_fires_on_both_triggers():
    """The real measurement from three-questions-p1: 39% silent, longest run 25.1s."""
    ctx = {
        "silent_runs": [
            {"start": 0.0, "end": 10.384, "duration": 10.384},
            {"start": 13.708, "end": 38.838, "duration": 25.13},
            {"start": 70.363, "end": 74.493, "duration": 4.129},
        ],
        "silent_total_s": 39.643,
        "silent_fraction": 0.3938,
    }
    rules = {f.rule for f in _tiers(vl.evaluate(_probe(), ratio="9:16", audio_context=ctx), "V10")}
    assert rules == {"dead_air_fraction", "dead_air_run"}


def test_a_deliberate_beat_is_not_dead_air():
    """One held pause in a long film clears both triggers — the tier must not tax good editing."""
    ctx = {
        "silent_runs": [{"start": 40.0, "end": 42.5, "duration": 2.5}],
        "silent_total_s": 2.5,
        "silent_fraction": 0.025,
    }
    assert _tiers(vl.evaluate(_probe(), ratio="9:16", audio_context=ctx), "V10") == []


def test_a_short_film_that_is_mostly_dead_fires_on_fraction_alone():
    """No single run reaches the 5s ceiling, but 30% of the asset is silence."""
    ctx = {
        "silent_runs": [
            {"start": 1.0, "end": 3.5, "duration": 2.5},
            {"start": 8.0, "end": 10.5, "duration": 2.5},
        ],
        "silent_total_s": 5.0,
        "silent_fraction": 0.30,
    }
    found = _tiers(vl.evaluate(_probe(duration_s=16.6), ratio="9:16", audio_context=ctx), "V10")
    assert [f.rule for f in found] == ["dead_air_fraction"]


def test_one_long_run_fires_even_when_the_overall_fraction_is_fine():
    """The mirror case: a 6s hole in a 3-minute film is 3% of runtime and still a broken file."""
    ctx = {
        "silent_runs": [{"start": 30.0, "end": 36.0, "duration": 6.0}],
        "silent_total_s": 6.0,
        "silent_fraction": 0.033,
    }
    found = _tiers(vl.evaluate(_probe(duration_s=180.0), ratio="9:16", audio_context=ctx), "V10")
    assert [f.rule for f in found] == ["dead_air_run"]


def test_missing_measurement_is_not_a_clean_pass_by_accident():
    """No audio_context means the pass could not run, not that the asset is fine."""
    assert _tiers(vl.evaluate(_probe(), ratio="9:16", audio_context=None), "V10") == []


# ── V11 — caption contrast ─────────────────────────────────────────────────────────────────


def test_the_shipped_opening_frame_fails_aa():
    ctx = [{"index": 0, "ratio": 3.67, "bg_luma": 129.2}, {"index": 5, "ratio": 12.79}]
    found = _tiers(vl.evaluate(_probe(), ratio="9:16", caption_contrast=ctx), "V11")
    assert len(found) == 1
    assert found[0].severity == vl.ERROR
    assert "1 of 2" in found[0].excerpt and "3.7:1" in found[0].excerpt


def test_captions_that_clear_aa_produce_nothing():
    ctx = [{"index": i, "ratio": 7.4} for i in range(8)]
    assert _tiers(vl.evaluate(_probe(), ratio="9:16", caption_contrast=ctx), "V11") == []


def test_exactly_at_the_threshold_passes():
    ctx = [{"index": 0, "ratio": vl.MIN_CAPTION_CONTRAST_RATIO}]
    assert _tiers(vl.evaluate(_probe(), ratio="9:16", caption_contrast=ctx), "V11") == []


# ── the WCAG maths itself — this is where the 2026-08-28 misreport came from ────────────────


@pytest.mark.parametrize(
    ("bg", "expected"),
    [(0, 21.0), (255, 1.0)],
)
def test_relative_luminance_anchors(bg, expected):
    lo, hi = sorted((cap._relative_luminance((bg, bg, bg)), cap._relative_luminance((255,) * 3)))
    assert round((hi + 0.05) / (lo + 0.05), 2) == expected


def test_luminance_is_linearised_not_raw():
    """The bug this guards: `mean/255` used directly as relative luminance reports luma 85 as
    2.7:1 (a fail) when the correct WCAG figure is ~7.5:1 (a comfortable pass). A whole
    "every caption fails contrast" finding was produced from that one missing step."""
    lin = cap._relative_luminance((85, 85, 85))
    naive = 85 / 255
    assert lin < naive / 2, "sRGB was not linearised"
    assert 7.0 < (1.05 / (lin + 0.05)) < 8.0


# ── V3 — the per-ratio face band ───────────────────────────────────────────────────────────


def test_a_vertical_presenter_caption_at_the_old_band_now_fails():
    """y=192-283 in a 1920 frame: inside the safe area, above the old 0.22 floor, and squarely on
    a 9:16 avatar's forehead. This is the exact geometry that shipped."""
    manifest = {
        "frame": [1080, 1920],
        "screens": [{"index": i, "box": {"x": 284, "y": 192, "w": 512, "h": 91}} for i in range(3)],
    }
    found = _tiers(
        vl.evaluate(_probe(), ratio="9:16", manifest=manifest, identity_used=["soul", "voice"]),
        "V3",
    )
    assert [f.rule for f in found] == ["caption_over_face"]
    assert found[0].severity == vl.ERROR


def test_the_same_geometry_is_still_clean_on_4x5():
    """The original 4:5 calibration must not regress: 0.22 is correct for a medium-close-up."""
    manifest = {
        "frame": [1080, 1350],
        "screens": [{"index": 0, "box": {"x": 284, "y": 120, "w": 512, "h": 91}}],
    }
    p = _probe(width=1080, height=1350)
    found = _tiers(
        vl.evaluate(p, ratio="4:5", manifest=manifest, identity_used=["soul"]),
        "V3",
    )
    assert found == []


def test_identical_screens_collapse_into_one_finding():
    """64 copies of one defect is an unreadable report, which is the same as no gate at all."""
    manifest = {
        "frame": [1080, 1920],
        "screens": [
            {"index": i, "box": {"x": 284, "y": 192, "w": 512, "h": 91}} for i in range(64)
        ],
    }
    found = _tiers(
        vl.evaluate(_probe(), ratio="9:16", manifest=manifest, identity_used=["soul"]), "V3"
    )
    assert len(found) == 1
    assert "64 screens (0-63)" in found[0].excerpt


def test_distinct_geometries_stay_distinct():
    manifest = {
        "frame": [1080, 1920],
        "screens": [
            {"index": 0, "box": {"x": 284, "y": 192, "w": 512, "h": 91}},
            {"index": 1, "box": {"x": 284, "y": 192, "w": 512, "h": 182}},
        ],
    }
    found = _tiers(
        vl.evaluate(_probe(), ratio="9:16", manifest=manifest, identity_used=["soul"]), "V3"
    )
    assert len(found) == 2


def test_the_fix_never_names_a_placement_that_cannot_exist():
    """On 9:16 the face band starts above the safe box, so 'upper' is unreachable — telling the
    caller to use it is an instruction they cannot follow."""
    manifest = {
        "frame": [1080, 1920],
        "screens": [{"index": 0, "box": {"x": 284, "y": 192, "w": 512, "h": 91}}],
    }
    fix = _tiers(
        vl.evaluate(_probe(), ratio="9:16", manifest=manifest, identity_used=["soul"]), "V3"
    )[0].fix
    assert "placement='lower'" in fix
    assert "'upper'" not in fix


# ── the renderer and the linter must agree, or the gate is unsatisfiable ───────────────────


@pytest.mark.parametrize("ratio", sorted(vl.SAFE_AREAS))
def test_resolve_placement_never_returns_an_unrenderable_placement(ratio):
    kit = {"captions": {"preset": "system_indigo"}}  # a real tenant kit; implies 'upper'
    placement = cap.resolve_placement(kit, ratio=ratio)
    if placement == "upper":
        assert cap.upper_placement_fits(ratio)


@pytest.mark.parametrize("ratio", ["9:16", "16:9"])
def test_render_refuses_an_impossible_upper_rather_than_clamping(ratio):
    with pytest.raises(ValueError, match="no room"):
        cap.render([], ratio=ratio, kit={}, out_dir=None, placement="upper")  # type: ignore[arg-type]


def test_probe_sees_the_audio_stream():
    """`-select_streams v:0` made has_audio unconditionally False for the module's whole life."""
    payload = {
        "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "30/1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "100.0", "bit_rate": "8000000"},
    }
    assert vl.Probe.from_ffprobe_json(payload).has_audio is True


def test_an_attached_cover_image_is_not_mistaken_for_the_video_track():
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 640,
                "height": 640,
                "r_frame_rate": "0/0",
                "disposition": {"attached_pic": 1},
            },
            {"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "30/1"},
        ],
        "format": {"duration": "100.0", "bit_rate": "8000000"},
    }
    p = vl.Probe.from_ffprobe_json(payload)
    assert (p.width, p.height) == (1080, 1920)


# ── V5/V8 were dormant: nothing wrote the keys the CLI read ─────────────────────────────────


def test_the_finish_plan_now_carries_the_declared_mix_facts():
    from gtm_core.video_finish import plan

    p = plan(
        profile="p",
        slug="t",
        ratio="9:16",
        source="a.mp4",
        spec={
            "caption_text": "hi",
            "total_s": 10,
            "has_music_bed": True,
            "has_voice": True,
            "ducking_applied": False,
            "spoken_text": "hi there",
        },
    )
    assert p.audio_context == {
        "has_music_bed": True,
        "has_voice": True,
        "ducking_applied": False,
        "loudness_target": -14.0,
    }
    assert p.spoken_text == "hi there"


def test_declaring_nothing_is_not_the_same_as_declaring_no_bed():
    from gtm_core.video_finish import plan

    p = plan(profile="p", slug="t", ratio="9:16", source="a.mp4", spec={})
    assert p.audio_context is None


def test_v5_fires_once_the_producer_declares_an_unducked_bed():
    """The tier's code is unchanged; it simply never had an input before."""
    ctx = {"has_music_bed": True, "has_voice": True, "ducking_applied": False}
    found = _tiers(vl.evaluate(_probe(), ratio="9:16", audio_context=ctx), "V5")
    assert [f.rule for f in found] == ["music_bed_without_ducking"]


def test_segments_without_caption_text_are_refused_not_silently_dropped():
    """The defect: a spec with segments and no caption_text skipped the whole captions stage and
    produced a fully linted, completely uncaptioned asset."""
    from gtm_core.video_finish import PlanError, plan

    with pytest.raises(PlanError, match="caption_segments but no caption_text"):
        plan(
            profile="p",
            slug="t",
            ratio="9:16",
            source="a.mp4",
            spec={"caption_segments": [{"text": "hi", "start_s": 0, "end_s": 1}]},
        )
