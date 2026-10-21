import json
from pathlib import Path
from unittest.mock import patch

from gtm_core.signal_backfill import (
    RECORD_INPUT_FIELDS,
    apply_records,
    load_records,
    promote_records,
)
from gtm_core.signal_record import SIGNAL_FIT_COLUMN, SIGNAL_VIRALITY_COLUMN


def test_record_input_fields_contains_quality_columns() -> None:
    assert SIGNAL_FIT_COLUMN in RECORD_INPUT_FIELDS
    assert SIGNAL_VIRALITY_COLUMN in RECORD_INPUT_FIELDS


def test_load_records_parses_quality_columns(tmp_path: Path) -> None:
    records_file = tmp_path / "records.json"
    records_data = [
        {
            "email": "jane@acme.example",
            "signal_clause": "Acme expanded multi-agent infrastructure on 2026-09-20",
            "signal_source_url": "https://news.example/acme-infra",
            "signal_observed": "2026-09-20",
            "signal_evidence": "Acme today expanded their multi-agent production deployment across clusters.",
            "signal_subject": "Acme",
            "signal_agent_kind": "ai",
            "category_relation": "prospect",
            "verdict": "send",
            "signal_fit": "3",
            "signal_virality": "2",
        }
    ]
    records_file.write_text(json.dumps(records_data), encoding="utf-8")
    loaded = load_records(records_file)
    assert "jane@acme.example" in loaded
    rec = loaded["jane@acme.example"]
    assert rec["signal_fit"] == "3"
    assert rec["signal_virality"] == "2"


def test_apply_records_preserves_quality_columns() -> None:
    rows = [
        {
            "email": "jane@acme.example",
            "company": "Acme",
            "why_now": "something old",
        }
    ]
    records = {
        "jane@acme.example": {
            "signal_clause": "Acme expanded multi-agent infrastructure on 2026-09-20",
            "signal_source_url": "https://news.example/acme-infra",
            "signal_observed": "2026-09-20",
            "signal_evidence": "Acme expanded multi-agent infrastructure on 2026-09-20 across clusters.",
            "signal_subject": "Acme",
            "signal_agent_kind": "ai",
            "category_relation": "prospect",
            "verdict": "send",
            "signal_fit": "3",
            "signal_virality": "2",
        }
    }
    with patch("gtm_core.signal_backfill.check_record", return_value=[]):
        res = apply_records(rows, ["email", "company", "why_now"], records)
        assert not res.refusals
        assert "signal_fit" in res.fieldnames
        assert "signal_virality" in res.fieldnames
        assert len(res.rows) == 1
        assert res.rows[0]["signal_fit"] == "3"
        assert res.rows[0]["signal_virality"] == "2"


def test_promote_records_copies_quality_columns() -> None:
    rows = [
        {
            "email": "jane@acme.example",
            "company": "Acme",
            "account_id": "acc-123",
        }
    ]
    records = {
        "jane@acme.example": {
            "signal_clause": "Acme expanded multi-agent infrastructure on 2026-09-20",
            "signal_source_url": "https://news.example/acme-infra",
            "signal_observed": "2026-09-20",
            "signal_evidence": "Acme today expanded their multi-agent production deployment across clusters.",
            "signal_subject": "Acme",
            "signal_agent_kind": "ai",
            "category_relation": "prospect",
            "verdict": "send",
            "signal_fit": "3",
            "signal_virality": "2",
        }
    }
    fake_latest = {
        "items": [
            {
                "account_id": "acc-123",
                "company": "Acme",
                "domain": "acme.example",
            }
        ]
    }
    with patch("gtm_core.prospects_state.load_latest", return_value=fake_latest):
        items, orphans = promote_records(records, rows, profile="test-profile")
        assert not orphans
        assert len(items) == 1
        item = items[0]
        assert item["signal_fit"] == "3"
        assert item["signal_virality"] == "2"
