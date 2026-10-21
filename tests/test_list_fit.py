"""Tests for the pre-enrolment list gate.

Every fixture here is invented. The defects are real ones observed on a live list, but the
identities are fictional per the third-party-PII rule (docs/RULES.md R9): real people and real
companies live only in `profiles/` and `content/`.
"""

from __future__ import annotations

import csv
import datetime

import pytest

from gtm_core import list_fit as lf
from gtm_core.list_fit import (
    RoleFit,
    SignalGrade,
    audit_rows,
    role_fit,
    signal_grade,
    source_hit_rates,
)

AS_OF = datetime.date(2026, 8, 11)


# --- role fit ------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Chief Technology Officer",
        "Chief Technical Officer",  # regressed once: "technolog" did not match "technical"
        "CTO & SVP of Engineering",
        "Chief Information Security Officer",
        "Vice President and Chief Security Officer",
        "Director of Software Engineering",  # regressed once: only "director of engineering" matched
        "Senior director engineering",  # no comma, no "of"
        "Head of AI strategy and deployment",
        "Chief Compliance Officer",
        "Vice President, Compliance Officer",
        "Executive Vice President, Chief Risk Officer",
        "Director - Risk Management",
        "Director information services",
        "General counsel",
        "Co-Founder & CEO",
        "Principal Architect",
    ],
)
def test_role_fit_accepts_seats_that_could_own_the_purchase(title):
    assert role_fit(title) == RoleFit.IN


@pytest.mark.parametrize(
    "title",
    [
        "Director of talent acquisition",
        "Director of pharmacy",
        "Director of hospitality operations",
        "Patient liaison director",
        "Director of materials management",
        "Senior vice president human resources",
        "Growth marketing director",
        "Vice president marketing",
        "Director of physician relations",
        "Vice president sales strategy and execution",
        "Senior director, business development",
        "Vice president of market underwriting",
    ],
)
def test_role_fit_rejects_seats_that_cannot_buy(title):
    assert role_fit(title) == RoleFit.OUT


def test_out_beats_in_when_a_title_contains_both():
    """A disqualifying token is load-bearing even when a plausible one is also present.

    'VP Human Resources Business Consulting' matches nothing in the IN pattern but would match a
    naive substring check; 'Director - Human Resources & Legal' matches 'legal' in the IN pattern
    and must still be OUT.
    """
    assert role_fit("Vice president human resources business consulting") == RoleFit.OUT
    assert role_fit("Director - human resources & legal") == RoleFit.OUT
    assert role_fit("Director of physician recruitment technology") == RoleFit.OUT


@pytest.mark.parametrize("title", ["", "   ", "President", "Managing director", "Director"])
def test_unplaceable_titles_are_unclear_not_passed(title):
    """Fail-closed: what the gate cannot place is never silently treated as a good seat."""
    assert role_fit(title) == RoleFit.UNCLEAR


# --- signal grade --------------------------------------------------------


def test_fresh_dated_signal_supports_a_news_opener():
    assert signal_grade("Series B announced 2026-07-02", as_of=AS_OF) == SignalGrade.FRESH


def test_aged_dated_signal_is_stale_not_fresh():
    """A 13-month-old round is history. Opening on it advertises a stale list."""
    assert signal_grade("$300M growth investment, 2025-07-22", as_of=AS_OF) == SignalGrade.STALE


def test_structural_fact_is_usable_and_does_not_decay():
    """The preferred hook: undated by nature, so it cannot go stale mid-campaign."""
    clause = "their platform runs autonomous agents against regulated casefiles"
    assert signal_grade(clause, as_of=AS_OF) == SignalGrade.STRUCTURAL
    # same verdict a year later -- that is the entire point
    assert signal_grade(clause, as_of=datetime.date(2027, 8, 11)) == SignalGrade.STRUCTURAL


@pytest.mark.parametrize(
    "clause",
    [
        "machine learning & artificial intelligence (intent score 93)",
        "emerging tech (intent score 77)",
        "security (intent score 79)",
    ],
)
def test_intent_score_is_not_a_fact(clause):
    """A Bombora topic score ranks who to contact. It is not a sentence and must never
    reach an email body -- shipping it renders a non-sequitur."""
    assert signal_grade(clause, as_of=AS_OF) == SignalGrade.INTENT_ONLY
    assert signal_grade(clause, as_of=AS_OF) not in SignalGrade.USABLE


