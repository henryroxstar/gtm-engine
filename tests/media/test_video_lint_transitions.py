"""C6 — transition density (V12), the cadence floor (V13), and the hoisted thresholds.

PRD test ids C6-T1/T2 (cuts and transitions are told apart on real assets), C6-T3 (the new tiers
are WARN by construction), C6-T4 (V6 and V13 measure different things), C6-T5 (nothing that
passed at ERROR now fails) and C6-T6 (the hoist is real — patching the threshold module flips the
verdict, which is only true because `cadence.py` reads by attribute rather than by from-import).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from gtm_core.video_lint import thresholds
from gtm_core.video_lint.cadence import cadence_findings
from gtm_core.video_lint.measure import classify_boundaries, measure_transitions
from gtm_core.video_lint.model import ERROR, WARN, Tier
from gtm_core.video_lint.probe import Probe
from gtm_core.video_lint.tiers import CANDIDATES, SHIPPED

FFMPEG = shutil.which("ffmpeg")


def _probe(duration_s: float) -> Probe:
    return Probe(
        width=1080,
        height=1920,
        fps=30.0,
        duration_s=duration_s,
        bit_rate=8_000_000,
        has_audio=True,
    )


def _rules(findings) -> list[str]:
    return [f.rule for f in findings]


def _static(duration_s: float) -> list[dict]:
    """One motionless shot spanning the whole asset — every gap is a genuine hold."""
    return [{"index": 0, "start": 0.0, "end": duration_s, "motion": 0.0}]


def _moving(duration_s: float) -> list[dict]:
    """One continuously moving shot — nothing here is a hold, whatever the cut list says."""
    return [{"index": 0, "start": 0.0, "end": duration_s, "motion": 0.03}]


# ── C6-T6: the hoist is real ──────────────────────────────────────────────────────────────────


def test_patching_the_threshold_module_flips_the_verdict(monkeypatch):
    """C6-T6. The whole reason these literals moved out of `evaluate.py`.

    `cadence.py` reads `thresholds.MAX_SECONDS_WITHOUT_CHANGE` BY ATTRIBUTE. A from-import would
    bind a copy at module load, so this monkeypatch would change nothing and the test would pass
    while measuring nothing at all — the silent-mock class of defect, asserted against here.
    """
    p, changes = _probe(20.0), [0.0, 4.0, 8.0, 12.0, 16.0]  # longest hold 4s

    assert "longest_hold_exceeds_cadence" not in _rules(
        cadence_findings(p, scene_changes=changes, motion_stats=_static(20.0))
    ), "a 4s hold should clear the shipped 5s ceiling"

    monkeypatch.setattr(thresholds, "MAX_SECONDS_WITHOUT_CHANGE", 3.0)
    assert "longest_hold_exceeds_cadence" in _rules(
        cadence_findings(p, scene_changes=changes, motion_stats=_static(20.0))
    ), "the constant was read from a bound copy, so patching it changed nothing"


def test_the_scene_change_threshold_reaches_the_ffmpeg_argument_list(monkeypatch):
    """The same property for the one constant that lived inside a filter STRING — the least
    patchable place a number can live."""
    # measure_cuts_and_motion makes TWO ffmpeg passes; recording only the last would have
    # examined the wrong one and reported a false failure.
    calls: list[list[str]] = []

    class _Result:
        stdout = ""
        stderr = ""

    def _fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _Result()

    monkeypatch.setattr(thresholds, "SCENE_CHANGE_THRESHOLD", 0.77)
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/ffmpeg")

    from gtm_core.video_lint.measure import measure_cuts_and_motion

    measure_cuts_and_motion(__import__("pathlib").Path("x.mp4"))
    assert calls, "no ffmpeg pass ran at all"
    assert any("0.77" in str(a) for call in calls for a in call), (
        f"the patched scene threshold never reached ffmpeg: {calls}"
    )


# ── C6-T3: the new tiers are WARN by construction ─────────────────────────────────────────────


def test_an_error_severity_tier_with_no_evidence_is_refused():
    """C6-T3. The registry's own guard, exercised on the shape C6 would have violated."""
    with pytest.raises(Exception):
        Tier("V12", "a claim with nothing behind it", ERROR, evidence=())


@pytest.mark.parametrize("tier_id", ["V12", "V13"])
def test_the_new_cadence_tiers_ship_as_warn_with_evidence(tier_id):
    tier = next(t for t in CANDIDATES if t.id == tier_id)
    assert tier.severity == WARN, f"{tier_id} shipped at ERROR on one corpus"
    assert tier.evidence, f"{tier_id} makes a claim with nothing behind it"


