"""Tests for the accounts block and funnel conservation in gtm_core.prospects status.

2026-09-21 (PSK-029): the banner and the accounts block are derived from the routed state the
contact table is built from, so three expectations here changed — see each test's docstring.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gtm_core import prospect_status_cli as cli
from gtm_core.prospect_status_receipt import (
    AttritionReceipt,
    compute_attrition_receipt,
    verify_funnel_conservation,
)


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
    """An account seen in two waves is ONE account — by the LEDGER's identity (domain first).

    Changed 2026-09-21: this used to dedupe on the company-name slug, which merged distinct
    ledger accounts that share a name. The ledger's own key is reused instead, so a re-spelled
    name on the same domain is still one account, and the same name on two domains is two.
    """
    wave1_items = [
        {"company": "Acme Robotics", "domain": "acmerobotics.example", "stage": "failed_fit"},
        {"company": "Eastvale Data", "domain": "eastvaledata.example", "stage": "ready"},
    ]
    wave2_items = [
        {"company": "Acme-Robotics", "domain": "acmerobotics.example", "stage": "held"},
        {"company": "Summitline Systems", "domain": "summitline.example", "stage": "ready"},
    ]

    receipt = compute_attrition_receipt(wave1_items + wave2_items)

    assert receipt.total_intake == 3
    assert (receipt.held, receipt.ready, receipt.failed_fit) == (1, 2, 0)  # the later wave wins
    assert verify_funnel_conservation(receipt) is True


def test_two_ledger_rows_sharing_a_name_on_different_domains_are_two_accounts() -> None:
    items = [
        {"company": "Northwind Robotics", "domain": "northwindrobotics.example"},
        {"company": "Northwind Robotics", "domain": "northwind-robotics-apac.example"},
        {"company": "Northwind Robotics", "domain": "northwindrobotics-eu.example"},
    ]
    assert compute_attrition_receipt(items).total_intake == 3


def _seed_status(tmp_path: Path, monkeypatch, state_rows: list[dict], items: list[dict]) -> str:
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    profile = "test-profile"
    evals_dir = tmp_path / profile / "prospects" / "evals"
    evals_dir.mkdir(parents=True, exist_ok=True)
    (evals_dir / "lanes-state.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in state_rows), encoding="utf-8"
    )
    (tmp_path / profile / "prospects" / "latest.json").write_text(
        json.dumps({"kind": "prospects", "items": items}), encoding="utf-8"
    )
    return profile


def test_status_cli_outputs_accounts_block_and_action_alert(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The banner counts the CONTACTS waiting on the operator and the accounts block agrees.

    Changed 2026-09-21: the banner used to say "N accounts require routing decisions in the
    Review Sheet" — a count of contacts labelled as accounts, naming a sheet with no path.
    """
    state_rows = [
        {"email": "ada@acme.example", "lane": "generic", "reason": "no-judge-verdict"},
        {"email": "bob@beta.example", "lane": "hold", "reason": "tier-a-generic"},
        {"email": "cy@gamma.example", "lane": "hold", "reason": "competitor-adjacent"},
    ]
    latest_items = [
        {"company": "Acme Corp", "domain": "acme.example", "contact_email": "ada@acme.example"},
        {"company": "Beta Inc", "domain": "beta.example", "contact_email": "bob@beta.example"},
        {"company": "Gamma LLC", "domain": "gamma.example", "contact_email": "cy@gamma.example"},
    ]
    profile = _seed_status(tmp_path, monkeypatch, state_rows, latest_items)

    assert cli.main(["--profile", profile]) == 0
    out = capsys.readouterr().out

    assert "> [!WARNING] ACTION REQUIRED: 2 contacts are waiting on your decision." in out
    assert "Accounts — where each stands (companies, not people):" in out
    assert "Contacts — by status (people, not companies):" in out
    assert re.search(r"^  Held\s+2\b", out, re.M)
    assert re.search(r"^  Ready\s+1\b", out, re.M)
    assert re.search(r"^  All accounts\s+3\b", out, re.M)
    assert "Check:" not in out


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
    # Changed 2026-09-21: a usable email alone used to make an account "ready". It is ready
    # only when a routed contact is ready to send; with nothing routed it is "not yet routed".
    assert (receipt.ready, receipt.not_routed) == (0, 1)
    assert verify_funnel_conservation(receipt) is True


def test_status_cli_prints_no_action_alert_when_only_the_ledger_says_held(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Nobody is waiting on the operator, so there is NO banner — whatever the ledger rows say.

    Changed 2026-09-21: this test used to REQUIRE a banner ("3 accounts") from ledger rows
    alone while "Waiting on you" read 0 — the contradiction the live block showed as "705
    accounts" above "Waiting on you 102". The banner is now the waiting count, always.
    """
    state_rows = [
        {"email": "ada@acme.example", "lane": "generic", "reason": "no-judge-verdict"},
        {"email": "bob@acme.example", "lane": "generic", "reason": "no-judge-verdict"},
    ]
    latest_items = [
        {"company": "Acme Corp", "domain": "acme.example", "contact_email": "ada@acme.example"},
        {"company": "Beta Inc", "stage": "held", "status": "new"},
        {"company": "Gamma LLC", "stage": "held", "status": "new"},
        {"company": "Delta Co", "stage": "held", "status": "new"},
    ]
    profile = _seed_status(tmp_path, monkeypatch, state_rows, latest_items)

    assert cli.main(["--profile", profile]) == 0
    out = capsys.readouterr().out

    assert "ACTION REQUIRED" not in out
    assert re.search(r"^Waiting on you\s+0\b", out, re.M)
    assert re.search(r"^  Held\s+0\b", out, re.M)
    assert re.search(r"^  Ready\s+1\b", out, re.M)
    # The three unrouted accounts have no usable contact; none of them is "held".
    assert re.search(r"^  No usable contact yet\s+3\b", out, re.M)


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