def test_hedged_research_note_is_absent_not_structural():
    """A note admitting it found nothing datable is honest, and still unusable as an opener."""
    assert (
        signal_grade("No dated funding round confirmed in research", as_of=AS_OF)
        == SignalGrade.ABSENT
    )


def test_empty_signal_is_absent():
    assert signal_grade("", as_of=AS_OF) == SignalGrade.ABSENT
    assert signal_grade(None, as_of=AS_OF) == SignalGrade.ABSENT


def test_only_fresh_and_structural_are_usable():
    assert SignalGrade.USABLE == {SignalGrade.FRESH, SignalGrade.STRUCTURAL}


# --- source attribution --------------------------------------------------


def _row(title="Chief Technology Officer", src="backlog-enrich", tier="", why_now=""):
    return {"title": title, "src": src, "tier": tier, "why_now": why_now}


def test_source_hit_rates_group_run_families_together():
    """Per-run filenames split the evidence; the run *family* is the unit that has a hit rate."""
    rows = [_row(src="prospects-20260724-backlog-enrich-run5-hubspot.csv") for _ in range(3)]
    rows += [_row(src="prospects-20260724-backlog-enrich-run6-hubspot.csv") for _ in range(2)]
    rates = source_hit_rates(rows)
    assert list(rates) == ["backlog-enrich"]
    assert rates["backlog-enrich"]["rows"] == 5


def test_source_hit_rates_separate_a_good_run_from_a_bad_one():
    rows = [_row(src="backlog-enrich") for _ in range(10)]
    rows += [_row(title="Director of pharmacy", src="vibebulk") for _ in range(7)]
    rows += [_row(src="vibebulk") for _ in range(3)]
    rates = source_hit_rates(rows)
    assert rates["backlog-enrich"]["hit_rate"] == 1.0
    assert rates["vibebulk"]["hit_rate"] == 0.3


# --- the audit -----------------------------------------------------------


def test_clean_list_passes():
    rows = [
        _row(why_now="their agents write into regulated systems", src="backlog-enrich")
        for _ in range(30)
    ]
    a = audit_rows(rows, as_of=AS_OF)
    assert not a.failed, a.findings
    assert a.role[RoleFit.IN] == 30
    assert a.signal_usable == 30


def test_audit_flags_wrong_seats_without_dropping_them():
    rows = [_row(why_now="runs agents in production") for _ in range(9)]
    rows.append(_row(title="Director of pharmacy", why_now="runs agents in production"))
    a = audit_rows(rows, as_of=AS_OF)
    assert a.failed
    assert any("role-fit" in f and "wrong seat" in f for f in a.findings)
    assert any("do not auto-delete" in f for f in a.findings)
    assert a.rows == 10  # nothing removed


def test_audit_detects_an_inverted_tier_column():
    """The 2026-08-11 defect: the labelled tiers ranked backwards against the untiered rows."""
    rows = [_row(title="Director of pharmacy", tier="A") for _ in range(12)]
    rows += [_row(title="Director of talent acquisition", tier="B") for _ in range(12)]
    rows += [_row(tier="") for _ in range(12)]  # untiered, all good seats
    a = audit_rows(rows, as_of=AS_OF)
    assert any("tier-inverted" in f for f in a.findings), a.findings
    assert a.tier_role["untiered"]["hit_rate"] == 1.0
    assert a.tier_role["A"]["hit_rate"] == 0.0


def test_audit_detects_a_tier_column_that_predicts_nothing():
    """Tiers that all score the same are measuring something other than fit."""
    rows = [_row(tier="A") for _ in range(10)] + [_row(tier="B") for _ in range(10)]
    a = audit_rows(rows, as_of=AS_OF)
    assert any("tier-meaningless" in f for f in a.findings), a.findings


def test_audit_flags_a_low_yield_source_run():
    rows = [_row(title="Director of pharmacy", src="vibebulk") for _ in range(20)]
    rows += [_row(src="vibebulk") for _ in range(10)]
    a = audit_rows(rows, as_of=AS_OF)
    assert any("source-quality" in f and "vibebulk" in f for f in a.findings), a.findings


def test_audit_ignores_a_small_source_run():
    """A handful of rows is not evidence about a sourcing method."""
    rows = [_row(title="Director of pharmacy", src="tiny") for _ in range(5)]
    rows += [_row(src="backlog-enrich") for _ in range(30)]
    a = audit_rows(rows, as_of=AS_OF)
    assert not any("source-quality" in f for f in a.findings), a.findings


