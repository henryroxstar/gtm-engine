"""The expression rule fires on a reaction shot and stays quiet on a direction.

The defect this rule was written for is over-direction, which arrives in two costumes: an
adjective the writer never translated into muscles, and a stack of individually-true markers
delivered at once. The shipped case was a payoff beat asking for "eyes closing, brows lifted at
the inner ends and held wide apart, mouth closed and still, jaw loose, chin steady, eyes wet and
holding" — six regions, two of which disagree in muscle terms, rendering as anguish rather than a
person quietly moved.

Three things about these fixtures, stated rather than implied:

* **They are shape-derived, not the shipped rows.** The rows live in the tenant's own content
  tree, which may not cross into this one (§R9). Each case below reproduces a HIT SHAPE on
  invented subjects and settings.
* **The negative-control half carries the weight**, exactly as it does for the cross-shot rule.
  A stop list that also refuses "eyes wet with nothing falling" would refuse the writing this
  whole change argues for, so every banned release form is paired with the restraint form that
  must survive it.
* **The ceiling is measured, not chosen.** Across the twelve expression values written on real
  shot lists, a ceiling of 4 regions fires on exactly the two stacks the operator flagged and
  passes the four well-formed multi-region directions; a ceiling of 3 fires on 4 of 12, including
  well-formed ones, and a ceiling of 5 misses one of the two stacks.
"""

from __future__ import annotations

import pytest

from gtm_core import shots_lint as sl


def _run(expression: str, **shot_extra) -> tuple[list[str], list[str]]:
    shot = {
        "duration_s": 5,
        "camera": "static, eye level",
        "visual": "a woman at a kitchen table",
        "motion_prompt": "she turns the cup a quarter turn",
        "expression": expression,
        **shot_extra,
    }
    errors: list[str] = []
    warnings: list[str] = []
    sl._lint_expression(shot, "shot[2]", errors, warnings)
    return errors, warnings


# ── T1 · every stop-list entry fires, and its restraint twin does not ──────────────────


#: (banned release form, the restraint form that must stay legal). The right-hand column is the
#: real point: each is a phrase this change wants people to write, sitting next to the phrase it
#: is most likely to be confused with by a regex.
RELEASE_AND_RESTRAINT: tuple[tuple[str, str], ...] = (
    ("beaming at the news", "cheeks lifting a fraction, lower lids pushing up, mouth closed"),
    ("grinning as he reads it", "one corner of the mouth tucking in, held"),
    ("a smile from ear to ear", "the smile reaching the eyes and going no wider"),
    ("wide-eyed at the total", "eyes widening a millimetre, the head stopping"),
    ("eyes wide as she turns", "lids opening slightly, gaze steady on him"),
    ("eyes bulging at the figure", "lower lids tightening, gaze fixed"),
    ("his jaw drops", "the jaw loosening a little, mouth staying closed"),
    ("mouth agape", "lips parting a few millimetres and staying there"),
    ("she gasps at the total", "one short breath in through the nose"),
    ("shocked by the number", "a blink held a beat too long, then the gaze coming back"),
    ("stunned into silence", "stillness, the hands stopping, gaze to the middle distance"),
    ("tears streaming down her face", "eyes wet with nothing falling"),
    ("sobbing at the table", "a swallow, then a breath out through the nose"),
    ("weeping as she reads", "eyes wet and holding, mouth closed and still"),
    ("crying as he says it", "refusing to let it reach her face, one slow blink"),
    ("ecstatic at the result", "a quiet exhale, the shoulders dropping"),
    ("overjoyed to see him", "warmth arriving in the eyes before the mouth"),
    ("elated by the outcome", "relief settling, the jaw unclenching"),
    ("thrilled by the offer", "brows lifting a few millimetres, gaze steady"),
    ("furious at the delay", "the jaw setting, lips pressing, stillness"),
    ("enraged by the reply", "lower lids tightening, breath going shallow"),
    ("she screams at the screen", "a sharp breath in, mouth closing again"),
    ("laughing hysterically", "a laugh arriving at the same time as the wet eyes"),
    ("he throws his head back", "the chin lifting a fraction"),
    ("punching the air", "one hand closing on the table edge"),
    ("a fist pump at the desk", "the fingers spreading flat on the desk"),
)


@pytest.mark.parametrize(
    ("release", "restraint"), RELEASE_AND_RESTRAINT, ids=[r[0] for r in RELEASE_AND_RESTRAINT]
)
def test_the_release_form_errors_and_its_restraint_twin_does_not(release: str, restraint: str):
    """A release is refused; the containment of the same feeling is the writing we want."""
    errors, _ = _run(release)
    assert len(errors) == 1, f"{release!r} did not fire the stop list"
    assert "names a stock reaction" in errors[0]

    errors, warnings = _run(restraint)
    assert errors == [], f"{restraint!r} was refused, and it is what this rule asks people to write"
    assert [w for w in warnings if "stock" in w] == []


def test_a_stock_reaction_reports_once_and_stops():
    """One finding per field: a value that is both stock AND stacked names the disqualifying one."""
    errors, warnings = _run(
        "beaming, brows up, mouth open, jaw loose, chin lifted, shoulders back, hands raised"
    )
    assert len(errors) == 1
    assert warnings == []


