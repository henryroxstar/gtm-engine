"""Load-time refusals, the sufficiency gate, and the invariants the PRD calls unrepresentable.

Every test here names the wrong number it prevents. A card that scores out of 87 while every
rendering says 100, an axis that scores our own research coverage, a required input that gates
nothing — each produces a plausible list, which is why none of them was caught by review.

Fixtures are fictional per §R9 (``python -m gtm_core.fictionalize``).
"""

from __future__ import annotations

import pytest

from gtm_core.scorecard import (
    Batch,
    Categorised,
    ScoreCardError,
    Scored,
    parse,
    score_row,
    score_rows,
)
from gtm_core.scorecard.cli import enabled

# A minimal well-formed card: two axes, one vocabulary and one components, a modulator and a gate.
GOOD = """
scorecard_version = "2026-01-01"
source = "knowledge/fixture.md#rubric"
ceiling = 50
tiers = { A = 40, B = 25 }
bottom_tier = "C"
required_inputs = ["tier_label", "in_region", "researched", "agent_evidence"]

[category]
tier_label = "Unscored — no label"
in_region = "Blocked — outside the regions"
researched = "Unscored — no research on file"
agent_evidence = "Unscored — agent activity not assessed"

[exclusion]
partner = "Partner — channel (not a buyer)"

[[axis]]
name = "fit"
max = 20
input = "tier_label"
weights = { T1 = 20, T2 = 10 }
modulated_by = "sub"
modulates = "T1"

[axis.phrases]
T1 = "T1 fit on the gated rubric"
T2 = "T2 fit"

[[axis]]
name = "evidence"
max = 18
input = "agent_evidence"
weights = { present = 18, industry_only = 6, absent = 2 }

[[axis]]
name = "timing"
max = 12
components = { dated_why_now = 8, in_region = 4 }
requires = { dated_why_now = "researched" }
"""

FULL_ROW = {
    "tier_label": "T2",
    "in_region": True,
    "researched": True,
    "agent_evidence": "present",
    "dated_why_now": True,
}


@pytest.fixture
def card():
    return parse(GOOD, "fixture.toml")


def _card_with(old: str, new: str):
    return parse(GOOD.replace(old, new), "fixture.toml")


# --------------------------------------------------------------------------------------------
# 1. Load-time refusals (W2)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "proxy",
    ["dossier_length", "has_evidence_url", "team_count", "description_length", "row_completeness"],
)
def test_an_axis_scoring_our_own_research_coverage_fails_at_load(proxy: str) -> None:
    """§R13. This is the 2026-09-21 defect as config: the ICP axis was a description LENGTH
    check, and the resulting mean tracked how many teams had listed a company."""
    with pytest.raises(ScoreCardError, match=proxy):
        _card_with('input = "tier_label"', f'input = "{proxy}"')


def test_a_coverage_proxy_as_a_COMPONENT_also_fails() -> None:
    """The denylist governs every point-awarding field, not only the headline axis input."""
    with pytest.raises(ScoreCardError, match="team_count"):
        _card_with("dated_why_now = 8, in_region = 4", "team_count = 8, in_region = 4")


def test_gating_on_research_coverage_is_allowed_and_is_the_point() -> None:
    """The boundary the denylist draws. The same fact that may not award points MUST be able to
    gate: thin research yields a category naming the unlock, never a low number."""
    card = parse(GOOD, "fixture.toml")
    assert "researched" in card.required_inputs
    result = score_row(card, FULL_ROW | {"researched": False})
    assert isinstance(result, Categorised)
    assert result.missing_input == "researched"


def test_axis_maxima_must_sum_to_the_declared_ceiling() -> None:
    """Otherwise every score renders out of a denominator the card does not use."""
    with pytest.raises(ScoreCardError, match="ceiling"):
        _card_with("ceiling = 50", "ceiling = 60")


def test_a_weight_above_its_own_axis_max_fails() -> None:
    with pytest.raises(ScoreCardError, match="above its own max"):
        _card_with("T1 = 20, T2 = 10", "T1 = 99, T2 = 10")


def test_components_that_do_not_sum_to_their_axis_max_fail() -> None:
    with pytest.raises(ScoreCardError, match="components sum"):
        _card_with("dated_why_now = 8, in_region = 4", "dated_why_now = 8, in_region = 1")


def test_a_required_input_no_axis_reads_fails() -> None:
    """An orphan is a typo, and a typo here gates nothing — the row scores anyway."""
    with pytest.raises(ScoreCardError, match="read by no axis"):
        _card_with('"researched", "agent_evidence"]', '"researched", "agent_evidenc"]')


def test_a_required_input_with_no_category_text_fails() -> None:
    with pytest.raises(ScoreCardError, match="no .category. text"):
        _card_with('in_region = "Blocked — outside the regions"\n', "")


