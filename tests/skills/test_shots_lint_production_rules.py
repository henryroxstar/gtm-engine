"""gtm_core.shots_lint — the three production rules added 2026-08-19.

Each mechanises one of the six defects the operator found in the asset shipped the day before,
and each is checkable for FREE, before any credit is spent:

* ``motion_prompt`` required — all six shots omitted it, so the model got a start frame and a
  camera note with no action and rendered a person sitting still ("it feels boring, it's almost
  entirely just face shots"; V9 found 3/6 motionless).
* VO/duration parity — every shot declared ``duration_s: 5`` while its VO ran 3.20-7.92s, so the
  mouth was animated across a duration the audio never filled ("lip sync wasn't working").
* Presenter quota — 5 of 6 shots were the presenter's face.
"""

from __future__ import annotations

import pytest

from gtm_core import shots_lint as sl


def _shot(**overrides) -> dict:
    shot = {
        "n": 1,
        "duration_s": 5,
        "camera": "static, slow push-in",
        "motion_prompt": "he speaks to camera, articulating each word, setting the phone down",
        "visual": "office",
        "role": "presenter",
        # audio_bed required since 2026-08-28 — see sl._lint_audio_bed. Present in the
        # baseline so the tests below exercise the rule they name, not this one.
        "audio_bed": "room tone under the whole beat",
    }
    shot.update(overrides)
    return shot


def _doc(shots: list[dict], total: float | None = None) -> dict:
    return {
        "source_item": "item-1",
        "total_duration_s": total if total is not None else sum(s["duration_s"] for s in shots),
        "style_scaffold": {"look": "editorial monochrome", "provider_model": "wan2_7"},
        "shots": shots,
    }


# --- motion_prompt -------------------------------------------------------------------------


def test_missing_motion_prompt_is_an_error():
    shot = _shot()
    del shot["motion_prompt"]
    errors, _ = sl.lint_shotlist(_doc([shot]))
    assert any("missing required field `motion_prompt`" in e for e in errors)


def test_empty_motion_prompt_is_an_error():
    errors, _ = sl.lint_shotlist(_doc([_shot(motion_prompt="   ")]))
    assert any("missing required field `motion_prompt`" in e for e in errors)


def test_motion_prompt_restating_the_camera_is_an_error():
    """camera is what the LENS does; motion_prompt is what the SUBJECT does. A restatement
    leaves the subject with nothing to do — which is the defect wearing a different hat."""
    errors, _ = sl.lint_shotlist(
        _doc([_shot(camera="static, slow push-in", motion_prompt="Static, slow push-in.")])
    )
    assert any("restates `camera`" in e for e in errors)


def test_a_thin_motion_prompt_warns_but_does_not_block():
    errors, warnings = sl.lint_shotlist(_doc([_shot(motion_prompt="he speaks")]))
    assert errors == []
    assert any("too thin to direct a render" in w for w in warnings)


# --- VO / duration parity ------------------------------------------------------------------


def test_a_vo_longer_than_its_shot_is_an_error_naming_the_fix():
    # 17 words ≈ 6.14s at the calibrated rate; a 5s shot cannot hold it.
    spoken = (
        "The website could check who you were but it never asked what was acting on your behalf"
    )
    errors, _ = sl.lint_shotlist(_doc([_shot(duration_s=5, spoken=spoken)]))
    hit = [e for e in errors if "shorter than its own spoken line" in e]
    assert hit, errors
    assert "Round up to 7s" in hit[0]


def test_a_shot_matching_its_line_is_clean():
    # 14 words ≈ 5.05s — a 5s shot is parity within tolerance.
    spoken = "A guy asked his assistant to get him into a full gym class today"
    # role=broll: this asserts a CLEAN result, and a speaking PRESENTER shot is now refused
    # outright (no engine is bound to that role). The refusal has its own coverage in
    # tests/media/test_render_engines.py; this test is about VO/duration parity.
    errors, warnings = sl.lint_shotlist(_doc([_shot(duration_s=5, spoken=spoken, role="broll")]))
    assert errors == []
    assert not any("spoken line" in w for w in warnings)


def test_a_shot_far_longer_than_its_line_warns_about_dead_air():
    spoken = "It found a way"  # 4 words ≈ 1.44s
    _, warnings = sl.lint_shotlist(_doc([_shot(duration_s=5, spoken=spoken)]))
    assert any("past its spoken line" in w for w in warnings)


def test_a_silent_shot_is_exempt_from_parity():
    errors, warnings = sl.lint_shotlist(_doc([_shot(duration_s=12, role="broll")]))
    assert not any("spoken line" in m for m in errors + warnings)


def test_a_fractional_duration_warns_because_providers_take_integers():
    _, warnings = sl.lint_shotlist(_doc([_shot(duration_s=5.5, spoken="It found a way somehow")]))
    assert any("fractional" in w for w in warnings)


# --- shot mix ------------------------------------------------------------------------------


def test_an_all_presenter_shot_list_is_an_error_past_the_quota():
    shots = [_shot(n=i) for i in range(1, 7)]
    errors, _ = sl.lint_shotlist(_doc(shots))
    assert any("role=presenter (100%)" in e for e in errors)


def test_the_real_2026_08_18_mix_is_an_error():
    """5 presenter + 1 broll = 83%, over the 60% ceiling."""
    shots = [_shot(n=i) for i in range(1, 6)] + [_shot(n=6, role="broll")]
    errors, _ = sl.lint_shotlist(_doc(shots))
    assert any("5 of 6 shots are role=presenter (83%)" in e for e in errors)


