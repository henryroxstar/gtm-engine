"""V10 ``audio_floor_only`` — level with no events.

The defect this closes: a 30s film shipped past a green V10 whose only audio was a synthesized
pink-noise room-tone floor, laid so that the dead-air rules would pass. ``silencedetect`` cannot
tell "audio present" from "a flat floor with no events", and the ebur128 SUMMARY numbers cannot
either once loudnorm has run — so the rule reads the 100ms momentary-loudness series from the
same pass and asks whether the loudness ever moves abruptly.

Three layers are asserted: the pure rule against hand-written context, the pure series maths
against hand-built series (a fade is a ramp and must not count; a step must), and the measured
path against lavfi-synthesized audio so the whole chain — ffmpeg log → parser → statistic →
verdict — is exercised on a signal whose shape is known. The calibration numbers from the real
assets are pinned as a table of NUMBERS only; no tenant asset is referenced.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gtm_core import video_lint as vl
from gtm_core.video_lint import measure, thresholds
from gtm_core.video_lint.measure import (
    ebur128_frames,
    measure_audio,
    momentary_dynamics,
    parse_audio_log,
)

FFMPEG = shutil.which("ffmpeg")


def _probe(**over) -> vl.Probe:
    base = {
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration_s": 30.0,
        "bit_rate": 8_000_000,
        "has_audio": True,
        "pix_fmt": "yuv420p",
    }
    return vl.Probe(**{**base, **over})


def _v10(findings) -> list[str]:
    return [f.rule for f in findings if f.tier == "V10"]


def _ctx(abruptness: float, events: float, **over) -> dict:
    """A measured context with NO dead air — the floor-only shape: level, no events."""
    base = {
        "silent_runs": [],
        "silent_total_s": 0.0,
        "silent_fraction": 0.0,
        "integrated_lufs": -14.0,
        "true_peak_dbfs": -0.3,
        "loudness_range_lu": 4.5,
        "crest_db": 13.7,
        "loudness_abruptness_lu": abruptness,
        "loudness_event_fraction": events,
        "has_voice": True,
    }
    return {**base, **over}


# ── the pure rule ──────────────────────────────────────────────────────────────────────────


def test_a_flat_floor_with_no_events_is_an_error():
    found = vl.evaluate(_probe(), ratio="9:16", audio_context=_ctx(0.5, 0.03))
    v10 = [f for f in found if f.tier == "V10"]
    assert [f.rule for f in v10] == ["audio_floor_only"]
    assert v10[0].severity == vl.ERROR


def test_the_excerpt_prints_the_measured_numbers_not_only_the_verdict():
    f = next(
        f
        for f in vl.evaluate(_probe(), ratio="9:16", audio_context=_ctx(0.58, 0.031))
        if f.rule == "audio_floor_only"
    )
    assert "0.58 LU" in f.excerpt
    assert "3.1%" in f.excerpt
    assert "-14.0 LUFS" in f.excerpt
    assert "LRA 4.5 LU" in f.excerpt and "crest 13.7 dB" in f.excerpt
    assert "gtm_core.audio_plan" in f.fix


def test_a_soundtrack_with_events_is_clean():
    assert _v10(vl.evaluate(_probe(), ratio="9:16", audio_context=_ctx(3.4, 0.36))) == []


def test_both_numbers_must_sit_under_their_ceiling_to_fire():
    """Either one over its ceiling is evidence of events; the rule needs both to be absent."""
    assert _v10(vl.evaluate(_probe(), ratio="9:16", audio_context=_ctx(0.5, 0.30))) == []
    assert _v10(vl.evaluate(_probe(), ratio="9:16", audio_context=_ctx(3.0, 0.02))) == []


def test_exactly_at_a_ceiling_does_not_fire():
    ctx = _ctx(
        thresholds.AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU,
        thresholds.AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION,
    )
    assert _v10(vl.evaluate(_probe(), ratio="9:16", audio_context=ctx)) == []


def test_a_short_asset_may_legitimately_be_one_held_bed():
    short = thresholds.AUDIO_FLOOR_ONLY_MIN_DURATION_S - 0.1
    assert (
        _v10(vl.evaluate(_probe(duration_s=short), ratio="9:16", audio_context=_ctx(0.2, 0.0)))
        == []
    )


def test_an_unmeasured_series_is_a_skip_never_a_verdict():
    """`--fast`, or an ebur128 build that prints no per-frame lines, lands here."""
    ctx = _ctx(0.2, 0.0)
    del ctx["loudness_abruptness_lu"]
    del ctx["loudness_event_fraction"]
    assert _v10(vl.evaluate(_probe(), ratio="9:16", audio_context=ctx)) == []


def test_no_level_at_all_belongs_to_the_dead_air_rules_not_this_one():
    """A digitally silent track has abruptness 0 too; dead air already owns that shape."""
    ctx = _ctx(0.0, 0.0, integrated_lufs=-70.0, silent_fraction=1.0, has_voice=False)
    ctx["silent_runs"] = [{"start": 0.0, "end": 30.0, "duration": 30.0}]
    rules = _v10(vl.evaluate(_probe(), ratio="9:16", audio_context=ctx))
    assert "audio_floor_only" not in rules
    assert "dead_air_fraction" in rules


# ── calibration, pinned as numbers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("abruptness_lu", "event_fraction", "must_fire"),
    [
        # a shipped 30s film whose whole soundtrack was a synthesized room-tone floor
        (0.57, 0.031, True),
        # a module-synthesized 10s room tone (gtm_core.video_finish.audio.room_tone)
        (0.30, 0.000, True),
        # two shipped voice-led films, one 17s and one 33s
        (2.33, 0.361, False),
        (2.28, 0.361, False),
    ],
)
def test_calibration_2026_09_11(abruptness_lu, event_fraction, must_fire):
    """The four real measurements the ceilings were chosen against. Every row keeps ~1.9x or
    more margin from its ceiling; if a recalibration narrows that, this table is where it
    shows."""
    fired = "audio_floor_only" in _v10(
        vl.evaluate(_probe(), ratio="9:16", audio_context=_ctx(abruptness_lu, event_fraction))
    )
    assert fired is must_fire


def test_the_ceilings_keep_margin_on_both_sides_of_the_calibration():
    """The property the table above is meant to hold, stated once as a number."""
    floor_max, soundtrack_min = 0.57, 2.28
    ceiling = thresholds.AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU
    assert ceiling / floor_max >= 1.8 and soundtrack_min / ceiling >= 1.8


def test_a_hard_gap_saturates_rather_than_dominating_the_mean():
    """Four corners of a digital-silence gap are ~55 LU each unsaturated — 3.3 LU of mean over
    68 frames, which would carry a floor with two short gaps past any ceiling a soundtrack
    clears. Saturated, they are four events and ~0.5 LU."""
    with_gap = [-14.0] * 30 + [None] * 10 + [-14.0] * 30
    stats = momentary_dynamics(_frames(with_gap))
    n_diffs = stats["momentary_frames"] - 2  # the 0.4s warm-up trim drops four frames
    assert stats["loudness_abruptness_lu"] == round(
        4 * thresholds.AUDIO_FLOOR_ONLY_EVENT_SATURATION_LU / n_diffs, 3
    )


def test_the_summary_numbers_that_were_first_proposed_would_not_have_discriminated():
    """The measured LRA / crest of the floor-only film sat INSIDE the range of the two clean
    films (4.7 LU / 13.7 dB against 2.7-4.8 LU / 14.5-14.6 dB). Pinned so nobody re-proposes a
    threshold on them without re-measuring: a check that cannot discriminate is not a check."""
    floor_lra, floor_crest = 4.7, 13.7
    clean = [(2.7, 14.5), (4.8, 14.6)]
    assert min(lra for lra, _ in clean) <= floor_lra <= max(lra for lra, _ in clean)
    assert max(crest for _, crest in clean) - floor_crest < 1.0


# ── the series maths ───────────────────────────────────────────────────────────────────────


def _frames(values: list[float | None]) -> list[dict]:
    return [{"t": round(0.1 * i, 1), "m": v, "ftpk": -3.0} for i, v in enumerate(values)]


def test_a_linear_fade_is_a_ramp_and_scores_nothing():
    """Second difference, not first: a 30 LU fade over 3s moves 1 LU every frame and would
    look like 30 events to a first-difference count. It is one gesture, not thirty."""
    ramp = [-50.0 + i for i in range(31)] + [-20.0] * 60
    stats = momentary_dynamics(_frames(ramp))
    assert stats is not None
    assert stats["loudness_abruptness_lu"] < 0.1
    assert stats["loudness_event_fraction"] < 0.05


def test_a_step_train_scores_as_events():
    steps = [(-14.0 if (i // 5) % 2 else -30.0) for i in range(100)]
    stats = momentary_dynamics(_frames(steps))
    assert stats["loudness_abruptness_lu"] > thresholds.AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU
    assert stats["loudness_event_fraction"] > thresholds.AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION


def test_the_warm_up_frames_and_the_nothing_floor_are_not_events():
    """ebur128 reports -120 until its 400ms window fills, and -inf on digital silence. Neither
    is an onset: the first is measurement warm-up, the second is a gap, one step down."""
    with_gap = [None, None, None, None] + [-14.0] * 30 + [None] * 10 + [-14.0] * 30
    stats = momentary_dynamics(_frames(with_gap))
    # two steps (into and out of the gap), each a corner pair in the second difference: four
    # event frames over ~70 — under the floor ceiling, and the clip at -70 is what keeps them
    # a step rather than a hundred-LU spike that would dominate the mean.
    assert stats["loudness_event_fraction"] == round(4 / 68, 4)
    assert stats["loudness_event_fraction"] < thresholds.AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION
    assert stats["loudness_abruptness_lu"] < thresholds.AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU


def test_too_few_frames_to_difference_is_none():
    assert momentary_dynamics(_frames([-14.0, -14.0])) is None
    assert momentary_dynamics([]) is None


def test_the_event_step_is_read_by_attribute_so_a_patch_reaches_it(monkeypatch):
    steps = [(-14.0 if (i // 5) % 2 else -17.0) for i in range(100)]  # 3 LU steps
    before = momentary_dynamics(_frames(steps))["loudness_event_fraction"]
    monkeypatch.setattr(thresholds, "AUDIO_FLOOR_ONLY_EVENT_STEP_LU", 10.0)
    after = momentary_dynamics(_frames(steps))["loudness_event_fraction"]
    assert before > 0.0 and after == 0.0


# ── the log parser ─────────────────────────────────────────────────────────────────────────

_LOG = """\
[Parsed_ebur128_1 @ 0x0] t: 0.1        TARGET:-23 LUFS    M:-120.7 S:-120.7     I: -70.0 LUFS     LRA:   0.0 LU  FTPK:  -inf  -inf dBFS  TPK:  -inf  -inf dBFS
[Parsed_ebur128_1 @ 0x0] t: 0.4        TARGET:-23 LUFS    M: -14.2 S:-120.7     I: -14.2 LUFS     LRA:   0.0 LU  FTPK:  -3.1  -3.4 dBFS  TPK:  -3.1  -3.4 dBFS
[Parsed_ebur128_1 @ 0x0] t: 0.5        TARGET:-23 LUFS    M: -14.0 S:-120.7     I: -14.1 LUFS     LRA:   0.0 LU  FTPK:  -2.9  -3.0 dBFS  TPK:  -2.9  -3.0 dBFS
[Parsed_ebur128_1 @ 0x0] t: 0.6        TARGET:-23 LUFS    M: -14.1 S:-120.7     I: -14.1 LUFS     LRA:   0.0 LU  FTPK:  -3.0  -3.2 dBFS  TPK:  -2.9  -3.0 dBFS
[Parsed_ebur128_1 @ 0x0] Summary:

  Integrated loudness:
    I:         -13.9 LUFS
    Threshold: -24.1 LUFS

  Loudness range:
    LRA:         4.7 LU
    Threshold: -34.1 LUFS
    LRA low:   -16.0 LUFS
    LRA high:  -11.3 LUFS

  True peak:
    Peak:       -0.2 dBFS