def test_a_modulator_naming_a_value_the_axis_cannot_weigh_fails() -> None:
    with pytest.raises(ScoreCardError, match="modulates"):
        _card_with('modulates = "T1"', 'modulates = "T9"')


def test_a_card_that_scores_nothing_fails() -> None:
    with pytest.raises(ScoreCardError, match="no .*axis"):
        parse("ceiling = 10\ntiers = { A = 5 }\n", "empty.toml")


def test_broken_toml_names_the_file_not_a_traceback() -> None:
    with pytest.raises(ScoreCardError, match="not valid TOML"):
        parse("ceiling = = 3", "truncated.toml")


def test_every_problem_is_reported_at_once() -> None:
    """Fixing a card one exception at a time is how a card ends up half-migrated."""
    with pytest.raises(ScoreCardError) as caught:
        _card_with('input = "tier_label"', 'input = "team_count"')
    message = str(caught.value)
    assert "team_count" in message and "read by no axis" in message


# --------------------------------------------------------------------------------------------
# 2. The sufficiency gate
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["tier_label", "researched", "agent_evidence"])
def test_a_missing_required_input_categorises_rather_than_scoring_low(card, missing) -> None:
    result = score_row(card, {k: v for k, v in FULL_ROW.items() if k != missing})
    assert isinstance(result, Categorised)
    assert result.missing_input == missing


def test_required_inputs_are_checked_in_declared_order(card) -> None:
    """Which of two missing inputs the operator is told about is a tenant decision, so it is
    declaration order — not dict order, not axis order."""
    result = score_row(card, {"agent_evidence": "present"})
    assert isinstance(result, Categorised)
    assert result.missing_input == "tier_label"


def test_an_out_of_region_row_is_blocked_not_deducted(card) -> None:
    """The market gate is an allow/deny. Scoring it as a deduction would let a strong enough
    account buy its way back into a market we do not sell into."""
    result = score_row(card, FULL_ROW | {"in_region": False})
    assert isinstance(result, Categorised)
    assert result.category.startswith("Blocked")


def test_an_unrecognised_exclusion_refuses_rather_than_being_ignored(card) -> None:
    """Silently ignoring an exclusion token the card cannot name would SCORE the row — the one
    outcome an upstream exclusion exists to prevent."""
    with pytest.raises(ScoreCardError, match="unrecognised exclusion"):
        score_row(card, FULL_ROW | {"exclusion": "by_legal_hold"})


def test_an_exclusion_beats_a_row_that_would_otherwise_score(card) -> None:
    result = score_row(card, FULL_ROW | {"exclusion": "partner"})
    assert isinstance(result, Categorised)
    assert result.missing_input == "partner"


# --------------------------------------------------------------------------------------------
# 3. Scoring, modulation, rounding
# --------------------------------------------------------------------------------------------


def test_a_complete_row_scores_the_sum_of_its_axes(card) -> None:
    result = score_row(card, FULL_ROW)
    assert isinstance(result, Scored)
    assert result.score == 10 + 18 + 8 + 4


def test_the_modulator_scales_its_label_rather_than_adding_beside_it(card) -> None:
    """A weak member of the best bucket must not outrank a strong member of a lesser one."""
    weak_t1 = score_row(card, FULL_ROW | {"tier_label": "T1", "sub": {"subtotal": 2, "max": 9}})
    strong_t2 = score_row(card, FULL_ROW | {"tier_label": "T2"})
    assert isinstance(weak_t1, Scored) and isinstance(strong_t2, Scored)
    assert weak_t1.score < strong_t2.score


def test_a_malformed_modulator_refuses(card) -> None:
    for bad in (
        {"subtotal": 4},
        {"subtotal": 4, "max": 0},
        {"subtotal": 12, "max": 9},
        "4/9",
        None,
    ):
        with pytest.raises(ScoreCardError):
            score_row(card, FULL_ROW | {"tier_label": "T1", "sub": bad})


def test_rounding_is_applied_once_to_the_total(card) -> None:
    """The only place a fraction reaches the sum is the modulator. Pinned explicitly because a
    per-axis round and a total round differ, and the live corpus has rows landing on .5."""
    result = score_row(card, FULL_ROW | {"tier_label": "T1", "sub": {"subtotal": 1, "max": 8}})
    assert isinstance(result, Scored)
    assert result.score == round(20 * 1 / 8 + 18 + 8 + 4)


# --------------------------------------------------------------------------------------------
# 4. Unrepresentability, conservation, row-locality (PRD §5, test plan §4.3-§4.5)
# --------------------------------------------------------------------------------------------


def test_a_result_carries_a_score_or_a_category_never_both(card) -> None:
    scored = score_row(card, FULL_ROW)
    categorised = score_row(card, {})
    assert isinstance(scored, Scored) and not hasattr(scored, "category")
    assert isinstance(categorised, Categorised) and not hasattr(categorised, "score")


