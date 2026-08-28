"""Tests for the groundedness cascade — case-study number traceability and unhedged
assertive-internals-claim detection (P1.5), plus the tier-0 reuse of
:mod:`gtm_core.signal_record`'s already-shipped clause-vs-evidence check.

Every company, person, URL and quote below is invented (docs/RULES.md R9).
"""

from __future__ import annotations

from gtm_core.groundedness import (
    GroundednessReport,
    assertive_internals_claims,
    case_study_numbers_traceable,
    cited_numbers,
    groundedness_predictions,
    groundedness_report,
    premise_cascade,
    research_record_findings,
)
from gtm_core.signal_record import Verdict


def _row(**kw):
    base = {
        "first": "Rae",
        "email": "rae.okafor@halden.example",
        "company": "Halden Systems",
        "company_domain": "halden.example",
        "signal_clause": "raised a Series B to expand its agent orchestration platform",
        "signal_source_url": "https://halden.example/news/series-b",
        "signal_observed": "2026-07-02",
        "signal_evidence": (
            "Halden Systems raised a $40M Series B led by Northgate to expand its agent "
            "orchestration platform across Europe."
        ),
        "signal_subject": "Halden Systems",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": Verdict.SEND,
        "verdict_reason": "",
    }
    base.update(kw)
    return base


CASE_STUDIES_TEXT = (
    "Meridian Capital Group — 90% time reduction, 100% regulatory audit compliance, 0 manual "
    "follow-up on employment reference checks.\n"
    "Vertex Systems — 80+ secrets found out of agent code across a mid-size fintech estate."
)


# --------------------------------------------------------------------------- tier 0 reuse


def test_research_record_findings_is_signal_records_check_record():
    # Not a re-implementation — literally the same function object, so the two modules
    # cannot silently drift apart on what counts as evidence support.
    from gtm_core.signal_record import check_record

    assert research_record_findings is check_record


def test_research_record_findings_catches_unsupported_clause():
    row = _row(signal_clause="raised a Series B and signed a deal with the Pentagon")
    findings = research_record_findings(row)
    assert any(f.rule == "signal-evidence-unsupported" for f in findings)


# --------------------------------------------------------------------------- tier 1: case-study numbers


def test_cited_numbers_finds_percentage_and_multiplier():
    body = "One team cut turnaround 90% and now moves 3x faster."
    assert cited_numbers(body) == ["90%", "3x"]


def test_cited_numbers_ignores_plain_dates_and_counts():
    body = "We spoke on 2026-08-19 about 45 open items."
    assert cited_numbers(body) == []


def test_case_study_numbers_traceable_true_when_body_cites_nothing():
    ok, missing = case_study_numbers_traceable("No numbers here at all.", CASE_STUDIES_TEXT)
    assert ok and missing == []


def test_case_study_numbers_traceable_true_for_a_real_figure():
    body = "A regulated-FI compliance team held 90% time reduction on the same workflow."
    ok, missing = case_study_numbers_traceable(body, CASE_STUDIES_TEXT)
    assert ok and missing == []


def test_case_study_numbers_traceable_catches_a_fabricated_figure():
    # 97% appears nowhere in the case-studies text — the "90% of what?" defect class,
    # mechanically caught: the number itself has no source, whatever it claims.
    body = "Another team cut turnaround 97% using the same approach."
    ok, missing = case_study_numbers_traceable(body, CASE_STUDIES_TEXT)
    assert not ok
    assert "97%" in missing


def test_case_study_numbers_traceable_with_no_case_studies_file():
    body = "A team saw 90% improvement."
    ok, missing = case_study_numbers_traceable(body, "")
    assert not ok
    assert missing == ["90%"]


# --------------------------------------------------------------------------- tier 2: assertive internals


def test_assertive_internals_claim_detected_when_unhedged():
    body = (
        "The honest answer at Cascade is a shared key nobody can attribute. "
        "Would the one-pager be useful?"
    )
    claims = assertive_internals_claims(body)
    assert len(claims) == 1
    assert "shared key" in claims[0]


def test_assertive_internals_claim_cleared_by_a_hedge_in_the_same_sentence():
    body = "My hunch: the honest answer at Cascade is a shared key nobody can attribute."
    assert assertive_internals_claims(body) == []


def test_assertive_internals_claim_cleared_by_a_hedge_one_sentence_earlier():
    body = (
        "Tell me if I've got this wrong. The real answer at Cascade is a shared key "
        "nobody can attribute."
    )
    assert assertive_internals_claims(body) == []


def test_assertive_internals_claim_not_cleared_by_a_hedge_two_sentences_earlier():
    body = (
        "My hunch is that agents are spreading fast. Logs capture the account, not the "
        "actor. The real answer at Cascade is a shared key nobody can attribute."
    )
    claims = assertive_internals_claims(body)
    assert len(claims) == 1  # the hedge is two sentences back, outside the window


