"""gtm_core.shots_lint — a shot's `production.transition_in` join spec is checked before any spend.

A real shot list declared a 0.4s dissolve and a 0.5s fade and nothing read either, so a misspelt
kind or a duration no join could hold would have surfaced (if at all) at stitch time, after every
render dollar. This rule makes the spec a plan-time contract, and refuses one on the FIRST shot,
which has no join to be cut into — the one shape that is well-formed and still unconsumable.
"""

from __future__ import annotations

import json

from gtm_core import shots_lint as sl


def _shot(n: int, **overrides) -> dict:
    """A well-formed b-roll beat. Every unrelated rule is satisfied in the baseline so these tests
    exercise the transition rule and not an incidental neighbour."""
    shot = {
        "n": n,
        "duration_s": 4,
        "camera": "static",
        "motion_prompt": "the steam curls off the cup and settles",
        "visual": "a quiet kitchen counter at first light",
        "role": "broll",
        "audio_bed": "room tone under a low note",
    }
    shot.update(overrides)
    return shot


def _doc(*shots: dict) -> dict:
    return {
        "source_item": "item-x",
        "total_duration_s": sum(s["duration_s"] for s in shots),
        "style_scaffold": {"look": "editorial, fine grain", "provider_model": "wan2_7"},
        "shots": list(shots),
    }


def _findings(doc: dict) -> list[str]:
    errors, _ = sl.lint_shotlist(doc)
    return [e for e in errors if "transition_in" in e]


def _with(entry: object) -> dict:
    return _doc(_shot(1), _shot(2, production={"transition_in": entry}))


# ── clean ──────────────────────────────────────────────────────────────────────────────────


def test_a_dissolve_a_fade_and_a_push_are_all_clean():
    for kind in ("dissolve", "fade", "push"):
        assert _findings(_with({"kind": kind, "duration_s": 0.4, "why": "months later"})) == []


def test_a_shot_without_a_transition_is_left_alone():
    assert _findings(_doc(_shot(1), _shot(2))) == []


def test_the_ceiling_is_inclusive():
    assert _findings(_with({"kind": "fade", "duration_s": sl.transition.TRANSITION_MAX_S})) == []


# ── refusals ───────────────────────────────────────────────────────────────────────────────


def test_a_kind_that_is_not_a_kind_is_refused():
    assert len(_findings(_with({"kind": "wipe", "duration_s": 0.4}))) == 1


def test_a_hard_cut_written_as_a_kind_is_refused_because_absence_is_the_default():
    assert _findings(_with({"kind": "cut", "duration_s": 0.0}))


def test_a_duration_outside_the_open_interval_is_refused():
    for duration in (0, -0.2, 1.4, "0.4", True, None):
        assert _findings(_with({"kind": "dissolve", "duration_s": duration})), duration


def test_a_missing_duration_is_refused():
    assert len(_findings(_with({"kind": "dissolve"}))) == 1


def test_a_push_with_a_bad_duration_is_refused():
    for duration in (0, -0.2, 1.4, "0.4", True, None):
        assert _findings(_with({"kind": "push", "duration_s": duration})), duration


def test_a_transition_that_is_not_an_object_is_refused():
    assert _findings(_with("dissolve over 0.4s"))


def test_a_transition_on_the_first_shot_is_refused_as_unconsumable():
    doc = _doc(
        _shot(1, production={"transition_in": {"kind": "fade", "duration_s": 0.5}}), _shot(2)
    )
    findings = _findings(doc)
    assert len(findings) == 1 and "first shot" in findings[0]


def test_a_push_on_the_first_shot_is_refused_as_unconsumable():
    doc = _doc(
        _shot(1, production={"transition_in": {"kind": "push", "duration_s": 0.4}}), _shot(2)
    )
    findings = _findings(doc)
    assert len(findings) == 1 and "first shot" in findings[0]


# ── the rule publishes itself ──────────────────────────────────────────────────────────────


def test_the_rule_is_listed_by_rules_with_its_constant(capsys):
    assert sl.main(["--rules"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "_lint_transition" in {rule["name"] for rule in payload["rules"]}
    assert payload["constants"]["transition_max_s"] == sl.transition.TRANSITION_MAX_S
