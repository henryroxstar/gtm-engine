"""gtm_core.video_lint's pure `evaluate()` layer — V1-V4 boundary tables against a fabricated
Probe. No ffmpeg/ffprobe on the machine required: this is the split (probe() shells out;
evaluate() does not) that makes tier B honest.

Ffmpeg-backed tests (probe() itself, real fixture generation, the calibration test against the
real shipped asset) are a separate, larger build item and land alongside gtm_core/video_finish.py.
"""

from __future__ import annotations

import pytest

from gtm_core import video_lint as vl


def _tiers(findings, *, severity=None):
    return {f.tier for f in findings if severity is None or f.severity == severity}


def _mk(width=1080, height=1920, fps=30.0, duration_s=10.0, bit_rate=8_000_000, **kw):
    return vl.Probe(
        width=width, height=height, fps=fps, duration_s=duration_s, bit_rate=bit_rate, **kw
    )


# ── V1: resolution / fps / bitrate ──────────────────────────────────────────────────────


def test_v1_clean_probe_has_no_v1_finding():
    findings = vl.evaluate(_mk(), ratio="9:16")
    assert "V1" not in _tiers(findings)


def test_v1_the_shipped_defect_resolution_errors_fps_only_warns():
    """The exact shape of the real asset that shipped: 720x1280 @ 24fps. Resolution is a hard
    ERROR; 24fps sits exactly on the WARN floor (cinematic-only), not the ERROR floor."""
    p = _mk(width=720, height=1280, fps=24.0, bit_rate=1_420_000)
    findings = vl.evaluate(p, ratio="9:16")
    v1 = [f for f in findings if f.tier == "V1"]
    by_rule = {f.rule: f.severity for f in v1}
    assert by_rule["resolution"] == vl.ERROR
    assert by_rule["fps"] == vl.WARN


@pytest.mark.parametrize(
    "fps,expect_severity",
    [
        (23.9, vl.ERROR),
        (24.0, vl.WARN),
        (24.5, vl.WARN),
        (29.9, vl.WARN),
        (30.0, None),
        (60.0, None),
    ],
)
def test_v1_fps_boundary(fps, expect_severity):
    findings = vl.evaluate(_mk(fps=fps), ratio="9:16")
    fps_findings = [f for f in findings if f.tier == "V1" and f.rule == "fps"]
    if expect_severity is None:
        assert not fps_findings
    else:
        assert fps_findings and fps_findings[0].severity == expect_severity


@pytest.mark.parametrize(
    "bit_rate,height,expect_error",
    [
        (5_999_999, 1920, True),
        (6_000_000, 1920, False),
        (6_000_001, 1920, False),
        (1_420_000, 1920, True),  # the shipped defect's exact measured bitrate
        (1_000_000, 720, False),  # below 1080p target — bitrate floor does not apply
        (None, 1920, False),  # unreported bitrate: not honestly assertable, so no finding
    ],
)
def test_v1_bitrate_boundary(bit_rate, height, expect_error):
    findings = vl.evaluate(_mk(height=height, bit_rate=bit_rate), ratio="9:16")
    bitrate_findings = [f for f in findings if f.tier == "V1" and f.rule == "bitrate"]
    assert bool(bitrate_findings) is expect_error


# ── V2: aspect exact ─────────────────────────────────────────────────────────────────────


def test_v2_exact_target_dims_are_clean():
    assert "V2" not in _tiers(vl.evaluate(_mk(width=1080, height=1920), ratio="9:16"))


def test_v2_within_one_px_tolerance_is_clean():
    assert "V2" not in _tiers(vl.evaluate(_mk(width=1081, height=1919), ratio="9:16"))


def test_v2_the_soul_2_4x5_to_3x4_coercion_errors():
    """identity/henry/IDENTITY.toml [render_behaviour]: a requested 4:5 render actually comes
    back 3:4. 1080x1350 (4:5) requested; provider ships ~1080x1440 (3:4)."""
    p = _mk(width=1080, height=1440)
    findings = vl.evaluate(p, ratio="4:5")
    v2 = [f for f in findings if f.tier == "V2"]
    assert v2 and v2[0].severity == vl.ERROR


