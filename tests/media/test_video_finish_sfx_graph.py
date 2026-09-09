"""gtm_core.video_finish._build_sfx_filtergraph / cue validation — pure, no ffmpeg.

The filtergraph is a value before it is a process, the same property plan() has: every invariant
that can be checked by reading the string is checked here, in milliseconds, and the behavioural
half lives in test_video_finish_sfx.py against real ffmpeg. Both halves are needed — a string
assertion only fails when someone edits the string, and the defects this guards against are the
ones that look right and sound wrong.
"""

from __future__ import annotations

import pytest

from gtm_core import video_finish as vf


def _cues(n: int) -> list[vf.SfxCue]:
    return [vf.SfxCue(path=f"c{i}.wav", at_s=1.0 + i, label=f"c{i}") for i in range(n)]


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_amix_always_carries_normalize_zero(n):
    """amix divides by its input count. With four cues that is 12.04 dB off the base, not 6 —
    silently, and in a direction video_lint's dead-air gate reads as an improvement."""
    graph = vf._build_sfx_filtergraph(_cues(n))
    assert f"amix=inputs={n + 1}:duration=first:dropout_transition=0:normalize=0" in graph
    assert "normalize=1" not in graph


def test_every_branch_is_forced_to_the_uniform_layout():
    graph = vf._build_sfx_filtergraph(_cues(2))
    assert graph.count(f"aresample={vf.AUDIO_SAMPLE_RATE}") == 3  # base + 2 cues


def test_adelay_carries_one_value_per_channel():
    """A bare `adelay=4200` delays channel 1 only and leaves the right channel at zero, so the
    cue arrives as a 4.2-second stereo smear rather than a click."""
    graph = vf._build_sfx_filtergraph([vf.SfxCue(path="c.wav", at_s=4.2)])
    assert "adelay=4200|4200" in graph


def test_asetpts_follows_every_atrim():
    """atrim preserves source PTS, so without the reset a cue trimmed at 0.120 already starts at
    t=0.120 and adelay=1500 lands it at 1.620s — wrong by exactly the trim, and silent."""
    graph = vf._build_sfx_filtergraph([vf.SfxCue(path="c.wav", at_s=1.5, trim_s=0.12)])
    assert "atrim=start=0.120000,asetpts=PTS-STARTPTS" in graph


def test_gain_is_applied_before_the_delay_and_after_the_format():
    """volume is a linear scalar, so the cue's peak moves by exactly gain_db — the identity the
    whole verification arithmetic rests on. It only holds once resampling has happened."""
    graph = vf._build_sfx_filtergraph([vf.SfxCue(path="c.wav", at_s=1.0, gain_db=-8.0)])
    branch = graph.split(";")[1]
    assert branch.index("aformat") < branch.index("volume=-8.0dB") < branch.index("adelay=")


def test_tap_base_splits_rather_than_reusing_a_consumed_label():
    """A filtergraph label may be consumed once. The verification pass needs the aligned base as
    well as the mix, so the base branch splits instead of being referenced twice."""
    graph = vf._build_sfx_filtergraph(_cues(1), tap_base=True)
    assert "asplit=2[base][baseout]" in graph
    assert graph.count("[baseout]") == 1


def test_duration_is_bounded_by_the_base():
    assert "duration=first" in vf._build_sfx_filtergraph(_cues(1))


# ── validation ───────────────────────────────────────────────────────────────────────────


def test_no_cues_is_refused():
    with pytest.raises(vf.SfxError, match="at least one cue"):
        vf._validate_sfx_cues([], base_duration_s=10.0)


def test_a_cue_past_the_clip_end_is_refused():
    with pytest.raises(vf.SfxError, match="past the clip"):
        vf._validate_sfx_cues([vf.SfxCue(path="c.wav", at_s=10.0)], base_duration_s=10.0)


def test_a_negative_time_is_refused():
    with pytest.raises(vf.SfxError, match="before the clip starts"):
        vf._validate_sfx_cues([vf.SfxCue(path="c.wav", at_s=-0.1)], base_duration_s=10.0)


def test_duplicate_labels_are_refused():
    with pytest.raises(vf.SfxError, match="duplicate cue label"):
        vf._validate_sfx_cues(
            [
                vf.SfxCue(path="a.wav", at_s=1.0, label="tick"),
                vf.SfxCue(path="b.wav", at_s=2.0, label="tick"),
            ],
            base_duration_s=10.0,
        )


def test_sfx_error_is_a_value_error_so_the_cli_maps_it_to_exit_4():
    assert issubclass(vf.SfxError, vf.PlanError)
    assert issubclass(vf.SfxError, ValueError)


# ── the control window is measured or absent, never fabricated ───────────────────────────


def test_a_control_window_avoids_every_cue():
    cues = [vf.SfxCue(path="a.wav", at_s=2.0), vf.SfxCue(path="b.wav", at_s=5.0)]
    start, span = vf._control_window(cues, base_duration_s=8.0, span_s=0.2)
    assert all(not (start < c.at_s + 0.25 and c.at_s - 0.05 < start + span) for c in cues)


def test_a_mix_too_dense_for_a_control_window_returns_none_rather_than_overlapping_one():
    """A control window overlapping a cue would read as a high noise floor and turn every real
    cue into a false 'unverifiable'. Reporting no window is honest; guessing one is not."""
    cues = [vf.SfxCue(path=f"{i}.wav", at_s=0.05 * i) for i in range(1, 12)]
    assert vf._control_window(cues, base_duration_s=0.7, span_s=0.2) is None


# ── regressions found by reviewing this feature, 2026-08-31 ──────────────────────────────


def test_no_verify_never_reports_verified():
    """`verified` used to be `not verify` when there were no measurements, so a mix run with
    --no-verify came back `{"verified": true, "cues": []}` — the exact false assurance the
    verification pass exists to prevent, reachable through the feature's own flag."""
    import inspect

    src = inspect.getsource(vf.mix_sfx_cues)
    assert "not verify," not in src, (
        "verified is derived from the flag again, not from measurements"
    )
    assert "verified=bool(measurements) and all(" in src


def test_shot_ids_validator_names_shots_the_way_the_loop_does():
    """An id-less shot was `shot-00` to the burn loop and `""` to the --shot-ids check, so
    filtering for it was refused as unknown."""
    import inspect

    src = inspect.getsource(vf.burn_captions)
    loop = 'shot_id = str(shot.get("id") or f"shot-{i:02d}")'
    check = 'f"shot-{i:02d}"'
    assert loop in src
    assert src.count(check) == 2, "the two shot-id fallbacks have diverged again"
