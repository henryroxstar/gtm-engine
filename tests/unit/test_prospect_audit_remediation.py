"""Targeted verification tests for the prospect audit remediation:
1. Static Email Mode Fixes
2. Regulator/Competitor Classifier Gate & Ledger
3. Signal Quality Triage & CxO Gate Logic
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from gtm_core import account_integrity as ai
from gtm_core.account_relation import Body, Regulators, RelationIndex
from gtm_core.account_relation_gate import check_row_relation, parse_relation_ack
from gtm_core.account_relation_record import record_relation_overrides
from gtm_core.competitor_index import CompetitorHit
from gtm_core.merge_hygiene.signal_dates import signal_recency_score
from gtm_core.prospect_paths import pool_dir, suppression_ledger
from gtm_core.static_pipeline import (
    _load_enrolled_emails,
    _load_suppressed_keys,
    partition_by_region,
)
from gtm_core.static_windows import SendWindows
from gtm_core.verdict_refusals import write_kept

# =============================================================================
# 1. Static Email Mode Fixes
# =============================================================================


def test_load_enrolled_emails_handles_dict_cell_map(tmp_path: Path):
    """_load_enrolled_emails must parse dictionaries from load_cell_map without AttributeError."""
    content_root = tmp_path / "content"
    p_dir = pool_dir("test_prof", content_root=content_root)
    csv_file = p_dir.parent / "enrolled.csv"
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    csv_file.write_text("email,first\nalready@example.com,Alice\n", encoding="utf-8")

    mock_cells = [
        {"cell_id": "c1", "csv_name": "enrolled.csv"},
        {"cell_id": "c2", "csv_name": ""},
    ]

    with patch("gtm_core.static_pipeline.load_cell_map", return_value=mock_cells):
        enrolled = _load_enrolled_emails("test_prof", content_root=content_root)
        assert "already@example.com" in enrolled


def test_load_suppressed_keys_company_domain(tmp_path: Path):
    """_load_suppressed_keys must collect company_domain and domain in addition to value/email."""
    content_root = tmp_path / "content"
    ledger = suppression_ledger("test_prof", content_root=content_root)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "email,reason,date,company_domain\n"
        ",competitor,2026-09-01,banned-company.example\n"
        "blocked-user@other.example,optout,2026-09-02,\n",
        encoding="utf-8",
    )

    suppressed = _load_suppressed_keys("test_prof", content_root=content_root)
    assert "banned-company.example" in suppressed
    assert "blocked-user@other.example" in suppressed


def test_partition_by_region_with_country_aliases():
    """partition_by_region correctly normalizes country names like 'United States' and 'USA'."""
    windows = SendWindows(
        default_schedule="sched_sg",
        countries={"US": "sched_us", "CA": "sched_ca"},
    )
    rows = [
        {"email": "user1@example.com", "country": "United States"},
        {"email": "user2@example.com", "country": "USA"},
        {"email": "user3@example.com", "country": "Canada"},
        {"email": "user4@example.com", "country": "Singapore"},
    ]
    res = partition_by_region(rows, windows, pilot_size=10)
    assert len(res["sched_us"].pilot) == 2
    assert len(res["sched_ca"].pilot) == 1
    assert len(res["sched_sg"].pilot) == 1


# =============================================================================
# 2. Regulator/Competitor Classifier Gate & Ledger
# =============================================================================


def test_account_relation_classifier_flags_and_acks():
    """check_row_relation correctly fires findings and respects domain-scoped acks."""
    body = Body(name="FinReg", kind="regulator", domains=("finreg.example",))
    regulators = Regulators(bodies=(body,))
    comp_hit = CompetitorHit(
        tier="direct",
        summary="competitor-direct: CompCo",
        entry="CompCo",
    )
    index = RelationIndex(
        regulators=regulators,
        competitors={"compco.example": comp_hit, "compco": comp_hit},
    )

    audit = ai.AccountAudit(rows=3)
    flagged: set[tuple[str, str]] = set()

    # Direct competitor
    row_comp = {
        "company": "CompCo",
        "company_domain": "compco.example",
        "email": "a@compco.example",
    }
    check_row_relation(audit, row_comp, "compco.example", index, flagged)
    assert audit.competitor_direct == 1
    assert any("competitor-direct" in e for e in audit.errors)

    # Regulator without ack -> error
    row_reg = {
        "company": "FinReg",
        "company_domain": "finreg.example",
        "email": "b@finreg.example",
    }
    check_row_relation(audit, row_reg, "finreg.example", index, flagged)
    assert any("relation-regulator" in e for e in audit.errors)

    # Regulator with valid ack -> warning + override recorded
    audit2 = ai.AccountAudit(rows=1)
    flagged2: set[tuple[str, str]] = set()
    recorded_overrides: list[dict] = []
    ack_res = parse_relation_ack("relation-regulator:finreg.example")
    assert ack_res is not None
    ack_rule, ack_domain = ack_res
    assert ack_rule == "relation-regulator"
    assert ack_domain == "finreg.example"

    check_row_relation(
        audit2,
        row_reg,
        "finreg.example",
        index,
        flagged2,
        acked_domains={ack_domain},
        recorded_overrides=recorded_overrides,
    )
    assert not any("relation-regulator" in e for e in audit2.errors)
    assert len(recorded_overrides) == 1
    assert recorded_overrides[0]["domain"] == "finreg.example"
    assert recorded_overrides[0]["via"] == "ack"


def test_record_relation_overrides():
    """record_relation_overrides appends history event with relation_override_used."""
    mock_ledgers = MagicMock()
    with patch("gtm_core.account_relation_record.Ledgers", return_value=mock_ledgers):
        record_relation_overrides(
            "test_prof",
            [{"domain": "finreg.example", "rule": "relation-regulator", "reason": "acked"}],
        )
        assert mock_ledgers.append_history.called
        event = mock_ledgers.append_history.call_args[0][0]
        assert event["event"] == "relation_override_used"
        assert event["domain"] == "finreg.example"


# =============================================================================
# 3. Signal Quality Triage & CxO Gate Logic
# =============================================================================


def test_filter_by_verdict_generic_lane_bypasses_signal_quality():
    """Generic lane must NOT filter or triage CxOs or non-CxOs on signal quality."""
    rows = [
        {
            "email": "ceo@generic.example",
            "title": "Chief Executive Officer",
            "verdict": "send",
            "why_now": "",
            "signal_fit": "",
            "signal_observed": "",
        },
        {
            "email": "lead@generic.example",
            "title": "Tech Lead",
            "verdict": "send",
            "why_now": "",
            "signal_fit": "",
            "signal_observed": "",
        },
    ]

    kept, stats = ai.filter_by_verdict(rows, "send", lane="generic")
    assert len(kept) == 2
    assert stats.cxo_dropped == 0
    assert stats.cxo_unverified == 0
    assert stats.tier4_dropped == 0


def test_filter_by_verdict_personalised_lane_triages_low_signal():
    """Personalised lane triages CxO and non-CxO according to signal quality rules."""
    rows = [
        # CxO with empty fit -> unverified
        {
            "email": "ceo@apex.example",
            "title": "Chief Executive Officer",
            "verdict": "send",
            "why_now": "Raised Series A",
            "signal_fit": "",
            "signal_observed": "2026-08-01",
        },
        # Non-CxO with empty fit on signal-scored list -> unverified
        {
            "email": "eng@apex.example",
            "title": "Staff Engineer",
            "verdict": "send",
            "why_now": "Hiring Engineers",
            "signal_fit": "",
            "signal_observed": "2026-08-01",
        },
        # Non-CxO with Tier 2 signal -> kept
        {
            "email": "vp@apex.example",
            "title": "VP of Product",
            "verdict": "send",
            "why_now": "Launched agent",
            "signal_fit": "2",
            "signal_observed": "2026-08-01",
        },
    ]

    as_of = datetime.date(2026, 8, 14)
    kept, stats = ai.filter_by_verdict(rows, "send", lane="personalised", as_of=as_of)
    assert len(kept) == 1
    assert kept[0]["email"] == "vp@apex.example"
    assert stats.cxo_unverified == 1
    assert stats.tier4_dropped == 1


def test_signal_recency_score_12_month_window():
    """signal_recency_score supports 365 days window when max_age_days is 365."""
    today = datetime.date(2026, 8, 14)
    obs = "2025-11-01"  # ~286 days old

    # Default max_age_days allows <= 365 as 0.1
    score = signal_recency_score(obs, as_of=today, max_age_days=365)
    assert score == 0.1

    # Over 365 days is stale (0.0)
    stale_obs = "2025-01-01"  # ~590 days old
    assert signal_recency_score(stale_obs, as_of=today, max_age_days=365) == 0.0


def test_write_kept_column_parity_without_signal_fit(tmp_path: Path):
    """write_kept only writes signal_quality_tier and signal_recency_score when signal_fit was in fieldnames."""
    out_file = tmp_path / "kept.csv"
    rows = [{"email": "a@example.com", "name": "Alice"}]
    fieldnames = ["email", "name"]

    write_kept(out_file, fieldnames, rows)
    content = out_file.read_text(encoding="utf-8")
    header = content.splitlines()[0]
    assert header == "email,name"
    assert "signal_quality_tier" not in header