def test_v2_the_real_shipped_asset_was_the_right_ratio_at_the_wrong_resolution():
    """Pins that V2 is diagnostic, not just 'everything fails': the one asset this system
    shipped WAS 9:16 (720x1280 scales exactly to 1080x1920) — V2 must stay clean on it even
    though V1 (resolution) correctly errors on the same probe."""
    p = _mk(width=720, height=1280, fps=24.0)
    findings = vl.evaluate(p, ratio="9:16")
    assert "V2" not in _tiers(findings)
    assert "V1" in _tiers(findings)


# ── V3: caption geometry — sidecar-bbox primary, edge-luma WARN fallback ───────────────────


def test_v3_no_manifest_and_no_edge_stats_is_silent():
    """Nothing to check against -> no finding at all, not a pass-by-assumption."""
    assert "V3" not in _tiers(vl.evaluate(_mk(), ratio="9:16"))


def test_v3_fires_error_from_a_sidecar_bbox_spanning_the_full_width():
    manifest = {
        "frame": [1080, 1920],
        "screens": [{"index": 0, "box": {"x": 0, "y": 1700, "w": 1080, "h": 150}}],
    }
    findings = vl.evaluate(_mk(width=1080, height=1920), ratio="9:16", manifest=manifest)
    v3 = [f for f in findings if f.tier == "V3"]
    assert v3 and v3[0].severity == vl.ERROR
    assert v3[0].rule == "caption_outside_safe_area"


def test_v3_clean_when_the_bbox_sits_fully_inside_the_safe_area():
    manifest = {
        "frame": [1080, 1920],
        "screens": [{"index": 0, "box": {"x": 100, "y": 1400, "w": 880, "h": 150}}],
    }
    findings = vl.evaluate(_mk(width=1080, height=1920), ratio="9:16", manifest=manifest)
    assert "V3" not in _tiers(findings)


def test_v3_stale_sidecar_errors_when_the_manifest_frame_disagrees_with_the_probed_frame():
    """Catches a captions.json written before a rescale."""
    manifest = {"frame": [720, 1280], "screens": []}
    findings = vl.evaluate(_mk(width=1080, height=1920), ratio="9:16", manifest=manifest)
    v3 = [f for f in findings if f.tier == "V3"]
    assert v3 and v3[0].rule == "stale_sidecar" and v3[0].severity == vl.ERROR


def test_v3_falls_back_to_edge_luma_warn_when_there_is_no_sidecar():
    edge_stats = {"threshold": 60.0, "left_ymax": 235.0, "right_ymax": 18.0}
    findings = vl.evaluate(_mk(), ratio="9:16", edge_stats=edge_stats)
    v3 = [f for f in findings if f.tier == "V3"]
    assert v3 and v3[0].severity == vl.WARN
    assert "no caption manifest" in v3[0].excerpt


def test_v3_edge_luma_clean_when_all_strips_are_flat():
    edge_stats = {
        "threshold": 60.0,
        "left_ymax": 20.0,
        "right_ymax": 18.0,
        "top_ymax": 15.0,
        "bottom_ymax": 22.0,
    }
    assert "V3" not in _tiers(vl.evaluate(_mk(), ratio="9:16", edge_stats=edge_stats))


def test_v3_sidecar_takes_priority_over_edge_stats_when_both_are_given():
    manifest = {"frame": [1080, 1920], "screens": []}
    edge_stats = {"threshold": 60.0, "left_ymax": 235.0}
    findings = vl.evaluate(
        _mk(width=1080, height=1920), ratio="9:16", manifest=manifest, edge_stats=edge_stats
    )
    assert "V3" not in _tiers(
        findings
    )  # sidecar says clean (no screens); the noisy fallback is ignored


# ── V4: predictor input duration, scoped by purpose ─────────────────────────────────────


@pytest.mark.parametrize(
    "duration_s,expect_error",
    [
        (14.899, False),
        (14.900, False),
        (14.901, True),
        (15.000, True),
        (15.041667, True),  # the observed 4/4-failure duration
    ],
)
def test_v4_boundary_scoped_to_predictor_purpose(duration_s, expect_error):
    findings = vl.evaluate(_mk(duration_s=duration_s), ratio="9:16", purpose="predictor")
    assert ("V4" in _tiers(findings, severity=vl.ERROR)) is expect_error