# ── T2 · an adjective with no region ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "adjective",
    [
        "conversational and a little amused",
        "matter-of-fact, correcting a misunderstanding",
        "level and slightly wry",
        "a flicker of interest settling back into level, unpersuaded calm",
        "calm",
    ],
)
def test_a_tone_word_with_no_region_warns(adjective: str):
    """Tone is a property of prose. Handed to a model as a face, it renders at full magnitude."""
    errors, warnings = _run(adjective)
    assert errors == []
    assert len(warnings) == 1
    assert "names no facial region" in warnings[0]


def test_a_register_label_survives_when_it_carries_muscles():
    """The good real rows open with a register word and then say what moves. That must stay legal."""
    errors, warnings = _run(
        "recognition — stillness, a small exhale through the nose, gaze held a beat too long"
    )
    assert (errors, warnings) == ([], [])


# ── T3 · the region ceiling ───────────────────────────────────────────────────────────


def test_the_grammar_s_own_maximum_passes():
    """Two regions moving, one holding, and the eyes — four, which is what the grammar allows."""
    errors, warnings = _run(
        "the inner brows lifting a fraction, mouth closed and still, jaw loose, "
        "gaze going down to the table"
    )
    assert (errors, warnings) == ([], [])


def test_one_region_past_the_ceiling_warns():
    """A fifth region is a stack, not a direction."""
    errors, warnings = _run(
        "the inner brows lifting a fraction, mouth closed and still, jaw loose, chin steady, "
        "gaze going down to the table"
    )
    assert errors == []
    assert len(warnings) == 1
    assert "names 5 facial regions" in warnings[0]


def test_the_shipped_stack_is_what_this_rule_catches():
    """The value that motivated the rule, reproduced in shape: six regions, two disagreeing."""
    errors, warnings = _run(
        "eyes closing, brows lifted at the inner ends and held wide apart, mouth closed and "
        "still, jaw loose, chin steady, one shoulder dropping"
    )
    assert errors == []
    assert "names 6 facial regions" in warnings[0]


def test_a_region_named_twice_counts_once():
    """The count is of things MOVING, so one region described from two angles is still one."""
    assert sl._regions_named(
        sl._normalize("eyes closing, eyes wet, gaze down, one slow blink")
    ) == ["eye"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # "eyebrows" is brow, not brow AND eye — the boundary that keeps the count honest.
        ("eyebrows lifting", ["brow"]),
        ("forehead smoothing", ["brow"]),
        # "widen" is a direction; "wide" is the stock signature. Only the latter is refused.
        ("eyes widen slightly", ["eye"]),
        ("half-smiling at a thought", ["mouth"]),
        ("a swallow, then a breath out through the nose", ["breath"]),
    ],
)
def test_region_boundaries(text: str, expected: list[str]):
    assert sl._regions_named(sl._normalize(text)) == expected


# ── T4 · scope ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["presenter", "broll", "screen", None])
def test_the_rule_reads_the_field_and_never_the_role(role: str | None):
    """The eight stacked values that motivated this rule were all on b-roll — a story's ACTORS.
    A presenter-only rule would have been blind to the defect it was written for."""
    extra = {} if role is None else {"role": role}
    errors, _ = _run("beaming at the news", **extra)
    assert len(errors) == 1


@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_absent_expression_is_not_this_rule_s_business(value):
    """A missing expression is a script gap, and two places already see the whole list at once."""
    shot = {"duration_s": 5, "camera": "static", "visual": "a table"}
    if value is not None:
        shot["expression"] = value
    errors: list[str] = []
    warnings: list[str] = []
    sl._lint_expression(shot, "shot[2]", errors, warnings)
    assert (errors, warnings) == ([], [])


# ── T5 · the rule publishes itself ────────────────────────────────────────────────────


def test_the_rule_and_its_ceiling_are_self_describing():
    """`--rules` is how the lexicon cites this without restating the number in prose."""
    names = [r["name"] for r in sl.active_rules()]
    assert "_lint_expression" in names
    summary = next(r["summary"] for r in sl.active_rules() if r["name"] == "_lint_expression")
    assert summary and summary != "(no docstring)"
    assert sl._active_constants()["expression_max_regions"] == sl._EXPRESSION_MAX_REGIONS


def test_the_ceiling_has_one_home():
    """A second copy of the number is how a published constant starts disagreeing with the rule."""
    _, warnings = _run(
        "brows up, mouth closed, jaw loose, chin steady, gaze down, one shoulder dropping"
    )
    assert f"past the {sl._EXPRESSION_MAX_REGIONS} the grammar allows" in warnings[0]


def test_every_message_points_at_the_lexicon():
    """A refusal that does not say what to write instead is a rule people learn to work around."""
    for value in (
        "beaming at the news",
        "calm",
        "brows up, mouth closed, jaw loose, chin steady, gaze down, shoulders back",
    ):
        errors, warnings = _run(value)
        assert "performance-lexicon.md" in (errors + warnings)[0]
