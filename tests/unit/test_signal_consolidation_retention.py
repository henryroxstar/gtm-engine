from gtm_core.prospects_consolidate.accounts import (
    _AUTHORITATIVE_RECORD_COLUMNS,
    _INHERITED_RECORD_COLUMNS,
    SIGNAL_GROUP_COLUMNS,
)
from gtm_core.prospects_consolidate.columns import _ALIASES, MASTER_COLS
from gtm_core.prospects_consolidate.confidence import _row_to_record
from gtm_core.signal_record import SIGNAL_FIT_COLUMN, SIGNAL_VIRALITY_COLUMN


def test_columns_and_aliases_include_quality() -> None:
    assert SIGNAL_FIT_COLUMN in MASTER_COLS
    assert SIGNAL_VIRALITY_COLUMN in MASTER_COLS
    assert SIGNAL_FIT_COLUMN in _ALIASES
    assert SIGNAL_VIRALITY_COLUMN in _ALIASES


def test_accounts_column_tuples_include_quality() -> None:
    assert SIGNAL_FIT_COLUMN in _INHERITED_RECORD_COLUMNS
    assert SIGNAL_VIRALITY_COLUMN in _INHERITED_RECORD_COLUMNS
    assert SIGNAL_FIT_COLUMN in _AUTHORITATIVE_RECORD_COLUMNS
    assert SIGNAL_VIRALITY_COLUMN in _AUTHORITATIVE_RECORD_COLUMNS
    assert SIGNAL_FIT_COLUMN in SIGNAL_GROUP_COLUMNS
    assert SIGNAL_VIRALITY_COLUMN in SIGNAL_GROUP_COLUMNS


def test_row_to_record_preserves_quality_scores() -> None:
    # Simulates incoming HubSpot CSV export row with GTM_Signal_Fit and GTM_Signal_Virality
    raw_row = {
        "First Name": "Jane",
        "Last Name": "Doe",
        "Email": "jane.doe@acme.example",
        "Job Title": "VP Engineering",
        "Company Name": "Acme",
        "Company Domain Name": "acme.example",
        "Email Status": "verified",
        "GTM_Segment": "enterprise",
        "GTM_Tier": "A",
        "GTM_Score": "8",
        "GTM_Why_Now": "Acme deployed multi-agent system on 2026-09-20",
        "GTM_Signal_Fit": "3",
        "GTM_Signal_Virality": "2",
        "GTM_Signal_Source_URL": "https://news.example/acme-ai",
        "GTM_Signal_Observed": "2026-09-20",
        "GTM_Signal_Evidence": "Acme deployed multi-agent system on 2026-09-20 in production.",
        "GTM_Signal_Subject": "Acme",
        "GTM_Signal_Agent_Kind": "ai",
        "GTM_Category_Relation": "prospect",
        "GTM_Verdict": "send",
    }
    rec = _row_to_record(raw_row, "hubspot")
    assert rec["signal_fit"] == "3"
    assert rec["signal_virality"] == "2"


def test_account_records_authoritative_and_cleared_signal() -> None:
    from gtm_core.prospects_consolidate.accounts import _account_record_wins

    # Quality columns are authoritative
    assert _account_record_wins(SIGNAL_FIT_COLUMN, {"why_now": "something"}) is True
    assert _account_record_wins(SIGNAL_VIRALITY_COLUMN, {"why_now": "something"}) is True

    # Row blanking via SIGNAL_GROUP_COLUMNS
    row = {
        "why_now": "old",
        "signal_fit": "3",
        "signal_virality": "2",
        "signal_source_url": "https://news.example/ai",
    }
    for col in SIGNAL_GROUP_COLUMNS:
        row[col] = ""

    assert row["signal_fit"] == ""
    assert row["signal_virality"] == ""
    assert row["why_now"] == ""