"""


def test_the_parser_reads_lra_and_derives_crest_from_the_summary_block():
    out = parse_audio_log(_LOG, duration_s=30.0)
    assert out["integrated_lufs"] == -13.9
    assert out["true_peak_dbfs"] == -0.2
    assert out["loudness_range_lu"] == 4.7, "LRA low/high must not shadow the LRA line"
    assert out["crest_db"] == 13.7


def test_the_parser_reads_the_per_frame_series_from_the_same_log():
    frames = ebur128_frames(_LOG)
    assert [f["t"] for f in frames] == [0.1, 0.4, 0.5, 0.6]
    assert frames[0]["m"] == -120.7 and frames[0]["ftpk"] is None  # -inf → None
    assert frames[1]["m"] == -14.2 and frames[1]["ftpk"] == -3.1  # max over channels


def test_measured_keys_reach_the_audio_context():
    out = parse_audio_log(_LOG, duration_s=30.0)
    assert {"loudness_abruptness_lu", "loudness_event_fraction", "momentary_frames"} <= set(out)


# ── the whole chain, on synthesized signals ────────────────────────────────────────────────


def _lavfi_audio(tmp_path: Path, name: str, source: str, *, seconds: float, af: str = "") -> Path:
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            str(seconds),
            *(["-af", af] if af else []),
            "-ac",
            "2",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_a_synthesized_floor_measures_as_a_floor(tmp_path):
    """The exact recipe the shipped floor was made from: low-passed pink noise."""
    clip = _lavfi_audio(
        tmp_path,
        "floor.wav",
        "anoisesrc=color=pink:sample_rate=48000:amplitude=0.09:seed=7",
        seconds=12.0,
        af="lowpass=f=340,highpass=f=35",
    )
    ctx = measure_audio(clip, duration_s=12.0)
    assert ctx["loudness_abruptness_lu"] < thresholds.AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU
    assert ctx["loudness_event_fraction"] < thresholds.AUDIO_FLOOR_ONLY_MAX_EVENT_FRACTION
    assert "audio_floor_only" in _v10(
        vl.evaluate(_probe(duration_s=12.0), ratio="9:16", audio_context=ctx)
    )


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_a_floor_that_fades_in_and_out_is_still_a_floor(tmp_path):
    """Fades are the way a substitute floor is dressed up; a first-difference measure would
    have counted them as events and let the asset through."""
    clip = _lavfi_audio(
        tmp_path,
        "faded-floor.wav",
        "anoisesrc=color=pink:sample_rate=48000:amplitude=0.09:seed=7",
        seconds=12.0,
        af="lowpass=f=340,afade=t=in:d=2,afade=t=out:st=10:d=2",
    )
    ctx = measure_audio(clip, duration_s=12.0)
    assert "audio_floor_only" in _v10(
        vl.evaluate(_probe(duration_s=12.0), ratio="9:16", audio_context=ctx)
    ), ctx


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_a_track_with_events_does_not_fire(tmp_path):
    """Tone bursts — the crudest possible cue track. Steps in loudness every second."""
    clip = _lavfi_audio(
        tmp_path,
        "bursts.wav",
        "aevalsrc=0.4*sin(2*PI*440*t)*gt(mod(t\\,1)\\,0.55):s=48000",
        seconds=12.0,
    )
    ctx = measure_audio(clip, duration_s=12.0)
    assert ctx["loudness_abruptness_lu"] > thresholds.AUDIO_FLOOR_ONLY_MAX_ABRUPTNESS_LU
    assert "audio_floor_only" not in _v10(
        vl.evaluate(_probe(duration_s=12.0), ratio="9:16", audio_context=ctx)
    ), ctx


def test_the_measurement_survives_a_log_with_no_frame_lines(monkeypatch):
    """An ffmpeg that prints only the summary must degrade to the summary keys, not crash."""
    monkeypatch.setattr(measure, "_audio_pass_log", lambda _p: _LOG.split("Summary:")[1])
    out = measure_audio(Path("x.mp4"), duration_s=30.0)
    assert out["loudness_range_lu"] == 4.7
    assert "loudness_abruptness_lu" not in out