def test_v4_a_90s_long_form_asset_is_clean_without_purpose_predictor():
    """The obvious misreading of V4 ('the asset must be <=14.9s') is wrong — a long-form asset
    is legitimately long. V4 only fires when the caller is about to hand THIS FILE to the
    predictor."""
    findings = vl.evaluate(_mk(duration_s=90.0), ratio="9:16")
    assert "V4" not in _tiers(findings)


def test_v4_a_90s_asset_with_purpose_predictor_still_errors():
    """purpose='predictor' means exactly what it says — it does not special-case long assets."""
    findings = vl.evaluate(_mk(duration_s=90.0), ratio="9:16", purpose="predictor")
    assert "V4" in _tiers(findings, severity=vl.ERROR)


# ── V5: audio ducking ────────────────────────────────────────────────────────────────────


def test_v5_warns_when_music_bed_has_no_ducking():
    findings = vl.evaluate(
        _mk(),
        ratio="9:16",
        audio_context={
            "has_music_bed": True,
            "has_voice": True,
            "ducking_applied": False,
        },
    )
    v5 = [f for f in findings if f.tier == "V5"]
    assert v5 and v5[0].severity == vl.WARN
    assert v5[0].rule == "music_bed_without_ducking"


def test_v5_warns_when_ducked_music_is_still_too_loud():
    findings = vl.evaluate(
        _mk(),
        ratio="9:16",
        audio_context={
            "has_music_bed": True,
            "has_voice": True,
            "ducking_applied": True,
            "music_lufs": -12.0,
            "voice_lufs": -10.0,
        },
    )
    v5 = [f for f in findings if f.tier == "V5"]
    assert v5 and v5[0].severity == vl.WARN
    assert v5[0].rule == "music_bed_too_loud"


def test_v5_clean_when_music_is_ducked_and_quiet():
    findings = vl.evaluate(
        _mk(),
        ratio="9:16",
        audio_context={
            "has_music_bed": True,
            "has_voice": True,
            "ducking_applied": True,
            "music_lufs": -22.0,
            "voice_lufs": -10.0,
        },
    )
    assert "V5" not in _tiers(findings)


def test_v5_silent_without_music_bed():
    findings = vl.evaluate(
        _mk(),
        ratio="9:16",
        audio_context={
            "has_music_bed": False,
            "has_voice": True,
            "ducking_applied": False,
        },
    )
    assert "V5" not in _tiers(findings)


# ── V6: scene-change cadence ─────────────────────────────────────────────────────────────


def test_v6_warns_when_no_scene_changes_in_long_asset():
    findings = vl.evaluate(_mk(duration_s=20.0), ratio="9:16", scene_changes=[])
    v6 = [f for f in findings if f.tier == "V6"]
    assert v6 and v6[0].severity == vl.WARN
    assert v6[0].rule == "no_scene_changes"


def test_v6_warns_when_scene_changes_are_too_frequent():
    changes = [0.5 * i for i in range(10)]  # 0.5s average interval
    findings = vl.evaluate(_mk(duration_s=5.0), ratio="9:16", scene_changes=changes)
    v6 = [f for f in findings if f.tier == "V6"]
    assert v6 and v6[0].severity == vl.WARN
    assert v6[0].rule == "scene_changes_too_frequent"


def test_v6_warns_when_scene_changes_are_too_sparse():
    findings = vl.evaluate(_mk(duration_s=90.0), ratio="9:16", scene_changes=[10.0])
    v6 = [f for f in findings if f.tier == "V6"]
    assert v6 and v6[0].severity == vl.WARN
    assert v6[0].rule == "scene_changes_too_sparse"


def test_v6_clean_when_cadence_is_reasonable():
    findings = vl.evaluate(_mk(duration_s=60.0), ratio="9:16", scene_changes=[5.0, 20.0, 40.0])
    assert "V6" not in _tiers(findings)


