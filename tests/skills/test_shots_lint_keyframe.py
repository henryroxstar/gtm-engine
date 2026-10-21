"""C10 — keyframe shot-list rules (`gtm_core.shots_lint.keyframe`).

PRD test id C10-T3: an `end_frame` is legal only on a b-roll shot, refused everywhere else. Plus
the terse-register WARN that rides with it, and its negative controls — the rule that says a
keyframe shot's motion prompt stays short is the exact inverse of the thin-motion_prompt ERROR
this linter raises everywhere else, so it must not leak onto shots that carry no end frame.

Severity is asserted by LIST MEMBERSHIP (a finding is a bare string; which list it lands in is the
whole severity model), and every refusal is paired with the case that must stay clean.
"""

from __future__ import annotations

import pytest

from gtm_core.shots_lint import KEYFRAME_MOTION_MAX_WORDS, lint_shotlist
from gtm_core.shots_lint.keyframe import _lint_end_frame_role, _lint_keyframe_motion_prompt


def _doc(**shot_overrides) -> dict:
    shot = {
        "camera": "locked-off wide",
        "visual": "a machined brass dial on a workbench",
        "motion_prompt": "the dial turns a quarter clockwise",
        "duration_s": 4.0,
        "role": "broll",
    }
    shot.update(shot_overrides)
    return {
        "source_item": "item-1",
        "total_duration_s": 4.0,
        "style_scaffold": {"look": "clean studio", "provider_model": "wan2_7"},
        "shots": [shot],
    }


def _findings(**shot_overrides) -> tuple[list[str], list[str]]:
    return lint_shotlist(_doc(**shot_overrides))


# ── C10-T3: the role gate ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["presenter", "screen"])
def test_an_end_frame_on_a_non_broll_role_is_an_error(role):
    """Neither lane resolves an engine that takes a last frame, so the request cannot be filled."""
    errors, warnings = _findings(role=role, end_frame="refs/end.png")
    hits = [e for e in errors if "end_frame" in e]
    assert hits, f"role {role!r} accepted an end_frame it has no engine for"
    assert not [w for w in warnings if "legal only on role" in w], (
        "the role gate is an ERROR — it refuses a render that cannot be served, not a style note"
    )


def test_an_end_frame_on_a_broll_shot_is_accepted():
    """Positive control for the role gate: the one lane that does resolve a keyframe engine."""
    errors, _ = _findings(role="broll", end_frame="refs/end.png")
    assert not [e for e in errors if "end_frame" in e], "b-roll is the keyframe lane"


def test_a_shot_with_no_end_frame_is_never_touched_by_either_rule():
    """Positive control: the rules are inert on every shot that shipped before C10."""
    errors, warnings = _findings(role="presenter", motion_prompt="he leans in, then looks away")
    assert not [x for x in errors + warnings if "end_frame" in x or "keyframe" in x], (
        "the keyframe rules fired on a shot that declares no end frame"
    )


# ── the terse-register WARN ───────────────────────────────────────────────────────────────────


def test_a_long_motion_prompt_on_a_keyframe_shot_warns():
    """The two stills already fix the geometry; prose restating it competes with the tween."""
    verbose = " ".join(["the"] * (KEYFRAME_MOTION_MAX_WORDS + 4))
    errors, warnings = _findings(end_frame="refs/end.png", motion_prompt=verbose)
    assert [w for w in warnings if "motion_prompt" in w and "end_frame" in w], (
        "a long keyframe motion prompt should warn"
    )
    assert not [e for e in errors if "end_frame" in e and "motion_prompt" in e], (
        "terse register is a WARN — a long prompt still renders, it just renders worse"
    )


def test_a_terse_motion_prompt_on_a_keyframe_shot_is_clean():
    """Negative control: a short prompt naming what the frames cannot say."""
    _, warnings = _findings(end_frame="refs/end.png", motion_prompt="slow push, settling")
    assert not [w for w in warnings if "over" in w and "motion_prompt" in w], (
        "a terse keyframe prompt must not warn"
    )


def test_a_long_motion_prompt_WITHOUT_an_end_frame_stays_clean():
    """The inverse rule must not leak: everywhere else a rich motion prompt is what we want."""
    verbose = " ".join(["the"] * (KEYFRAME_MOTION_MAX_WORDS + 10))
    _, warnings = _findings(motion_prompt=verbose)
    assert not [w for w in warnings if "end_frame" in w], (
        "the keyframe brevity rule fired on a shot with no end frame — it would refuse the "
        "detailed prompts every other shot is required to carry"
    )


def test_a_two_clause_keyframe_motion_prompt_warns_because_it_can_only_arrive_once():
    """One interval, one destination — two moves needs two shots."""
    _, warnings = _findings(
        end_frame="refs/end.png", motion_prompt="the dial turns, then the light fades"
    )
    assert [w for w in warnings if "clause" in w], "two clauses in one keyframe shot should warn"


def test_a_missing_motion_prompt_on_a_keyframe_shot_is_left_to_the_rule_that_owns_it():
    """Two rules raising the same finding is how a message family ends up reported twice."""
    warnings: list[str] = []
    _lint_keyframe_motion_prompt({"end_frame": "refs/end.png"}, "shot[1]", warnings)
    assert warnings == [], "the missing-motion_prompt ERROR belongs to _lint_motion_prompt"


# ── the rules are callable in isolation and publish themselves ────────────────────────────────


def test_the_role_gate_writes_nothing_for_a_shot_with_an_empty_end_frame():
    """An empty string is not a declaration; whitespace must not trip the gate."""
    errors: list[str] = []
    _lint_end_frame_role({"role": "presenter", "end_frame": "   "}, "shot[1]", errors)
    assert errors == [], "whitespace in end_frame was read as a declared last frame"


def test_the_thin_motion_prompt_warn_still_fires_on_a_shot_with_no_end_frame():
    """Positive control for the exemption above: the thin-prompt rule keeps its full force.

    The exemption is narrow on purpose. Everywhere else a three-word motion prompt is the
    2026-08-18 defect — six shots shipped with no action described and half the runtime rendered
    motionless — so the exemption must be readable as "the frames say it instead", never as a
    general relaxation.
    """
    _, warnings = _findings(motion_prompt="slow push, settling")
    assert [w for w in warnings if "too thin" in w], (
        "the keyframe exemption leaked onto a shot with no end frame"
    )


@pytest.mark.parametrize(
    "prompt",
    [
        "slow push, settling",
        "the dial turns, unhurried",
        "a slow, even pan",
    ],
)
def test_a_comma_separating_a_move_from_its_manner_is_not_two_moves(prompt):
    """Regression: keyed on the comma, this rule warned on the exact form the body recommends.

    "slow push, settling" is one move with a quality note — which is precisely what a keyframe
    prompt is supposed to contain, since the frames carry the geometry. A rule that fires on its
    own recommended output teaches operators that the warning column is noise.
    """
    _, warnings = _findings(end_frame="refs/end.png", motion_prompt=prompt)
    assert not [w for w in warnings if "clause" in w], f"{prompt!r} was read as two moves"


@pytest.mark.parametrize(
    "prompt",
    [
        "the dial turns, then the light fades",
        "the lid lifts; the contents settle",
        "a slow push followed by a rack focus",
    ],
)
def test_an_explicit_sequencer_is_still_read_as_two_moves(prompt):
    """Positive control for the fix above — the rule must not have been defanged."""
    _, warnings = _findings(end_frame="refs/end.png", motion_prompt=prompt)
    assert [w for w in warnings if "clause" in w], f"{prompt!r} sequences two moves and must warn"
