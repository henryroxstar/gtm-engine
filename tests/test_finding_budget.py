"""Tests for the severity budget — the readability gate on a gate's own output.

The numbers in the docstrings are the real measurement that produced this module
(2026-08-19: 388 warnings, 11 of them competitor flags), but every fixture finding
below is invented, per the third-party-PII rule (docs/RULES.md R9).
"""

from __future__ import annotations

from gtm_core.finding_budget import (
    SATURATION,
    WARN_BUDGET,
    budget_verdict,
    group_by_rule,
    render_budget,
    split_rule,
)


def test_split_rule_separates_name_from_detail():
    assert split_rule("competitor-flag: 'Rival Corp' — ships an overlap") == (
        "competitor-flag",
        "'Rival Corp' — ships an overlap",
    )


def test_split_rule_keeps_an_unformatted_finding_as_its_own_class():
    """Better a one-member class than a silently dropped finding."""
    assert split_rule("something went wrong") == ("something went wrong", "")


def test_group_by_rule_preserves_first_seen_order():
    grouped = group_by_rule(["b: one", "a: two", "b: three"])
    assert list(grouped) == ["b", "a"]
    assert grouped["b"] == ["one", "three"]


def test_under_budget_enumerates_and_does_not_block():
    v = budget_verdict([f"domain-mismatch: row {i}" for i in range(5)], 100)
    assert not v.over_budget
    assert v.enumerable
    assert not v.blocked
    assert "within the readability budget" in render_budget(v)


def test_over_budget_blocks_and_suppresses_enumeration():
    """The 2026-08-19 shape: a gate that found the right things and printed them in a
    form nobody could read, so the whole block was acknowledged wholesale."""
    findings = ["domain-mismatch: a"] * 300 + ["competitor-flag: b"] * 11
    v = budget_verdict(findings, 292)
    assert v.blocked
    assert not v.enumerable
    out = render_budget(v)
    assert "Enumeration suppressed" in out
    assert "This blocks" in out
    # The 3.8% class is still visible — that is the entire point of a rate.
    assert "competitor-flag: 11/292" in out


def test_exemplars_appear_only_once_enumeration_is_suppressed():
    """A bare rate is not actionable; the caller prints the full list under budget."""
    over = render_budget(budget_verdict(["r: detail-x"] * 40, 100))
    under = render_budget(budget_verdict(["r: detail-x"] * 3, 100))
    assert "e.g. detail-x" in over
    assert "e.g." not in under


def test_ack_is_per_class_and_buys_no_headroom_for_the_rest():
    findings = ["noisy: x"] * 80 + ["sharp: y"] * 20
    v = budget_verdict(findings, 100, acked=["noisy"])
    assert v.unacked == 20  # only the unacknowledged class counts
    assert v.blocked  # 20 > 15: acknowledging one class does not clear the other
    v2 = budget_verdict(findings, 100, acked=["noisy", "sharp"])
    assert v2.unacked == 0
    assert not v2.blocked


def test_acked_classes_still_print():
    """Acknowledgment is not suppression — an accepted class stays on the report."""
    out = render_budget(budget_verdict(["noisy: x"] * 80, 100, acked=["noisy"]))
    assert "[ACKED" in out
    assert "noisy: 80/100" in out


def test_per_class_denominators_keep_the_rate_honest():
    """One gate examines two populations (rows and accounts). A rate against the wrong
    one reads as precise and is wrong."""
    v = budget_verdict(
        ["per-row: a"] * 10 + ["per-account: b"] * 10,
        88,
        denominators={"per-row": 496},
    )
    by_rule = {c.rule: c for c in v.classes}
    assert by_rule["per-row"].denominator == 496
    assert by_rule["per-account"].denominator == 88
    out = render_budget(v, unit="account", units={"per-row": "row"})
    assert "10/496 rows" in out
    assert "10/88 accounts" in out


def test_saturated_class_is_called_out():
    """A class firing on most of the population describes the list, not its members —
    the form that made `leadership-freshness` at 93% legible as miscalibration rather
    than as eighty-two separate problems."""
    v = budget_verdict(["everywhere: x"] * 90, 100)
    assert v.classes[0].saturated
    assert "SATURATED" in render_budget(v)


def test_a_class_just_under_saturation_is_not_flagged():
    v = budget_verdict(["common: x"] * 49, 100)
    assert not v.classes[0].saturated
    assert SATURATION == 0.5


def test_no_findings_renders_nothing():
    assert render_budget(budget_verdict([], 100)) == ""


def test_default_budget_is_a_screenful():
    assert WARN_BUDGET == 15