def test_a_balanced_mix_clears_the_quota():
    shots = [
        _shot(n=1),
        _shot(n=2),
        _shot(n=3, role="screen"),
        _shot(n=4, role="broll"),
        _shot(n=5, role="screen"),
        _shot(n=6, role="broll"),
    ]
    errors, warnings = sl.lint_shotlist(_doc(shots))
    assert not any("role=presenter" in e for e in errors)
    assert not any("role=screen" in w for w in warnings)


def test_no_screen_shot_warns_even_when_the_quota_passes():
    """3 presenter + 3 abstract b-roll clears any ratio and is still six variations on a face."""
    shots = [_shot(n=i) for i in range(1, 4)] + [_shot(n=i, role="broll") for i in range(4, 7)]
    errors, warnings = sl.lint_shotlist(_doc(shots))
    assert not any("role=presenter" in e for e in errors)
    assert any("no shot has role=screen" in w for w in warnings)


def test_the_quota_does_not_bind_on_a_short_shot_list():
    """A 2-shot piece cannot be split finely enough for a 60% ceiling to mean anything."""
    errors, warnings = sl.lint_shotlist(_doc([_shot(n=1), _shot(n=2)]))
    assert not any("ceiling is" in e for e in errors)
    assert any("all 2 shots are role=presenter" in w for w in warnings)


@pytest.mark.parametrize("rate_words,expected_s", [(28, 10.1), (14, 5.05)])
def test_the_speech_rate_constant_is_the_measured_one(rate_words, expected_s):
    """2.77 words/sec was measured across six real rendered VO tracks, not assumed. If someone
    retunes it, this fails loudly rather than silently shifting every parity verdict."""
    assert sl.VO_WORDS_PER_SEC == pytest.approx(2.77)
    assert sl._estimated_vo_seconds(" ".join(["w"] * rate_words)) == pytest.approx(
        expected_s, abs=0.05
    )


# --- measured VO parity (2026-08-19) --------------------------------------------------------


def _vo_shot(n: int, spoken: str, duration: float, vo_seconds: float) -> dict:
    return {
        "n": n,
        "duration_s": duration,
        "vo_seconds": vo_seconds,
        "role": "presenter",
        "camera": "static, camera remains still",
        "motion_prompt": "he leans in slightly, one hand lifting off the desk",
        "visual": "medium close-up on a navy field",
        "spoken": spoken,
    }


def test_a_measured_vo_replaces_the_estimate_and_accepts_a_tight_duration():
    """The 17-word line measures 4.56s. The estimate band puts it anywhere from 4.84s to 6.16s,
    so 4s reads as an overrun against the slow end — but the measurement says 4s is right."""
    shot = _vo_shot(
        1,
        "Because the site could check who you were, and it never asked what was acting for you.",
        4,
        4.56,
    )
    errors: list[str] = []
    warnings: list[str] = []
    sl._lint_vo_duration_parity(shot, "shot[1]", errors, warnings)
    assert errors == []
    assert warnings == []


def test_a_measured_vo_catches_the_silent_tail_the_estimate_band_let_through():
    """5s against a measured 4.08s VO is a 0.92s tail — refused by the mux, and now refused here,
    one render earlier and for free."""
    shot = _vo_shot(1, "A guy asked his AI assistant to get him into a full gym class.", 5, 4.08)
    errors: list[str] = []
    warnings: list[str] = []
    sl._lint_vo_duration_parity(shot, "shot[1]", errors, warnings)
    assert len(errors) == 1
    assert "0.92s silent tail" in errors[0]
    assert "duration=4s" in errors[0]


def test_a_measured_vo_catches_an_unfittable_compression():
    """3s against a 4.56s VO needs atempo 1.52 — the cloned voice would be audibly distorted."""
    shot = _vo_shot(5, "Because the site could check who you were and what was acting.", 3, 4.56)
    errors: list[str] = []
    warnings: list[str] = []
    sl._lint_vo_duration_parity(shot, "shot[5]", errors, warnings)
    assert len(errors) == 1
    assert "atempo" in errors[0]


def test_a_vo_that_fits_no_integer_duration_says_so_instead_of_suggesting_one():
    """The 3.60s dead band: 3s needs atempo 1.18, 4s leaves a 0.40s tail. Naming a duration here
    would send the reader back to the one that was just refused."""
    shot = _vo_shot(6, "And it's not just the gym. It's every app your agent can reach.", 4, 3.60)
    errors: list[str] = []
    warnings: list[str] = []
    sl._lint_vo_duration_parity(shot, "shot[6]", errors, warnings)
    assert len(errors) == 1
    assert "no integer duration fits" in errors[0]


def test_the_whole_pre_measurement_shot_list_is_flagged_once_vo_lengths_are_known():
    """The real 2026-08-19 case: a shot list this linter passed with zero findings, checked again
    against the VO that was actually generated from it."""
    lines = [
        ("A guy asked his AI assistant to get him into a full gym class.", 5, 4.08),
        ("And it worked. It cancelled someone else's spot to make room.", 4, 3.28),
        ("But nobody had locked that door. Any member could have done the same thing.", 5, 4.08),
        ("So this wasn't a hack. Nobody broke in. The door was already open.", 5, 4.00),
        ("Because the site could check who you were, and it never asked what was acting.", 7, 4.56),
    ]
    flagged = 0
    for i, (spoken, duration, vo) in enumerate(lines, start=1):
        errors: list[str] = []
        sl._lint_vo_duration_parity(_vo_shot(i, spoken, duration, vo), f"shot[{i}]", errors, [])
        flagged += bool(errors)
    assert flagged == 5, "every over-long shot must be caught once the VO is measured"