def test_audit_flags_intent_scores_and_stale_signals_separately():
    rows = [
        _row(why_now="machine learning & artificial intelligence (intent score 93)")
        for _ in range(4)
    ]
    rows += [_row(why_now="Series A closed 2024-01-15") for _ in range(3)]
    a = audit_rows(rows, as_of=AS_OF)
    assert any("signal-intent-only" in f for f in a.findings), a.findings
    assert any("signal-stale" in f for f in a.findings), a.findings
    assert a.signal_usable == 0


def test_the_2026_08_11_shape_fails_the_gate():
    """End-to-end regression for the list that passed every other gate and was half mis-aimed.

    Mirrors the real distribution -- a big low-yield bulk run, a small perfect one, an inverted
    tier column, and research that is mostly intent scores -- with invented identities.
    """
    rows = []
    for i in range(200):  # bulk run, ~35% usable seats
        good = i % 3 == 0
        rows.append(
            _row(
                title="Chief Technology Officer" if good else "Director of talent acquisition",
                src="vibebulk",
                tier="A" if i % 2 else "B",
                why_now="machine learning & artificial intelligence (intent score 80)",
            )
        )
    for _ in range(69):  # targeted run, all usable
        rows.append(
            _row(
                src="backlog-enrich",
                tier="",
                why_now="their platform orchestrates agents across customer systems",
            )
        )

    a = audit_rows(rows, as_of=AS_OF)
    assert a.failed
    kinds = " ".join(a.findings)
    assert "source-quality" in kinds
    assert "signal-intent-only" in kinds
    assert "tier-inverted" in kinds or "tier-meaningless" in kinds
    # the untiered rows are the good ones -- the finding that started all of this
    assert a.tier_role["untiered"]["hit_rate"] > a.tier_role["A"]["hit_rate"]


# --- budget + one suppression semantics (B4 / P2.4) ------------------------- #


def _csv_of(tmp_path, rows, cols=None):
    p = tmp_path / "list.csv"
    cols = cols or ["email", "first", "last", "company_domain", "title", "src", "suppression"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return p


def test_suppressed_rows_are_skipped_by_default(tmp_path, capsys):
    """Default = consult suppression. The three gates used to disagree about this."""
    p = _csv_of(
        tmp_path,
        [
            {
                "email": "a@acme.example",
                "title": "Director of catering",
                "src": "run-1",
                "suppression": "dnc-optout",
            },
            {"email": "b@acme.example", "title": "Director of catering", "src": "run-1"},
        ],
    )
    lf.main(["--csv", str(p), "--warn-only"])
    out = capsys.readouterr().out
    assert "skipped 1 row(s)" in out
    assert "1 row(s)" in out


def test_include_suppressed_opts_back_in(tmp_path, capsys):
    p = _csv_of(
        tmp_path,
        [
            {
                "email": "a@acme.example",
                "title": "Director of catering",
                "src": "run-1",
                "suppression": "dnc-optout",
            }
        ],
    )
    lf.main(["--csv", str(p), "--warn-only", "--include-suppressed"])
    assert "skipped" not in capsys.readouterr().out


def test_the_deprecated_flag_still_parses(tmp_path):
    p = _csv_of(tmp_path, [{"email": "a@acme.example", "title": "CTO", "src": "run-1"}])
    assert lf.main(["--csv", str(p), "--warn-only", "--skip-suppressed"]) == 0


def test_findings_past_the_budget_stop_enumerating(tmp_path):
    rows = [
        {"email": f"p{i}@acme.example", "title": "Director of catering", "src": f"run-{i}"}
        for i in range(40)
    ]
    a = lf.audit_rows(rows, budget=2)
    if a.findings:
        v = a.verdict
        assert v.blocked or v.enumerable
        if v.blocked:
            assert "Enumeration suppressed" in lf.render(a)


def test_acking_a_class_clears_it_from_the_budget(tmp_path):
    rows = [
        {"email": f"p{i}@acme.example", "title": "Director of catering", "src": f"run-{i}"}
        for i in range(40)
    ]
    a = lf.audit_rows(rows, budget=1)
    classes = [c.rule for c in a.verdict.classes]
    if classes:
        acked = lf.audit_rows(rows, budget=1, acked=tuple(classes))
        assert acked.verdict.unacked == 0