# ── cross-firing: a rule must fire on its own bad case and nothing else ─────────────────


def test_a_clean_probe_trips_nothing_at_all():
    findings = vl.evaluate(_mk(), ratio="9:16", purpose="predictor")
    assert findings == []


def test_a_clean_probe_with_an_honest_manifest_declaration_still_trips_nothing():
    """The V11 geometry rule fires on a CLAIM with no evidence; an uncaptioned cut that says so
    (`caption_route: none`) and a captioned cut that carries its boxes are both clean."""
    assert vl.evaluate(_mk(), ratio="9:16", caption_route="none") == []
    manifest = {
        "frame": [1080, 1920],
        "screens": [{"index": 0, "box": {"x": 100, "y": 1400, "w": 880, "h": 150}}],
    }
    assert (
        vl.evaluate(
            _mk(), ratio="9:16", manifest=manifest, caption_route="local", captions_preburned=True
        )
        == []
    )


def test_a_measured_soundtrack_with_events_and_no_dead_air_trips_nothing():
    """The V10 floor-only rule reads the momentary series; a track that moves is clean."""
    ctx = {
        "silent_runs": [],
        "silent_fraction": 0.0,
        "integrated_lufs": -15.4,
        "loudness_abruptness_lu": 3.4,
        "loudness_event_fraction": 0.36,
    }
    assert vl.evaluate(_mk(duration_s=30.0), ratio="9:16", audio_context=ctx) == []


def test_the_shipped_defect_trips_v1_and_v4_but_not_v2_or_v3_without_context():
    p = _mk(width=720, height=1280, fps=24.0, bit_rate=1_420_000, duration_s=15.041667)
    findings = vl.evaluate(p, ratio="9:16", purpose="predictor")
    tiers = _tiers(findings, severity=vl.ERROR)
    assert "V1" in tiers
    assert "V4" in tiers
    assert "V2" not in tiers  # was the right ratio at the wrong resolution
    assert "V3" not in tiers  # no manifest/edge_stats supplied — nothing to check


def test_unknown_ratio_raises_rather_than_silently_skipping():
    with pytest.raises(ValueError, match="unknown ratio"):
        vl.evaluate(_mk(), ratio="21:9")


# --- V7 caption reading load -------------------------------------------------
# Calibrated against the 2026-08-18 shipped asset (agent-gateway-ciso-15s): 150 on-screen words
# in 15.55s. The operator's first reported defect was being unable to read it.


#: A caption box comfortably inside the 9:16 safe area, so these fixtures exercise V7/V8 without
#: also tripping V3. (A screen with no box reads as a zero-size box at the origin, which is
#: outside the safe area — correct behaviour for V3, just noise here.)
_SAFE_BOX = {"x": 100, "y": 1200, "w": 800, "h": 300}


def _screens(*specs):
    """specs: (text, start_s, end_s) triples."""
    return {
        "frame": [1080, 1920],
        "screens": [
            {"index": i, "text": t, "start_s": a, "end_s": b, "box": dict(_SAFE_BOX)}
            for i, (t, a, b) in enumerate(specs)
        ],
    }


def test_v7_paced_captions_are_clean():
    m = _screens(("your audit log", 0.0, 1.5), ("names the agent", 1.5, 3.0))
    assert _tiers(vl.evaluate(_mk(duration_s=10.0), ratio="9:16", manifest=m)) == set()


def test_v7_a_single_overstuffed_screen_warns():
    m = _screens(("one two three four five six seven eight nine ten", 0.0, 1.0))
    findings = vl.evaluate(_mk(duration_s=30.0), ratio="9:16", manifest=m)
    assert any(f.rule == "caption_screen_too_dense" for f in findings)


