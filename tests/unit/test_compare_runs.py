"""The comparison helper must refuse the comparisons that cannot fail.

Two real incidents, both of which produced a green result that meant nothing: a verifier pointed
at one file twice that reported a clean pass, and a hardcoded ``changed == 2006`` that fired
three false failures when the corpus legitimately grew.

Fixtures are fictional per §R9.
"""

from __future__ import annotations

import pytest

from gtm_core.compare_runs import ComparisonError, compare, expect_from

BEFORE = [
    {"id": "aldermoor-labs", "score": 73, "tier": "B"},
    {"id": "wrenfield-systems", "score": 85, "tier": "A"},
    {"id": "caldermere-group", "score": 51, "tier": "C"},
]
AFTER = [
    {"id": "aldermoor-labs", "score": 73, "tier": "B"},
    {"id": "wrenfield-systems", "score": 61, "tier": "C"},
    {"id": "pallister-freight", "score": 44, "tier": "D"},
]
BY_ID = {"key": lambda r: r["id"], "fields": ("score", "tier")}


# --------------------------------------------------------------------------------------------
# 1. The refusals
# --------------------------------------------------------------------------------------------


def test_comparing_the_same_object_with_itself_is_refused() -> None:
    """The incident: a verifier compared a file to itself and reported a clean pass."""
    with pytest.raises(ComparisonError, match="SAME object"):
        compare(BEFORE, BEFORE, **BY_ID)


def test_two_equal_but_distinct_collections_are_also_refused() -> None:
    """Identity is not the only way to get a vacuous comparison — re-reading the same file into
    two lists produces distinct objects with identical contents, and is just as meaningless."""
    with pytest.raises(ComparisonError, match="identical"):
        compare(BEFORE, [dict(r) for r in BEFORE], **BY_ID)


def test_comparing_no_fields_is_refused() -> None:
    with pytest.raises(ComparisonError, match="vacuously"):
        compare(BEFORE, AFTER, key=lambda r: r["id"], fields=())


def test_a_key_that_is_not_an_identity_is_refused() -> None:
    """A grouping key is a claim about identity. Keyed on ``tier``, these three rows would
    collapse to two and the denominator would silently shrink."""
    with pytest.raises(ComparisonError, match="not an identity"):
        compare(
            [*BEFORE, {"id": "extra", "score": 70, "tier": "B"}],
            AFTER,
            key=lambda r: r["tier"],
            fields=("score",),
        )


# --------------------------------------------------------------------------------------------
# 2. It still compares
# --------------------------------------------------------------------------------------------


def test_a_real_comparison_reports_what_moved() -> None:
    """Positive control. A helper that refuses everything passes every test above."""
    result = compare(BEFORE, AFTER, **BY_ID)
    assert result.compared == 2
    assert result.changed_keys == ("wrenfield-systems",)
    assert not result.identical


def test_both_denominators_survive() -> None:
    """A row present on only one side is not a difference and not nothing — it is a third
    category, and folding it into either one misstates the comparison."""
    result = compare(BEFORE, AFTER, **BY_ID)
    assert result.only_before == ("caldermere-group",)
    assert result.only_after == ("pallister-freight",)
    assert result.compared + len(result.only_before) == len(BEFORE)


def test_the_difference_names_the_field_and_both_values() -> None:
    (score_diff,) = [d for d in compare(BEFORE, AFTER, **BY_ID).differences if d.field == "score"]
    assert (score_diff.before, score_diff.after) == (85, 61)
    assert "wrenfield-systems.score: 85 -> 61" == str(score_diff)


def test_the_summary_carries_every_count() -> None:
    assert compare(BEFORE, AFTER, **BY_ID).summary() == (
        "compared 2 · changed 1 · only-before 1 · only-after 1"
    )


# --------------------------------------------------------------------------------------------
# 3. Derived expectations
# --------------------------------------------------------------------------------------------


def test_the_expected_count_is_derived_from_the_input() -> None:
    """``assert changed == 2006`` records the day it was written. This survives the corpus
    growing, which is the only reason the assertion is worth having."""
    scored = expect_from(BEFORE, lambda r: isinstance(r.get("score"), int))
    assert scored == len(BEFORE)
    grown = [*BEFORE, {"id": "new-row", "score": 60, "tier": "C"}]
    assert expect_from(grown, lambda r: isinstance(r.get("score"), int)) == len(grown)


def test_a_derived_expectation_can_still_be_wrong_and_say_so() -> None:
    """Instrument check: the predicate must actually discriminate, or the derivation is just
    ``len()`` wearing a hat."""
    mixed = [*BEFORE, {"id": "unscored-row", "score_category": "Unscored — no ICP label"}]
    assert expect_from(mixed, lambda r: isinstance(r.get("score"), int)) == 3
    assert expect_from(mixed, lambda r: "score_category" in r) == 1