def test_the_new_tiers_are_candidates_and_not_shipped():
    """A candidate is advisory. Promoting one is a separate, evidenced decision."""
    assert not [t for t in SHIPPED if t.id in {"V12", "V13"}]


# ── C6-T4: V6 and V13 measure different things ────────────────────────────────────────────────


def test_a_healthy_average_can_still_hide_one_long_hold():
    """C6-T4. The exact case an average cannot see, and the reason V13 is its own tier."""
    # 40s, 20 changes: a 2s average. But one 11s block in the middle.
    changes = [float(i) for i in range(10)] + [21.0 + i for i in range(10)]
    findings = cadence_findings(_probe(40.0), scene_changes=changes, motion_stats=_static(40.0))
    rules = _rules(findings)
    assert "longest_hold_exceeds_cadence" in rules, "the 11s hold went unreported"
    assert "scene_changes_too_sparse" not in rules, (
        "V6 is not the tier that catches this — if it were, V13 would be redundant"
    )


def test_a_sparse_asset_trips_v6_and_v13_for_different_reasons():
    """Both can fire on one asset; they are not alternatives."""
    rules = _rules(cadence_findings(_probe(60.0), scene_changes=[30.0], motion_stats=_static(60.0)))
    assert "scene_changes_too_sparse" in rules
    assert "longest_hold_exceeds_cadence" in rules


def test_the_head_gap_counts_even_though_no_change_has_happened_yet():
    """A static OPENING is the most expensive place of all to lose someone."""
    rules = _rules(
        cadence_findings(
            _probe(20.0), scene_changes=[9.0, 11.0, 13.0, 15.0, 17.0], motion_stats=_static(20.0)
        )
    )
    assert "longest_hold_exceeds_cadence" in rules, "the 9s opening hold was not counted"


def test_the_tail_gap_counts_too():
    rules = _rules(
        cadence_findings(
            _probe(20.0), scene_changes=[1.0, 2.0, 3.0, 4.0], motion_stats=_static(20.0)
        )
    )
    assert "longest_hold_exceeds_cadence" in rules, "the 16s tail hold was not counted"


def test_a_moving_single_shot_is_not_a_hold_however_long_it_runs_without_a_cut():
    """Review finding: an 8s push-in with no cut fired as "nothing changing on screen".

    A hold is NO CUT *and* NO MOTION. The registry text always promised a single moving shot
    does not fail this tier; the first implementation measured cuts alone and broke that promise
    on the commonest single-shot input there is.
    """
    rules = _rules(cadence_findings(_probe(8.0), scene_changes=[], motion_stats=_moving(8.0)))
    assert "longest_hold_exceeds_cadence" not in rules, "a moving shot was called a hold"


def test_the_same_shot_with_no_motion_IS_a_hold():
    """Positive control — same cut list, only the motion differs."""
    rules = _rules(cadence_findings(_probe(8.0), scene_changes=[], motion_stats=_static(8.0)))
    assert "longest_hold_exceeds_cadence" in rules


def test_v13_is_skipped_rather_than_reported_when_motion_was_never_measured():
    """Unmeasured is a skip — never "fine", never a finding. `--fast` lands here."""
    rules = _rules(cadence_findings(_probe(20.0), scene_changes=[2.0], motion_stats=None))
    assert "longest_hold_exceeds_cadence" not in rules


def test_a_hold_is_judged_by_the_shots_that_actually_cover_it():
    """A static 0-9s shot then a moving one: only the static gap is a hold."""
    stats = [
        {"index": 0, "start": 0.0, "end": 9.0, "motion": 0.001},
        {"index": 1, "start": 9.0, "end": 20.0, "motion": 0.04},
    ]
    out = cadence_findings(_probe(20.0), scene_changes=[9.0], motion_stats=stats)
    hold = [f for f in out if f.rule == "longest_hold_exceeds_cadence"]
    assert len(hold) == 1 and "starting at 0.0s" in hold[0].excerpt, (
        "the 11s MOVING tail was reported instead of the 9s static head"
    )


def test_the_shared_delta_pass_feeds_transitions_without_a_second_decode(tmp_path, monkeypatch):
    """Review finding: measure_transitions ran its own byte-identical tblend pass. With deltas
    supplied it must not touch ffmpeg at all."""
    import shutil

    from gtm_core.video_lint.measure import measure_transitions

    monkeypatch.setattr(
        shutil, "which", lambda _n: (_ for _ in ()).throw(AssertionError("ffmpeg was invoked"))
    )
    diffs = [(i / 30, 0.01) for i in range(20)]
    for i in range(8, 14):
        diffs[i] = (i / 30, 0.25)
    out = measure_transitions(tmp_path / "never-read.mp4", deltas=diffs)
    assert out and out[0]["frames"] == 6