def test_the_score_tier_and_derivation_all_come_from_one_result(card) -> None:
    """§4.5. A tier recomputed at the rendering layer is the defect."""
    result = score_row(card, FULL_ROW)
    assert isinstance(result, Scored)
    sentence = result.sentence()
    assert f"{result.score}/100" in sentence
    assert f"Tier {result.tier}" in sentence
    for part in result.derivation:
        assert part in sentence


def test_a_row_scores_the_same_alone_as_in_a_batch(card) -> None:
    """§4.3. The scorecard is row-local and joins nothing; a future axis reaching for a second
    row would silently acquire a whole defect class."""
    alone = score_row(card, FULL_ROW)
    crowd = score_rows(
        card, [FULL_ROW | {"tier_label": "T1", "sub": {"subtotal": 9, "max": 9}}] * 50 + [FULL_ROW]
    )
    assert crowd.results[-1] == alone


def test_scored_plus_categorised_equals_the_input_count(card) -> None:
    """§4.4. A row that vanishes from the denominator produces a confidently wrong distribution."""
    rows = [FULL_ROW, {}, FULL_ROW | {"in_region": False}, {"tier_label": "T2"}]
    batch = score_rows(card, rows)
    assert len(batch.scored) + len(batch.categorised) == batch.input_count == len(rows)


def test_conservation_is_checked_and_not_merely_documented() -> None:
    with pytest.raises(ScoreCardError, match="conservation violated"):
        Batch(results=(), input_count=3)


def test_a_malformed_row_names_itself_instead_of_being_skipped(card) -> None:
    """§4.4. Refuse, never skip — a skipped row leaves the denominator silently."""
    with pytest.raises(ScoreCardError, match="row 2"):
        score_rows(card, [FULL_ROW, "not a mapping", FULL_ROW])


# --------------------------------------------------------------------------------------------
# 5. The kill switch parses in the safe direction
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " Off "])
def test_a_recognised_false_switches_the_engine_off(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GTM_SCORECARD_ENABLED", value)
    assert enabled() is False


@pytest.mark.parametrize("value", ["", "   ", "true", "1", "wharrgarbl", "disabled", "nope"])
def test_anything_unrecognised_leaves_the_engine_ON(monkeypatch, value: str) -> None:
    """The mirror of the house default-off idiom. This switch DISABLES A REFUSAL, so the
    permissive side is "off" — and an unrecognised value must never land there. Note ``disabled``
    and ``nope`` read as "off" to a human and are deliberately not honoured: a closed list is
    only safe if it is closed in the direction that matters."""
    monkeypatch.setenv("GTM_SCORECARD_ENABLED", value)
    assert enabled() is True


def test_an_unset_switch_leaves_the_engine_ON(monkeypatch) -> None:
    monkeypatch.delenv("GTM_SCORECARD_ENABLED", raising=False)
    assert enabled() is True


# --------------------------------------------------------------------------------------------
# 6. An out-of-vocabulary value is a MISSING input, not a crash
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("tier_label", "Not ICP fit — US-only, no APAC presence"),
        ("tier_label", "T9"),
        ("agent_evidence", "Present"),
        ("agent_evidence", "looks agentic to me"),
    ],
)
def test_a_value_outside_an_axis_vocabulary_categorises_rather_than_raising(card, field, value):
    """Found by mutation testing: the sufficiency gate tested only for ABSENCE, so a row whose
    value was present-but-unrecognised slipped past it and blew up in the weight lookup instead
    of being categorised.

    This is not hypothetical — it is the commonest real shape. 20 rows in the live corpus carry
    free text like "Not ICP fit — US-only" in the ICP column: a populated field asserting the
    row does not qualify. The honest outcome is the category naming the unlock, not a traceback
    and not a score.
    """
    result = score_row(card, FULL_ROW | {field: value})
    assert isinstance(result, Categorised)
    assert result.missing_input == field


def test_an_out_of_vocabulary_value_never_reaches_the_weight_lookup(card) -> None:
    """The other half of the same property: `_weighted` still refuses if called directly, so the
    gate is defence-in-depth rather than the only thing standing between a bad value and a 0."""
    from gtm_core.scorecard.score import _weighted

    with pytest.raises(ScoreCardError, match="not one of"):
        _weighted(card.axes[0], {"tier_label": "T9"})


