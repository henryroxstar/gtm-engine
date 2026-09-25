"""Tests for the research record behind a prospect row.

Every company, person, URL and quote below is invented (docs/RULES.md R9). The defect
*shapes* are real ones that survived three rounds of human email review in August 2026
and were only caught by reading — which is the argument for making them type errors:

* a clause about company A opening an email to company B;
* a number that drifted across three documents until it described a mechanism its
  source never measured;
* "agents" meaning insurance agents, read as an AI-agent buying signal;
* an account on the competitor watchlist reached with a cold pitch anyway.
"""

from __future__ import annotations

import datetime

import pytest

from gtm_core.signal_record import (
    RECORD_COLUMNS,
    Verdict,
    audit_records,
    check_record,
    claim_numbers,
    content_tokens,
    evidence_supports,
    has_record_columns,
    missing_record_columns,
    normalise_company,
)

AS_OF = datetime.date(2026, 8, 19)

_EVIDENCE = (
    "Halden Systems raised a $40M Series B led by Fernway Ventures to expand its agent "
    "orchestration platform across Europe."
)


def _row(**kw):
    base = {
        "first": "Rae",
        "email": "rae.okafor@halden.example",
        "company": "Halden Systems",
        "company_domain": "halden.example",
        "signal_clause": "raised a Series B to expand its agent orchestration platform",
        "signal_source_url": "https://halden.example/news/series-b",
        "signal_observed": "2026-07-02",
        "signal_evidence": _EVIDENCE,
        "signal_subject": "Halden Systems",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": Verdict.SEND,
        "verdict_reason": "",
    }
    base.update(kw)
    return base


def _rules(findings):
    return {f.rule for f in findings}


# --- the clean case ------------------------------------------------------


def test_a_complete_record_produces_no_findings():
    assert check_record(_row(), as_of=AS_OF) == []


def test_a_generic_row_needs_a_verdict_but_no_provenance():
    """No clause means no dated claim, so there is nothing for a source to be a source
    OF. Requiring one is how a fail-closed gate turns into noise."""
    row = _row(signal_clause="", signal_source_url="", signal_observed="", signal_evidence="")
    assert check_record(row, as_of=AS_OF) == []


# --- signal_clause is derived, never stored (PS3, 2026-09-10) ------------
#
# `signal_clause` is not one of `ready-to-load.csv`'s 41 stored columns — it is
# derived-only, so it is blank on every pooled row. Before this fell back to deriving
# it from `why_now`, EVERY row on a real list took the "no clause" exit above and
# skipped every source/date/evidence/subject/agent-kind check, whatever `why_now`
# actually said.


def test_why_now_without_a_signal_clause_column_still_triggers_provenance_checks():
    """A row with no `signal_clause` value but a `why_now` that reduces to a real,
    unsourced claim must not take the "nothing to source" exit — the fallback
    derivation is what makes the checks run at all on a real pooled row."""
    row = _row(
        signal_clause="",
        why_now="Halden Systems opened a new AI safety office",
        signal_source_url="",
        signal_observed="",
        signal_evidence="",
    )
    findings = check_record(row, as_of=AS_OF)
    rules = _rules(findings)
    assert "signal-source-missing" in rules
    assert "signal-observed-missing" in rules
    assert "signal-evidence-missing" in rules


def test_a_sourced_why_now_with_no_signal_clause_column_produces_no_findings():
    """Negative control for the derivation above: a `why_now` that reduces to a clause
    the row's OWN source/date/evidence genuinely support must clear the record — this
    proves the fallback derives the right clause, not merely that any `why_now` at all
    now fires a finding."""
    row = _row(
        signal_clause="",
        why_now="raised a Series B to expand its agent orchestration platform",
    )
    assert check_record(row, as_of=AS_OF) == []


def test_research_note_why_now_triggers_provenance_and_underivable_checks():
    """PS-R I3: A row with a research-note why_now that does not reduce to a signal clause
    must NOT take the 'nothing to source' exit — it must fire signal-clause-underivable
    and require source/date/evidence provenance."""
    row = _row(
        signal_clause="",
        why_now="enterprise automation platform (intent score 81)",
        signal_source_url="",
        signal_observed="",
        signal_evidence="",
    )
    findings = check_record(row, as_of=AS_OF)
    rules = _rules(findings)
    assert "signal-clause-underivable" in rules
    assert "signal-source-missing" in rules
    assert "signal-observed-missing" in rules
    assert "signal-evidence-missing" in rules


# --- evidence support ----------------------------------------------------


def test_content_tokens_drops_short_and_stopword_tokens():
    assert content_tokens("it has been about the agent platform") == ["agent", "platform"]


def test_claim_numbers_normalises_thousands_separators():
    assert claim_numbers("1,200 agents and 90 percent") == {"1200", "90"}