def test_an_evenly_cut_asset_trips_neither():
    """Positive control across both tiers."""
    changes = [float(i) * 3 for i in range(1, 10)]
    assert not _rules(cadence_findings(_probe(30.0), scene_changes=changes))


def test_unmeasured_scene_changes_report_nothing_rather_than_clean():
    """A clean verdict nothing measured is the one answer this tool must never give."""
    assert cadence_findings(_probe(60.0), scene_changes=None) == []


# ── V12 ───────────────────────────────────────────────────────────────────────────────────────


def test_a_transition_heavy_edit_warns():
    transitions = [{"start": i * 2.0, "end": i * 2.0 + 0.5, "frames": 15} for i in range(8)]
    rules = _rules(
        cadence_findings(
            _probe(30.0), scene_changes=[float(i) * 2 for i in range(8)], transitions=transitions
        )
    )
    assert "transition_density_high" in rules


def test_a_few_transitions_are_clean():
    """Positive control: a dissolve is a legitimate device, and this tier is about DENSITY."""
    rules = _rules(
        cadence_findings(
            _probe(60.0),
            scene_changes=[10.0, 20.0, 30.0, 40.0, 50.0],
            transitions=[{"start": 10.0, "end": 10.5, "frames": 15}],
        )
    )
    assert "transition_density_high" not in rules


def test_unmeasured_transitions_skip_v12_rather_than_reporting_zero():
    """`--fast` and a missing ffmpeg both land here. Zero transitions is a claim; None is not."""
    rules = _rules(cadence_findings(_probe(10.0), scene_changes=[1.0, 3.0, 5.0], transitions=None))
    assert "transition_density_high" not in rules


# ── C6-T1 / C6-T2: cuts and transitions on real pixels ────────────────────────────────────────


def _lavfi(tmp_path, name: str, filtergraph: str, seconds: float) -> object:
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-filter_complex",
            filtergraph,
            "-map",
            "[v]",
            "-t",
            str(seconds),
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_hard_cuts_are_not_counted_as_transitions(tmp_path):
    """C6-T1. Three hard cuts between solid colours: boundaries exist, blends do not."""
    graph = (
        "color=c=red:s=320x240:d=1[a];color=c=green:s=320x240:d=1[b];"
        "color=c=blue:s=320x240:d=1[c];[a][b][c]concat=n=3:v=1:a=0[v]"
    )
    asset = _lavfi(tmp_path, "cuts.mp4", graph, 3.0)
    transitions = measure_transitions(asset)
    assert transitions is not None, "ffmpeg is present, so this must measure"
    assert transitions == [], f"hard cuts were read as blends: {transitions}"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")
def test_a_crossfade_is_counted_as_a_transition(tmp_path):
    """C6-T2. The same colours joined with a half-second xfade instead of a cut."""
    graph = (
        "color=c=red:s=320x240:d=2[a];color=c=blue:s=320x240:d=2[b];"
        "[a][b]xfade=transition=fade:duration=0.5:offset=1.0[v]"
    )
    asset = _lavfi(tmp_path, "xfade.mp4", graph, 3.0)
    transitions = measure_transitions(asset)
    assert transitions, "a half-second crossfade was not detected as a blend"
    assert transitions[0]["frames"] >= thresholds.TRANSITION_MIN_BLEND_FRAMES


# ── the pure classifier, on hand-built arrays ─────────────────────────────────────────────────


def test_the_classifier_ignores_a_single_frame_spike():
    diffs = [(i / 30, 0.01) for i in range(20)]
    diffs[10] = (10 / 30, 0.9)
    assert classify_boundaries(diffs, min_blend_frames=3) == []


def test_the_classifier_reports_a_run_of_elevated_frames():
    diffs = [(i / 30, 0.01) for i in range(20)]
    for i in range(8, 14):
        diffs[i] = (i / 30, 0.25)
    found = classify_boundaries(diffs, min_blend_frames=3)
    assert len(found) == 1 and found[0]["frames"] == 6


def test_a_run_shorter_than_the_blend_floor_is_a_cut_not_a_transition():
    """The boundary case the floor exists for: two frames is a fast cut, not a dissolve."""
    diffs = [(i / 30, 0.01) for i in range(20)]
    for i in range(8, 10):
        diffs[i] = (i / 30, 0.25)
    assert classify_boundaries(diffs, min_blend_frames=3) == []


def test_a_perfectly_static_clip_reports_no_transitions():
    """Median 0 and MAD 0 would make every frame "elevated" without the absolute floor."""
    assert classify_boundaries([(i / 30, 0.0) for i in range(30)], min_blend_frames=3) == []


def test_too_few_frames_to_judge_reports_nothing():
    assert classify_boundaries([(0.0, 0.5), (0.03, 0.5)], min_blend_frames=3) == []
