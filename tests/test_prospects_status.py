"""Tests for Attrition Receipt and funnel conservation in gtm_core.prospects status."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gtm_core import prospect_status_cli as cli
from gtm_core.prospect_status import (
    AttritionReceipt,
    compute_attrition_receipt,
    verify_funnel_conservation,
)
from gtm_core.slugify import slug


def test_funnel_conservation_of_accounts() -> None:
    """Assert Total Intake == (Failed Fit + Failed Intent + Failed Enrichment + Held + Ready).

    A missing or double-counted row must raise ValueError rather than printing an
    unbalanced funnel.
    """
    receipt = AttritionReceipt(
        total_intake=50,
        failed_fit=20,
        failed_intent=10,
        failed_enrichment=8,
        held=5,
        ready=7,
    )
    # Conservation holds: 20 + 10 + 8 + 5 + 7 == 50
    assert verify_funnel_conservation(receipt) is True
    assert receipt.total_intake == (
        receipt.failed_fit
        + receipt.failed_intent
        + receipt.failed_enrichment
        + receipt.held
        + receipt.ready
    )

    # Missing row: sum is 49 != 50
    unbalanced_missing = AttritionReceipt(
        total_intake=50,
        failed_fit=19,
        failed_intent=10,
        failed_enrichment=8,
        held=5,
        ready=7,
    )
    with pytest.raises(ValueError, match=r"[Ff]unnel conservation violated"):
        verify_funnel_conservation(unbalanced_missing)

    # Double-counted row: sum is 51 != 50
    unbalanced_double = AttritionReceipt(
        total_intake=50,
        failed_fit=20,
        failed_intent=11,
        failed_enrichment=8,
        held=5,
        ready=7,
    )
    with pytest.raises(ValueError, match=r"[Ff]unnel conservation violated"):
        verify_funnel_conservation(unbalanced_double)


def test_double_count_prevention() -> None:
    """Ensure accounts processed across multiple waves deduplicate correctly based on their canonical slug."""
    # Wave 1 processed Acme Robotics (failed fit) and Eastvale (ready)
    # Wave 2 re-evaluated Acme Robotics (now held) and added Summitline (ready)
    wave1_items = [
        {"company": "Acme Robotics", "stage": "failed_fit"},
        {"company": "Eastvale Data", "stage": "ready"},
    ]
    wave2_items = [
        {"company": "Acme-Robotics", "stage": "held"},  # Same canonical slug as "Acme Robotics"
        {"company": "Summitline Systems", "stage": "ready"},
    ]

    combined = wave1_items + wave2_items
    receipt = compute_attrition_receipt(combined)

    # Should deduplicate Acme Robotics down to 1 account: total unique = 3 (Acme Robotics, Eastvale Data, Summitline Systems)
    assert receipt.total_intake == 3
    assert verify_funnel_conservation(receipt) is True

    # Check that canonical slugs are used
    assert slug("Acme Robotics") == slug("Acme-Robotics")


def test_status_cli_outputs_attrition_receipt_and_action_alert(tmp_path: Path, monkeypatch) -> None:
    """CLI outputs Attrition Receipt waterfall and ACTION REQUIRED alert when items are held."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-profile"
    evals_dir = tmp_path / profile / "prospects" / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)

    # lanes-state.jsonl with held records
    state_rows = [
        {"email": "ada@example.com", "lane": "generic", "reason": ""},
        {"email": "bob@example.com", "lane": "hold", "reason": "tier-a-generic"},
        {"email": "cy@example.com", "lane": "hold", "reason": "competitor-adjacent"},
    ]
    (evals_dir / "lanes-state.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in state_rows), encoding="utf-8"
    )

    latest_items = [
        {"company": "Acme Corp", "stage": "ready"},
        {"company": "Beta Inc", "stage": "held"},
        {"company": "Gamma LLC", "stage": "held"},
    ]
    latest_file = tmp_path / profile / "prospects" / "latest.json"
    latest_file.write_text(
        json.dumps({"kind": "prospects", "items": latest_items}), encoding="utf-8"
    )

    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    exit_code = cli.main(["--profile", profile])
    assert exit_code == 0
    out = buf.getvalue()

    # ACTION REQUIRED banner for 2 held accounts
    assert (
        "> [!WARNING] ACTION REQUIRED: 2 accounts require routing decisions in the Review Sheet."
        in out
    )
    # Attrition receipt or waterfall
    assert "[Total Intake]" in out or "Attrition Receipt" in out