# --------------------------------------------------------------------------------------------
# 7. Components grant on the literal True alone (found by mutation testing)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("truthy", ["yes", "true", "TRUE", 1, "no", "false", ["x"], {"a": 1}, 0.5])
def test_an_OPTIONAL_component_grants_on_the_literal_True_alone(card, truthy) -> None:
    """``dated_why_now`` is a component but NOT a required input — a row may legitimately answer
    "no dated why-now" and score 0 for it. That makes it the one place a truthy non-``True``
    value reaches the arithmetic without passing the sufficiency gate first.

    ``bool("false")`` is ``True``, and ``bool("no")`` is ``True``. Under a truthiness check every
    one of these values silently buys the full 8 points. Zero is the safe way to be wrong here:
    the row simply does not get timing credit it cannot evidence.
    """
    result = score_row(card, FULL_ROW | {"dated_why_now": truthy})
    assert isinstance(result, Scored)
    assert result.score == score_row(card, FULL_ROW | {"dated_why_now": False}).score


def test_the_optional_component_still_grants_on_a_real_True(card) -> None:
    """Positive control — the rule above must not zero the component unconditionally."""
    granted = score_row(card, FULL_ROW | {"dated_why_now": True})
    withheld = score_row(card, FULL_ROW | {"dated_why_now": False})
    assert isinstance(granted, Scored) and isinstance(withheld, Scored)
    assert granted.score - withheld.score == 8


def test_a_component_whose_precondition_is_unsatisfied_is_refused_not_zeroed(card) -> None:
    """Defence-in-depth, exercised directly because ``score_row``'s gate makes it unreachable
    from the top: "no dated why-now" and "nobody looked for one" are different answers, and only
    the first is a legitimate zero. Reached via a direct call the way ``_weighted`` is."""
    from gtm_core.scorecard.score import _componentwise

    timing = next(a for a in card.axes if a.name == "timing")
    with pytest.raises(ScoreCardError, match="that is a category, not a zero"):
        _componentwise(timing, {"dated_why_now": True, "in_region": True, "researched": False})

    points, _phrase = _componentwise(
        timing, {"dated_why_now": True, "in_region": True, "researched": True}
    )
    assert points == 12


# --------------------------------------------------------------------------------------------
# 8. §R6 — the scorecard is pure computation and opens no egress
# --------------------------------------------------------------------------------------------


def test_the_scorecard_package_imports_no_http_client() -> None:
    """The §R6 semgrep rule scans ``gtm_core/**`` and forbids these four, including imports
    written inside a function body. Asserted here as well so the property is visible where the
    code is, not only in a CI config."""
    import ast
    import pathlib

    import gtm_core.scorecard as pkg

    forbidden = {"httpx", "requests", "aiohttp", "urllib"}
    offenders = []
    for path in sorted(pathlib.Path(pkg.__file__).parent.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            offenders += [
                f"{path.name}:{node.lineno}: {n}" for n in names if n.split(".")[0] in forbidden
            ]
    assert not offenders, "\n".join(offenders)


def test_the_egress_check_is_not_vacuous() -> None:
    """Negative control: the walk above must actually see imports, or a package with none and a
    package full of them look the same."""
    import ast
    import pathlib

    import gtm_core.scorecard as pkg

    seen = {
        a.name
        for path in pathlib.Path(pkg.__file__).parent.glob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Import)
        for a in node.names
    }
    assert "tomllib" in seen, f"the import walk found nothing it should have: {seen}"


# --------------------------------------------------------------------------------------------
# 9. A row with several gaps names all of them (SR2)
# --------------------------------------------------------------------------------------------


def test_a_row_with_several_gaps_names_every_one(card) -> None:
    """Which gap is REPORTED is the card's declared order — a choice, and one no row in the
    2026-09-21 corpus can evidence, because every row there records exactly one missing input.
    Carrying the full list makes that choice visible instead of load-bearing."""
    result = score_row(card, {"agent_evidence": "present"})
    assert isinstance(result, Categorised)
    assert result.missing_input == "tier_label"
    assert result.missing_inputs == ("tier_label", "in_region", "researched")


def test_the_reported_gap_is_always_the_first_declared(card) -> None:
    """The precedence rule itself, pinned: declaration order, not dict order, not axis order."""
    result = score_row(card, {})
    assert isinstance(result, Categorised)
    assert result.missing_inputs[0] == result.missing_input
    assert list(result.missing_inputs) == [
        n for n in card.required_inputs if n in result.missing_inputs
    ]


def test_a_single_gap_still_reports_exactly_one(card) -> None:
    """The other half — the list must not simply always be long."""
    result = score_row(card, {k: v for k, v in FULL_ROW.items() if k != "agent_evidence"})
    assert isinstance(result, Categorised)
    assert result.missing_inputs == ("agent_evidence",)


def test_an_exclusion_carries_no_gap_list(card) -> None:
    """An exclusion is a verdict about the account, not a gap in our inputs. Listing "missing"
    inputs there would invite someone to go fill them in on a row that is already decided."""
    result = score_row(card, FULL_ROW | {"exclusion": "partner"})
    assert isinstance(result, Categorised)
    assert result.missing_inputs == ()
