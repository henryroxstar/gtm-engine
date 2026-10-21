"""Tests for verdict leak detection and drop suppression enforcement."""

from __future__ import annotations

from pathlib import Path

from gtm_core.ledger_integrity import check_verdict_leak, enforce_drop_suppression
from gtm_core.suppression import load_index


def test_clean_why_now_passes() -> None:
    items = [
        {
            "company": "Acme Corp",
            "why_now": "Announced partnership with Global Systems for automated warehousing",
            "verdict": "send",
        }
    ]
    findings = check_verdict_leak(items)
    assert len(findings) == 0


def test_verdict_leak_patterns_detected() -> None:
    items = [
        {
            "company": "Company A",
            "why_now": "Bombora topic-intent: agentic ai (79) — feed signal; dated public why-now not found",
        },
        {
            "company": "Company B",
            "why_now": "no dated public article found this pass",
        },
        {
            "company": "Company C",
            "why_now": "firmographic + cohort fit only",
        },
        {
            "company": "Company D",
            "why_now": "feed signal only",
        },
        {
            "company": "Company E",
            "why_now": "no public news found",
        },
    ]

    findings = check_verdict_leak(items)
    assert len(findings) == 5
    companies = {f["company"] for f in findings}
    assert companies == {"Company A", "Company B", "Company C", "Company D", "Company E"}


def test_empty_or_missing_why_now_not_flagged() -> None:
    items = [
        {"company": "Blank Co", "why_now": ""},
        {"company": "None Co", "why_now": None},
        {"company": "Missing Co"},
    ]
    findings = check_verdict_leak(items)
    assert len(findings) == 0


def test_enforce_drop_suppression(tmp_path: Path) -> None:
    profile = "test-tenant"
    items = [
        {
            "company": "Competitor X",
            "domain": "competitorx.example",
            "email": "ceo@competitorx.example",
            "verdict": "drop",
            "verdict_reason": "competitor",
        },
        {
            "company": "Valid Co",
            "domain": "validco.example",
            "email": "lead@validco.example",
            "verdict": "send",
        },
        {
            "company": "Dead Co",
            "domain": "deadco.example",
            "email": "info@deadco.example",
            "verdict": "drop",
            "verdict_reason": "entity-dissolved",
        },
    ]

    added = enforce_drop_suppression(profile, items, content_root=tmp_path)
    assert added == 2

    # Check suppression ledger
    ledger_path = tmp_path / profile / "prospects" / "sequences" / ".pool" / "suppression.csv"
    assert ledger_path.exists()

    idx = load_index(ledger_path)
    assert idx.match({"email": "ceo@competitorx.example"}) is not None
    assert idx.match({"email": "info@deadco.example"}) is not None
    assert idx.match({"email": "lead@validco.example"}) is None

    # Running a second time should be idempotent (added == 0)
    added_again = enforce_drop_suppression(profile, items, content_root=tmp_path)
    assert added_again == 0