def test_v7_the_shipped_asset_reading_load_warns():
    """The real 2026-08-18 asset, beat for beat: 150 words across 15.55s = 9.6 w/s."""
    beats = [
        (11, 0.00, 4.18),
        (40, 4.18, 7.60),
        (44, 7.60, 10.63),
        (16, 10.63, 12.41),
        (13, 12.41, 14.05),
        (26, 14.05, 15.55),
    ]
    m = _screens(*((" ".join(f"w{i}" for i in range(n)), a, b) for n, a, b in beats))
    findings = vl.evaluate(_mk(duration_s=15.55), ratio="9:16", manifest=m)
    rules = {f.rule for f in findings}
    assert "caption_screen_too_dense" in rules
    assert "caption_load_too_high" in rules
    assert all(f.severity == vl.WARN for f in findings if f.tier == "V7")
    load = next(f for f in findings if f.rule == "caption_load_too_high")
    assert "150 on-screen words" in load.excerpt


def test_v7_screens_without_timing_do_not_crash_and_still_count_toward_the_asset_total():
    m = {
        "frame": [1080, 1920],
        "screens": [{"index": 0, "text": "a b c d e f g h i j k l", "box": dict(_SAFE_BOX)}],
    }
    findings = vl.evaluate(_mk(duration_s=2.0), ratio="9:16", manifest=m)
    assert {f.rule for f in findings} == {"caption_load_too_high"}


# --- V8 caption/voice divergence ---------------------------------------------


def test_v8_captions_cut_from_the_spoken_line_are_clean():
    m = _screens(("the auditor wants", 0.0, 1.2), ("the person", 1.2, 2.4))
    findings = vl.evaluate(
        _mk(duration_s=10.0),
        ratio="9:16",
        manifest=m,
        spoken_text="Your audit log names the agent. The auditor wants the person.",
    )
    assert "V8" not in _tiers(findings)


def test_v8_a_second_independent_text_stream_warns():
    # the real defect: burned citation text the voice-over never says
    m = _screens(("Accession 0001628280-26-057139", 0.0, 3.0))
    findings = vl.evaluate(
        _mk(duration_s=15.0),
        ratio="9:16",
        manifest=m,
        spoken_text="Western Digital disclosed it in August: unauthorized agent actions.",
    )
    assert any(f.rule == "caption_diverges_from_voice" for f in findings)


def test_v8_punctuation_and_case_do_not_count_as_divergence():
    m = _screens(("THE AUDITOR WANTS THE PERSON.", 0.0, 3.0))
    findings = vl.evaluate(
        _mk(duration_s=10.0),
        ratio="9:16",
        manifest=m,
        spoken_text="the auditor wants the person",
    )
    assert "V8" not in _tiers(findings)


def test_v8_is_silent_without_a_spoken_line_rather_than_guessing():
    m = _screens(("anything at all here", 0.0, 3.0))
    assert "V8" not in _tiers(vl.evaluate(_mk(duration_s=10.0), ratio="9:16", manifest=m))


# --- V9 within-shot motion ---------------------------------------------------


def test_v9_moving_shots_are_clean():
    stats = [{"index": 0, "start": 0.0, "end": 5.0, "motion": 0.02}]
    assert "V9" not in _tiers(vl.evaluate(_mk(), ratio="9:16", motion_stats=stats))


def test_v9_the_sub_perceptual_zoom_warns():
    # the shipped 1.08x-over-3s push-in measured 0.0022, roughly a third of the floor
    stats = [
        {"index": 0, "start": 0.0, "end": 4.2, "motion": 0.0022},
        {"index": 1, "start": 4.2, "end": 10.0, "motion": 0.03},
    ]
    findings = vl.evaluate(_mk(duration_s=10.0), ratio="9:16", motion_stats=stats)
    assert any(f.rule == "static_shots" for f in findings)
    assert all(f.severity == vl.WARN for f in findings if f.tier == "V9")


def test_v9_is_independent_of_v6_because_cuts_are_not_motion():
    """The shipped asset cut five times and was still a slideshow — V6 passed it, V9 must not."""
    stats = [{"index": i, "start": float(i), "end": i + 1.0, "motion": 0.001} for i in range(5)]
    findings = vl.evaluate(
        _mk(duration_s=5.0), ratio="9:16", motion_stats=stats, scene_changes=[1.0, 2.0, 3.0, 4.0]
    )
    assert "V9" in _tiers(findings)


def test_v9_is_silent_when_motion_could_not_be_measured():
    assert "V9" not in _tiers(vl.evaluate(_mk(), ratio="9:16", motion_stats=None))
