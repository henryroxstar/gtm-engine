"""Contract test — research is told what makes a fact usable, at the point it is found.

Both checks the `prospect` skill now states in Step 6/7 are enforced far downstream, by which
time the research spend is unrecoverable. Measured on the live pool 2026-08-29: 74% of rows
carrying a researched fact reduced to a clause that could not ship, and 40% of the ones that
could attested a premise no argument may rest on. Neither was written down where the research
happens, so neither was checkable by the person doing it.

This is the drift guard. If a future edit strips the clause contract or the premise test, that
fails here rather than resurfacing as a batch of unusable rows after the money is spent.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "plugin" / "skills" / "prospect"


def _bodies() -> list[str]:
    """Both the source template and the generated SKILL.md — the skill is read as SKILL.md."""
    return [
        (SKILL_DIR / name).read_text(encoding="utf-8") for name in ("body_template.md", "SKILL.md")
    ]


def test_the_sendable_clause_contract_is_stated_at_research_time():
    for body in _bodies():
        assert "SENDABLE" in body
        assert "No digits at all" in body, "the digit ban is the single biggest clause loss"
        assert "verbatim reduction" in body, "reduction-not-rewording must be stated"
        assert "signal-off-topic" in body, "a clause must carry agent content"


def test_the_sellable_premise_test_names_the_disqualified_premise():
    for body in _bodies():
        assert "SELLABLE" in body
        assert "premise-vocab.toml" in body
        assert "ships-agent-product" in body
        assert "is a FILTER, never a spec premise" in body, (
            "the skill must say ships-agent-product is filter-only — proposing a spec on it "
            "is work that was already tested and refuted (2026-08-25, 3 send / 47 drop)"
        )


def test_the_clause_bounds_are_referenced_not_restated():
    """The length rule names the CONSTANTS, never the numbers.

    A doc that restates `12-110` goes stale silently the moment the constant moves, and this
    repo has already made that mistake four times over with linter rule counts. Naming
    `SIGNAL_MIN_CHARS`/`SIGNAL_MAX_CHARS` cannot drift.
    """
    from gtm_core.merge_hygiene import SIGNAL_MAX_CHARS, SIGNAL_MIN_CHARS

    for body in _bodies():
        assert "SIGNAL_MIN_CHARS" in body and "SIGNAL_MAX_CHARS" in body
        assert f"{SIGNAL_MIN_CHARS}-{SIGNAL_MAX_CHARS}" not in body, (
            "hardcoded bounds drift from gtm_core.merge_hygiene — name the constants instead"
        )


def test_a_correction_is_routed_to_the_account_not_a_pooled_csv():
    """The write that loses data is the obvious one, so the skill has to name the other one."""
    import gtm_core.signal_backfill as sb

    for body in _bodies():
        assert "--promote" in body
        assert "signal_backfill" in body
    # Drift guard in the other direction: the flag the skill advertises must exist.
    assert "--promote" in sb.main.__doc__ if sb.main.__doc__ else True
    assert hasattr(sb, "promote_records"), "the skill names --promote; the module must provide it"
    assert hasattr(sb, "refuse_derived_target"), "aiming --out at a pooled CSV must be refused"


def test_a_dropped_candidate_is_recorded_as_a_verdict_not_as_why_now_prose():
    """`why_now` is what every downstream count reads as "this account has a signal".

    Measured 2026-08-30: 545 accounts carried a `why_now` asserting no signal was found
    (feed-only hits, "no dated public article found this pass"), and 541 of those had a
    blank `verdict` — the column that exists to represent exactly that refusal. 452 sat at
    status `new`, indistinguishable from researched accounts, so the pool read as 99%
    `why_now` coverage and the remaining work was sized off a number that was 43% wrong.
    """
    for body in _bodies():
        assert "never a sentence in `why_now`" in body.lower() or (
            "A dropped candidate is a VERDICT" in body
        ), "Step 6 must say an empty sweep is recorded as a verdict, not as why_now prose"
        assert "Never write the negative result" in body
        assert "541" in body, "the rule must carry the number that earned it"


def test_a_step_six_drop_also_carries_a_verdict():
    """The structural hole: Step 8's verdict applied to "each finalist" only, and a
    candidate the Step 6 sweep dropped never becomes a finalist — so nothing assigned it
    one and the negative result went into free text instead."""
    for body in _bodies():
        assert '"Finalist" is not the only row that gets one.' in body
        assert "for any\nreason" in body or "for any reason" in body


def test_existing_research_is_checked_before_the_sweep_is_run():
    """A dossier already on disk usually carries the source/date/span the six fields want.
    Of 724 substantive-but-unsourced rows measured 2026-08-30, 321 already had one."""
    for body in _bodies():
        assert "accounts-needing-dossier" in body
        assert "Before you sweep" in body
        assert "321" in body, "the backfill-vs-research split must carry its measurement"