def test_classification_on_real_account_shapes() -> None:
    """Verify classification of real latest.json accounts with verdict, relation, email, and lane."""
    accounts = [
        # Competitor dropped with email must be failed_fit, NOT ready
        {
            "company": "Rival AI",
            "verdict": "drop",
            "category_relation": "competitor",
            "contact_email": "rival@example.com",
            "lane": "excluded",
        },
        # Re-angled signal must be failed_intent
        {
            "company": "Pending Story",
            "verdict": "re-angle",
            "verdict_reason": "no dated signal",
            "contact_email": "story@example.com",
        },
        # Missing contact email must be failed_enrichment
        {
            "company": "Ghost Lead",
            "verdict": "send",
            "contact_name": "Ghost User",
            "contact_email": "",
            "lane": "personalised",
        },
        # Held account in hold lane
        {
            "company": "Held Company",
            "verdict": "send",
            "lane": "hold",
            "lane_reason": "tier-a-generic",
            "contact_email": "held@example.com",
        },
        # Clean ready account
        {
            "company": "Winning Corp",
            "verdict": "send",
            "lane": "personalised",
            "contact_email": "winner@example.com",
            "status": "ready",
        },
    ]

    receipt = compute_attrition_receipt(accounts)
    assert receipt.total_intake == 5
    assert receipt.failed_fit == 1
    assert receipt.failed_intent == 1
    assert receipt.failed_enrichment == 1
    assert receipt.held == 1
    assert receipt.ready == 1
    assert verify_funnel_conservation(receipt) is True


def test_pseudo_email_values_classified_as_failed_enrichment() -> None:
    """Accounts with pseudo-emails ('null', 'unverified', 'none', or missing @) are classified as failed_enrichment."""
    accounts = [
        {"company": "Null Email Corp", "verdict": "send", "contact_email": "null"},
        {"company": "Unverified Email Inc", "verdict": "send", "contact_email": "unverified"},
        {"company": "None Email LLC", "verdict": "send", "contact_email": "none"},
        {"company": "Invalid Handle Co", "verdict": "send", "contact_email": "not-an-email"},
        {"company": "Valid Email Ltd", "verdict": "send", "contact_email": "valid@example.com"},
    ]
    receipt = compute_attrition_receipt(accounts)
    assert receipt.total_intake == 5
    assert receipt.failed_enrichment == 4
    assert receipt.ready == 1
    assert verify_funnel_conservation(receipt) is True


def test_status_cli_outputs_action_alert_when_latest_has_held_accounts(
    tmp_path: Path, monkeypatch
) -> None:
    """CLI outputs ACTION REQUIRED alert when lanes-state.jsonl has 0 held, but latest.json has held accounts."""
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-profile"
    evals_dir = tmp_path / profile / "prospects" / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)

    # lanes-state.jsonl has generic only (0 held)
    state_rows = [
        {"email": "ada@example.com", "lane": "generic", "reason": ""},
        {"email": "bob@example.com", "lane": "generic", "reason": ""},
    ]
    (evals_dir / "lanes-state.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in state_rows), encoding="utf-8"
    )

    # latest.json has 3 held accounts
    latest_items = [
        {"company": "Acme Corp", "stage": "ready"},
        {"company": "Beta Inc", "stage": "held"},
        {"company": "Gamma LLC", "stage": "held"},
        {"company": "Delta Co", "stage": "held"},
    ]
    latest_file = tmp_path / profile / "prospects" / "latest.json"
    latest_file.write_text(
        json.dumps({"kind": "prospects", "items": latest_items}), encoding="utf-8"
    )

    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)

    exit_code = cli.main(["--profile", profile])
    assert exit_code == 0
    out = buf.getvalue()

    # ACTION REQUIRED banner must be present for the 3 held accounts in the ledger
    assert (
        "> [!WARNING] ACTION REQUIRED: 3 accounts require routing decisions in the Review Sheet."
        in out
    )
    assert "[Held: 3]" in out


def test_provenance_pairing_contract() -> None:
    """Outreach pack drafts must carry verifiable **Source Evidence:** blocks pairing triggers to dates and citations."""
    import re

    valid_draft = (
        "## Email (touch 1)\n"
        "**Subject:** soc2 audit timeline\n"
        "> Hi Alex,\n>\n"
        "> Noticed your team completed the SOC2 readiness review.\n"
        "\n"
        "**Source Evidence:** `RocketReach Intent (+2) | 2026-09-18 | Acme IT Blog: 'Passing SOC2 Phase 1'`\n"
    )
    evidence_pattern = re.compile(
        r"\*\*Source Evidence:\*\*\s*`([^|]+)\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^`]+)`"
    )
    match = evidence_pattern.search(valid_draft)
    assert match is not None
    provider, date_str, snippet = (
        match.group(1).strip(),
        match.group(2).strip(),
        match.group(3).strip(),
    )
    assert "RocketReach" in provider
    assert date_str == "2026-09-18"
    assert "SOC2 Phase 1" in snippet

    # Hallucinated / detached trigger draft fails
    invalid_draft = (
        "## Email (touch 1)\n**Subject:** quick question\n> Hi Alex,\n> Hope you are well.\n"
    )
    assert evidence_pattern.search(invalid_draft) is None