def test_evidence_supports_an_honest_reduction_of_its_own_source():
    ok, unsupported, unsourced = evidence_supports(
        "raised a Series B to expand its agent orchestration platform", _EVIDENCE
    )
    assert ok and not unsupported and not unsourced


def test_evidence_tolerates_inflection_but_not_substitution():
    """ "verification"/"verified" is one claim; "examiner"/"reference" is two."""
    ok, _, _ = evidence_supports(
        "verification turnaround improved", "the team verified turnaround improvements"
    )
    assert ok


def test_evidence_catches_a_clause_that_changed_what_the_number_measures():
    """The real drift: a source measured reference-check turnaround; three documents
    later the same 90 percent described examiner verification. Every hop plausible,
    the endpoint false — and invisible to any check that never saw the source."""
    ok, unsupported, _ = evidence_supports(
        "cut examiner verification time 90 percent",
        "the platform cut reference-check turnaround by 90 percent for its hiring team",
    )
    assert not ok
    assert "examiner" in unsupported


def test_evidence_catches_a_fabricated_number_with_no_threshold():
    """Word support is a share; number provenance is absolute. A number the source
    does not contain is invented, however plausible the sentence around it."""
    ok, _, unsourced = evidence_supports(
        "deployed 80 autonomous agents across claims",
        "Halden Systems deployed autonomous agents across its claims workflow",
    )
    assert not ok
    assert unsourced == {"80"}


def test_missing_evidence_blocks():
    assert "signal-evidence-missing" in _rules(check_record(_row(signal_evidence=""), as_of=AS_OF))


def test_unsupported_clause_blocks():
    row = _row(signal_clause="cut examiner verification time by half after the merger")
    assert "signal-evidence-unsupported" in _rules(check_record(row, as_of=AS_OF))


# --- source URL ----------------------------------------------------------


@pytest.mark.parametrize(
    "url,rule",
    [
        ("", "signal-source-missing"),
        ("halden.example/news", "signal-source-malformed"),
        ("http://halden.example/news", "signal-source-malformed"),
        ("https://www.google.com/search?q=halden+series+b", "signal-source-is-search"),
        ("https://news.google.com/search?q=halden", "signal-source-is-search"),
    ],
)
def test_source_url_shapes_that_cannot_carry_a_fact(url, rule):
    assert rule in _rules(check_record(_row(signal_source_url=url), as_of=AS_OF))


# --- observed date -------------------------------------------------------


def test_observed_date_must_be_iso():
    assert "signal-observed-missing" in _rules(
        check_record(_row(signal_observed="July 2026"), as_of=AS_OF)
    )


def test_stale_signal_blocks():
    """Freshness lives on the record, not in the clause. The clause contract forbids
    dates, which is why this was unenforceable until the record existed."""
    rules = _rules(check_record(_row(signal_observed="2025-06-01"), as_of=AS_OF))
    assert "signal-stale" in rules


def test_future_observed_date_blocks():
    assert "signal-observed-future" in _rules(
        check_record(_row(signal_observed="2026-12-01"), as_of=AS_OF)
    )


# --- subject -------------------------------------------------------------


def test_normalise_company_ignores_legal_suffixes_and_punctuation():
    assert normalise_company("Halden Systems, Inc.") == normalise_company("Halden Systems")


def test_subject_mismatch_blocks():
    """The fact is about the investor; the email goes to the portfolio company."""
    rules = _rules(check_record(_row(signal_subject="Fernway Ventures"), as_of=AS_OF))
    assert "signal-subject-mismatch" in rules


def test_subject_absent_from_evidence_is_advisory():
    row = _row(
        company="Meridian Rail",
        signal_subject="Meridian Rail",
        signal_clause="raised a Series B to expand its agent orchestration platform",
    )
    findings = check_record(row, as_of=AS_OF)
    absent = [f for f in findings if f.rule == "signal-subject-absent-from-evidence"]
    assert absent and absent[0].level == "warn"