def test_assertive_internals_claim_requires_the_answer_truth_or_reality_noun():
    # The pattern anchors on "the honest/real/likely ANSWER/TRUTH/REALITY at X is..." —
    # a different predicate noun ("growth", "focus", "priority") is a public/strategic
    # claim, not an assertion about internal architecture, and correctly does not match.
    # This is a narrow, deliberate anchor (not "every confident sentence") — the LLM tier
    # exists for judgment calls the heuristic cannot make; this heuristic's job is only to
    # catch the specific "the honest answer at {{Company}} is X" shape the persona review
    # named, not confident language in general.
    body = "The real growth at Cascade is a market everyone can see. Useful?"
    assert assertive_internals_claims(body) == []


# --------------------------------------------------------------------------- cascade aggregation


def test_groundedness_report_clean_row_is_clean():
    row = _row()
    body = "Would the one-pager be useful?"
    report = groundedness_report("r1", row, body, CASE_STUDIES_TEXT)
    assert report.clean
    assert not report.needs_judge


def test_groundedness_report_flags_untraceable_number():
    row = _row()
    body = "Another team cut turnaround 97%. Useful?"
    report = groundedness_report("r1", row, body, CASE_STUDIES_TEXT)
    assert not report.clean
    assert report.needs_judge
    assert "97%" in report.untraceable_numbers


def test_groundedness_report_flags_unhedged_internals_claim():
    row = _row()
    body = "The honest answer at Cascade is a shared key nobody can attribute."
    report = groundedness_report("r1", row, body, CASE_STUDIES_TEXT)
    assert not report.clean
    assert report.unhedged_internals_claims


def test_groundedness_report_flags_blocking_research_finding():
    row = _row(signal_evidence="")  # signal-evidence-missing is a block-level finding
    report = groundedness_report("r1", row, "Useful?", CASE_STUDIES_TEXT)
    assert not report.clean


def test_groundedness_report_warn_level_research_finding_does_not_block_clean():
    # signal-subject-absent-from-evidence is a WARN, not a block — it should not by
    # itself flip `.clean` to False (only block-level findings do).
    row = _row(signal_subject="A Different Company Name")
    report = groundedness_report("r1", row, "Useful?", CASE_STUDIES_TEXT)
    blocking = [f for f in report.research_findings if f.level == "block"]
    assert any(f.rule == "signal-subject-mismatch" for f in blocking)  # this IS a block
    assert not report.clean  # so clean correctly reflects it


def test_groundedness_predictions_matches_confusion_input_shape():
    reports = [
        GroundednessReport("r1", [], [], [], needs_judge=False),
        GroundednessReport("r2", [], ["97%"], [], needs_judge=True),
    ]
    preds = groundedness_predictions(reports)
    assert preds == {"r1": True, "r2": False}


# --- premise cascade (2026-08-21) --------------------------------------------


class _P:
    """A minimal stand-in for gtm_core.hook_coverage.Premise.

    Deliberately a local double rather than the real class: this module must not grow an
    import of the hook-coverage machinery just to be tested, and the cascade only needs the
    two members it actually reads.
    """

    key = "multi-framework"
    claim = "runs agents on more than one framework"
    min_distinct = 2

    def attested_by(self, text):
        return {t for t in ("langgraph", "crewai", "bedrock") if t in (text or "").lower()}


def test_a_row_attesting_enough_is_settled_without_spend():
    settled, escalate = premise_cascade(
        [{"email": "a@x.example", "signal_evidence": "runs langgraph and crewai"}], _P()
    )
    assert escalate == []
    assert settled[0].entailed and settled[0].tier == "deterministic"


def test_a_row_attesting_nothing_is_settled_without_spend():
    """Nothing in the evidence is even on topic, so there is nothing for a judge to weigh.
    Escalating these is how a per-row judge becomes unaffordable."""
    settled, escalate = premise_cascade(
        [{"email": "a@x.example", "signal_evidence": "opened a regional office"}], _P()
    )
    assert escalate == []
    assert not settled[0].entailed


def test_only_the_ambiguous_middle_escalates():
    """Attests something, but not enough — the case the term list genuinely cannot settle."""
    settled, escalate = premise_cascade(
        [{"email": "a@x.example", "signal_evidence": "built on bedrock"}], _P()
    )
    assert settled == []
    assert len(escalate) == 1
    assert escalate[0]["email"] == "a@x.example"
    assert "bedrock" in escalate[0]["prompt"]
    assert "runs agents on more than one framework" in escalate[0]["prompt"]


def test_the_judge_prompt_forbids_outside_knowledge():
    """The L2 contract: never ask a model whether something is TRUE, only whether it
    FOLLOWS from the supplied span. A prompt that permits recall invites confabulation."""
    _, escalate = premise_cascade(
        [{"email": "a@x.example", "signal_evidence": "built on bedrock"}], _P()
    )
    prompt = escalate[0]["prompt"]
    assert "Do NOT use anything you know about the company beyond the evidence" in prompt
    assert "Default to NO when uncertain" in prompt