# PH16: the subject is the row's full company name; a source names the same company the way
# people say it. Conservative by design (a gate that passes a wrong-company row is worse than
# noise): a one-word form counts only as a possessive or before a capitalised product name,
# and any form followed by a qualifier the subject lacks is a namesake.
@pytest.mark.parametrize(
    "subject,evidence",
    [
        ("Brightpath Health", "He will lead Brightpath's efforts to use AI for patients."),
        ("Brightpath Health", "He will lead Brightpath’s efforts to use AI for patients."),
        ("Northwind AI", "Northwind's platform powers the clinical agents."),
        ("Marlowe Healthcare", "The site was purchased by Marlowe Hospital last month."),
        ("Riverbend Systems", "Riverbend ClaimsDesk is an agentic compliance assistant."),
        ("Eastvale Card Centre", "Eastvale's collaboration with the network centres on agents."),
        ("R.J. Halden Companies", "R.J. Halden announced today it advised on the sale."),
        ("The Lantern Group", "Lantern's rollout reaches every branch."),
        ("Quillon Companies", "Quillon Companies announced an agent pilot."),
        # A one-word name is the row's own name, not a shortening of it.
        ("Wideloop", "Wideloop announced an agent pilot."),
        # ACCEPTED RESIDUAL (2026-09-25), pinned so tightening it is deliberate: a one-word
        # full name that is also a dictionary word passes on a sentence-initial use.
        ("Wideloop", "Wideloop of adoption rose across the sector."),
        # The full name's own legal form, and a qualifier that belongs to the next list item.
        ("Halden", "The platform company Halden Inc. today unveiled agent tooling."),
        ("Bank of Lantern", "Six banks -- Bank of Lantern, Capital Quarry and others -- joined."),
    ],
)
def test_subject_present_in_evidence_under_a_shorter_form(subject, evidence):
    row = _row(company=subject, signal_subject=subject, signal_evidence=evidence)
    assert "signal-subject-absent-from-evidence" not in _rules(check_record(row, as_of=AS_OF))


@pytest.mark.parametrize(
    "subject,evidence",
    [
        # First-person quote naming no company: nothing ties the fact to the subject.
        ("Brightpath Health", "AI allows us to simplify workflows and give caregivers time."),
        # A namesake: the core followed by a qualifier the subject does not carry.
        ("Cascade Health", "Cascade Insurance announced an agentic intake pilot."),
        ("Quarry Financial", "Quarry plc announced an agentic intake pilot."),
        ("Brightpath Health", "Brightpath Healthcare announced an agentic intake pilot."),
        # A namesake on a domain-style name.
        ("Wavelet Card", "Wavelet.example launched agents for retail banking."),
        ("Wideloop", "WIDELOOP.AI launched agents for retail banking."),
        # A bare one-word form: a sentence-initial dictionary word, or a same-named stranger.
        ("Marlowe HealthCare", "The Marlowe said it would expand its agent programme."),
        ("Lantern Financial", "Lantern, the bank said, will pilot agents next year."),
        ("Quillon AI", "Quillon of adoption rose across the sector."),
        ("Cascade Health", "Cascade of alerts overwhelmed the operations team."),
        ("Riverbend Systems", "Riverbend announced an agent pilot."),
        ("Marlowe HealthCare", "Marlowe, Wideloop and Quarry announced an agent pilot."),
        # Lowercase, and part of a longer word, never count.
        ("First Eastvale Financial", "It was the first quarter of net interest growth."),
        ("Lantern AI", "Lanterns lined the route of the product launch."),
        ("Quarry", "The research team mines quarry data daily."),
        ("The Lantern Group", "The group said the rollout reaches every branch."),
    ],
)
def test_subject_absent_still_warns_when_no_form_of_it_is_named(subject, evidence):
    row = _row(company=subject, signal_subject=subject, signal_evidence=evidence)
    assert "signal-subject-absent-from-evidence" in _rules(check_record(row, as_of=AS_OF))


@pytest.mark.parametrize(
    "subject,company",
    [
        ("Quillon", "Quillon Financial"),
        ("Eastvale", "EastVale AI"),
        ("Wideloop", "WIDELOOP.AI"),
        ("Wavelet", "Wavelet Card"),
        ("Marlowe", "Marlowe HealthCare"),
        ("Quarry", "Quarry Financial"),
        ("Lantern", "Lantern Financial"),
    ],
)
def test_a_short_form_subject_is_surfaced_not_silenced(subject, company):
    """The subject is the company's core: usually the same company, sometimes a namesake.
    Not an ERROR (it is the likely case) and never silent (it is not always true)."""
    findings = check_record(_row(company=company, signal_subject=subject), as_of=AS_OF)
    short = [f for f in findings if f.rule == "signal-subject-short-form"]
    assert short and short[0].level == "warn"
    assert "signal-subject-mismatch" not in _rules(findings)


@pytest.mark.parametrize(
    "subject,company",
    [
        # Same leading word, different descriptor: neither is the other's short form.
        ("Quillon Health", "Quillon Financial"),
        # The subject is the LONGER form: the email goes to a name the fact is not about.
        ("Quillon Financial", "Quillon"),
        ("Fernway Ventures", "Halden Systems"),
    ],
)
def test_subject_sharing_only_a_leading_word_is_still_a_mismatch(subject, company):
    row = _row(company=company, signal_subject=subject)
    assert "signal-subject-mismatch" in _rules(check_record(row, as_of=AS_OF))


# --- agent homonym -------------------------------------------------------


def test_human_agents_block():
    """An insurance carrier's "agents" are people. A regex sees a buying signal; so
    does a hurried reader. The record makes research say which it is."""
    rules = _rules(check_record(_row(signal_agent_kind="human"), as_of=AS_OF))
    assert "agent-kind-human" in rules


def test_unresolved_agent_kind_blocks():
    for value in ("", "unclear"):
        assert "agent-kind-unresolved" in _rules(
            check_record(_row(signal_agent_kind=value), as_of=AS_OF)
        )


def test_agent_kind_none_contradicting_the_clause_blocks():
    rules = _rules(check_record(_row(signal_agent_kind="none"), as_of=AS_OF))
    assert "agent-kind-contradiction" in rules


def test_agent_kind_none_is_fine_when_the_clause_never_says_agent():
    """A funding round or a leadership hire is a legitimate signal with no agents in
    it at all — the common case, and it must not be a finding."""
    row = _row(
        signal_clause="raised a Series B led by Fernway Ventures",
        signal_evidence=_EVIDENCE,
        signal_agent_kind="none",
    )
    assert check_record(row, as_of=AS_OF) == []


# --- relation ------------------------------------------------------------


def test_competitor_relation_blocks():
    rules = _rules(check_record(_row(category_relation="competitor"), as_of=AS_OF))
    assert "relation-competitor" in rules


def test_unresolved_relation_blocks():
    for value in ("", "unclear"):
        assert "relation-unresolved" in _rules(
            check_record(_row(category_relation=value), as_of=AS_OF)
        )


@pytest.mark.parametrize(
    "value,rule", [("partner", "relation-partner"), ("adjacent", "relation-adjacent")]
)
def test_partner_and_adjacent_are_advisory(value, rule):
    findings = check_record(_row(category_relation=value), as_of=AS_OF)
    match = [f for f in findings if f.rule == rule]
    assert match and match[0].level == "warn"


# --- verdict -------------------------------------------------------------


def test_missing_verdict_blocks():
    """ "Not sendable" has to be representable, or a research step asked for 442
    emails produces 442."""
    assert "verdict-missing" in _rules(check_record(_row(verdict=""), as_of=AS_OF))


def test_unknown_verdict_blocks():
    assert "verdict-unknown" in _rules(check_record(_row(verdict="maybe"), as_of=AS_OF))


def test_non_send_verdict_requires_a_reason():
    rules = _rules(check_record(_row(verdict=Verdict.DROP, verdict_reason=""), as_of=AS_OF))
    assert "verdict-reason-missing" in rules


def test_a_reasoned_drop_is_a_clean_record():
    row = _row(verdict=Verdict.REANGLE, verdict_reason="clause is about the investor, not them")
    assert check_record(row, as_of=AS_OF) == []


# --- file-level ----------------------------------------------------------


def test_has_record_columns():
    assert has_record_columns(list(RECORD_COLUMNS))
    assert not has_record_columns(["first", "email", "company"])
    assert missing_record_columns(["verdict"]) == [c for c in RECORD_COLUMNS if c != "verdict"]


def test_a_pre_record_list_produces_one_file_level_finding_not_one_per_row():
    """A gate that answers "your list is from last week" with 496 identical errors is
    the same unreadable-output failure the budget exists to stop."""
    rows = [{"first": "Rae", "email": f"r{i}@halden.example"} for i in range(496)]
    a = audit_records(rows, ["first", "email"])
    assert a.failed
    assert a.missing_columns == list(RECORD_COLUMNS)
    assert a.errors == []
    assert a.checked == 0


def test_audit_records_reports_per_row_once_the_columns_exist():
    rows = [_row(), _row(email="b@halden.example", signal_subject="Fernway Ventures")]
    a = audit_records(rows, list(rows[0]), as_of=AS_OF)
    assert a.checked == 2
    assert any("signal-subject-mismatch" in e for e in a.errors)
    assert len([e for e in a.errors if "signal-subject-mismatch" in e]) == 1


# --- regulator relation (2026-08-21) -----------------------------------------


def test_a_regulator_account_blocks():
    """The class that had no word for itself.

    A supervisory body was researched, recorded `prospect` (the only value that fit),
    given `verdict: send`, and staged — on a clause describing that regulator's OWN
    published safeguards, under a body arguing that governance unlocks a commercial deal.
    Nothing objected, because a fail-closed field cannot fail closed on a value the
    vocabulary cannot express. Same fix as `Verdict` itself: name it, then check it.
    """
    row = _row(category_relation="regulator")
    findings = check_record(row)
    hit = [f for f in findings if f.rule == "relation-regulator"]
    assert len(hit) == 1
    assert hit[0].level == "block"


def test_a_prospect_relation_is_still_silent():
    """Negative control — the new branch must not have caught the ordinary case."""
    findings = check_record(_row(category_relation="prospect"))
    assert not [f for f in findings if f.rule.startswith("relation-")]
