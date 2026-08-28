"""Tests for :mod:`gtm_core.signal_backfill`.

Every company, person and URL here is invented (§R9 -- no third-party PII outside
``profiles/`` and ``content/``). Real rows are the ones the module writes; fixtures are not.
"""

from __future__ import annotations

import csv
import datetime
import json
from pathlib import Path

import pytest

from gtm_core.hook_coverage import parse_matrix
from gtm_core.signal_backfill import (
    RECORD_INPUT_FIELDS,
    apply_records,
    load_records,
    render,
    write_list,
)
from gtm_core.signal_record import RECORD_COLUMNS, SIGNAL_COLUMN

AS_OF = datetime.date(2026, 8, 20)

FIELDNAMES = ["first", "last", "email", "title", "company", "signal_clause", "why_now"]


def _p(name: str) -> Path:
    return Path(name)


def _row(**kw) -> dict:
    base = {
        "first": "Rowan",
        "last": "Vaske",
        "email": "rowan@cascade.example",
        "title": "Chief Information Security Officer",
        "company": "Cascade Systems",
        "signal_clause": "Cascade Systems published a runtime policy engine for agent actions",
        "why_now": "",
    }
    base.update(kw)
    return base


def _record(**kw) -> dict:
    base = {
        "email": "rowan@cascade.example",
        "signal_source_url": "https://cascade.example/news/policy-engine",
        "signal_observed": "2026-07-01",
        "signal_evidence": (
            "Cascade Systems today published a runtime policy engine that evaluates agent "
            "actions before they execute."
        ),
        "signal_subject": "Cascade Systems",
        "signal_agent_kind": "ai",
        "category_relation": "prospect",
        "verdict": "send",
        "verdict_reason": "",
    }
    base.update(kw)
    return base


def _apply(rows, records, **kw):
    return apply_records(rows, FIELDNAMES, {r["email"]: r for r in records}, as_of=AS_OF, **kw)


# --- the happy path -------------------------------------------------------


def test_a_verified_record_is_written_and_adds_the_eight_columns():
    res = _apply([_row()], [_record()])
    assert res.applied == 1
    assert not res.refusals
    assert not res.failed
    assert all(c in res.fieldnames for c in RECORD_COLUMNS)
    assert res.rows[0]["signal_source_url"] == "https://cascade.example/news/policy-engine"


def test_the_original_columns_keep_their_order_and_the_record_is_appended():
    res = _apply([_row()], [_record()])
    assert res.fieldnames[: len(FIELDNAMES)] == FIELDNAMES
    assert res.fieldnames[len(FIELDNAMES) :] == list(RECORD_COLUMNS)


def test_a_row_with_no_record_is_left_exactly_as_it_was():
    """Resumability: backfilling one batch must not mark the remainder as researched."""
    rows = [_row(), _row(email="ines@harbor.example", company="Harbor Freight Labs")]
    res = _apply(rows, [_record()])
    assert res.applied == 1
    assert res.untouched == ["ines@harbor.example"]
    untouched = res.rows[1]
    assert untouched["signal_clause"] == rows[1]["signal_clause"]
    assert all(untouched[c] == "" for c in RECORD_COLUMNS)


# --- refusals: the point of the module ------------------------------------


@pytest.mark.parametrize(
    ("override", "rule"),
    [
        ({"signal_source_url": ""}, "signal-source-missing"),
        ({"signal_source_url": "http://cascade.example/x"}, "signal-source-malformed"),
        (
            {"signal_source_url": "https://www.google.com/search?q=cascade"},
            "signal-source-is-search",
        ),
        ({"signal_observed": "July 2026"}, "signal-observed-missing"),
        ({"signal_observed": "2026-09-01"}, "signal-observed-future"),
        ({"signal_observed": "2025-01-05"}, "signal-stale"),
        ({"signal_evidence": ""}, "signal-evidence-missing"),
        ({"category_relation": "unclear"}, "relation-unresolved"),
        ({"category_relation": "competitor"}, "relation-competitor"),
        ({"verdict": ""}, "verdict-missing"),
        ({"verdict": "maybe"}, "verdict-unknown"),
        ({"verdict": "drop", "verdict_reason": ""}, "verdict-reason-missing"),
    ],
)
def test_a_record_that_would_fail_the_load_gate_is_refused_not_written(override, rule):
    res = _apply([_row()], [_record(**override)])
    assert res.applied == 0
    assert rule in {r.rule for r in res.refusals}
    assert res.failed


def test_a_refused_record_leaves_the_row_untouched_rather_than_half_written():
    """A partial write is worse than no write: it reads as researched and is not."""
    res = _apply([_row()], [_record(signal_evidence="")])
    row = res.rows[0]
    assert all(row[c] == "" for c in RECORD_COLUMNS)
    assert row["signal_clause"] == _row()["signal_clause"]


def test_evidence_that_does_not_support_the_clause_is_refused():
    """The defect this whole family exists for: a real source that says something else."""
    res = _apply(
        [_row()],
        [
            _record(
                signal_evidence=(
                    "Cascade Systems announced a new regional office and two senior hires."
                )
            )
        ],
    )
    assert res.applied == 0
    assert "signal-evidence-unsupported" in {r.rule for r in res.refusals}


def test_a_number_absent_from_the_source_is_refused():
    res = _apply(
        [_row(signal_clause="Cascade Systems cut agent policy review to 12 minutes")],
        [
            _record(
                signal_evidence=(
                    "Cascade Systems cut agent policy review to 40 minutes, the company said."
                )
            )
        ],
    )
    assert "signal-number-unsourced" in {r.rule for r in res.refusals}


def test_refusals_name_the_company_so_the_report_is_actionable():
    res = _apply([_row()], [_record(signal_observed="2024-01-01")])
    assert res.refusals[0].company == "Cascade Systems"
    assert "signal backfill" in render(res, list_path=_p("in.csv"), out_path=_p("out.csv"))


# --- the clause a signal did not survive ----------------------------------


def test_an_unverifiable_signal_clears_the_clause_rather_than_shipping_it():
    """A dropped row must not keep asserting the sentence that failed verification."""
    res = _apply(
        [_row()],
        [
            {
                "email": "rowan@cascade.example",
                "signal_clause": "",
                "signal_source_url": "",
                "signal_observed": "",
                "signal_evidence": "",
                "signal_subject": "",
                "signal_agent_kind": "none",
                "category_relation": "prospect",
                "verdict": "drop",
                "verdict_reason": "no source found that supports the clause",
            }
        ],
    )
    assert res.applied == 1
    assert res.cleared == 1
    assert not res.refusals
    assert res.rows[0]["signal_clause"] == ""
    assert res.rows[0]["verdict"] == "drop"


def test_clearing_the_clause_does_not_excuse_a_missing_verdict_reason():
    res = _apply(
        [_row()],
        [
            {
                "email": "rowan@cascade.example",
                "signal_clause": "",
                "verdict": "drop",
                "verdict_reason": "",
                "category_relation": "prospect",
            }
        ],
    )
    assert "verdict-reason-missing" in {r.rule for r in res.refusals}


# --- loading --------------------------------------------------------------


def test_an_unknown_record_field_is_an_error_not_a_silent_drop(tmp_path):
    """A typo'd column that is ignored produces a blank field and no complaint."""
    p = tmp_path / "recs.json"
    p.write_text(json.dumps([{"email": "a@b.example", "signal_evidance": "typo"}]))
    with pytest.raises(ValueError, match="unknown field"):
        load_records(p)


def test_two_records_for_one_email_are_an_error(tmp_path):
    p = tmp_path / "recs.json"
    p.write_text(json.dumps([{"email": "a@b.example"}, {"email": "A@B.example"}]))
    with pytest.raises(ValueError, match="two records"):
        load_records(p)


def test_a_record_with_no_email_has_nothing_to_join_on(tmp_path):
    p = tmp_path / "recs.json"
    p.write_text(json.dumps([{"signal_subject": "Cascade Systems"}]))
    with pytest.raises(ValueError, match="no email"):
        load_records(p)


def test_records_accepts_both_a_bare_list_and_a_wrapped_object(tmp_path):
    rec = {"email": "a@b.example", "verdict": "send"}
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps([rec]))
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"records": [rec]}))
    assert load_records(bare) == load_records(wrapped)


def test_every_record_input_field_is_a_real_column_or_the_join_key():
    assert set(RECORD_INPUT_FIELDS) == {"email", "signal_clause", SIGNAL_COLUMN, *RECORD_COLUMNS}


def test_a_record_matching_no_row_fails_rather_than_disappearing(tmp_path):
    res = _apply([_row()], [_record(email="ghost@nowhere.example")])
    assert res.unmatched == ["ghost@nowhere.example"]
    assert res.failed


# --- the write ------------------------------------------------------------


MINI_MATRIX = parse_matrix(
    """---
source: manual
---
# Hook matrix — Cascade Systems

## Enterprise

| Signal → / Persona ↓ | Compliance event (audit, breach) | M&A / consolidation |
|---|---|---|
| **CISO** | "Evidence a regulator can read." | "One perimeter." |

## Startup

| Signal → / Persona ↓ | MCP / A2A in build |
|---|---|
| **CEO / Founder** | "Buy the protocol layer." |
"""
)


def test_a_valid_signal_column_is_written_when_a_matrix_is_supplied():
    res = _apply(
        [_row(segment="Enterprise")],
        [_record(signal_column="Compliance event (audit, breach)")],
        matrix=MINI_MATRIX,
    )
    assert res.applied == 1
    assert not res.refusals
    assert res.rows[0][SIGNAL_COLUMN] == "Compliance event (audit, breach)"
    assert SIGNAL_COLUMN in res.fieldnames


def test_an_invalid_signal_column_is_refused_when_a_matrix_is_supplied():
    """A Startup-only signal on an Enterprise row is refused, not silently written --
    exactly the cross-grid mistake `cell-segment-fit` already proved happens in practice."""
    res = _apply(
        [_row(segment="Enterprise")],
        [_record(signal_column="MCP / A2A in build")],
        matrix=MINI_MATRIX,
    )
    assert res.applied == 0
    assert res.failed
    assert res.refusals[0].rule == "signal-column-unknown"
    # Refused means unwritten: the row keeps its pre-record state.
    assert SIGNAL_COLUMN not in res.rows[0] or not res.rows[0].get(SIGNAL_COLUMN)


def test_signal_column_is_written_unvalidated_without_a_matrix():
    """No matrix supplied -- same opt-in convention as --hook-matrix on the linter side: a
    caller that wants the check must supply what it checks against."""
    res = _apply(
        [_row(segment="Enterprise")],
        [_record(signal_column="not a real matrix label")],
    )
    assert res.applied == 1
    assert not res.refusals
    assert res.rows[0][SIGNAL_COLUMN] == "not a real matrix label"


def test_signal_column_survives_the_round_trip_write(tmp_path):
    """The write-time guard now also protects `signal_column`, not just the eight record
    columns -- the exact class of silent loss `write_list`'s own docstring exists to catch."""
    res = _apply(
        [_row(segment="Enterprise")],
        [_record(signal_column="Compliance event (audit, breach)")],
        matrix=MINI_MATRIX,
    )
    out = tmp_path / "out.csv"
    write_list(res, out)
    back = list(csv.DictReader(out.open(newline="", encoding="utf-8")))
    assert back[0][SIGNAL_COLUMN] == "Compliance event (audit, breach)"


def test_evidence_containing_a_newline_survives_the_write(tmp_path):
    """CSV quoting is the step most likely to invent a defect the in-memory check passed."""
    evidence = (
        "Cascade Systems today published a runtime policy engine\nthat evaluates agent "
        "actions before they execute."
    )
    res = _apply([_row()], [_record(signal_evidence=evidence)])
    assert res.applied == 1
    out = tmp_path / "out.csv"
    write_list(res, out)
    back = list(csv.DictReader(out.open(newline="", encoding="utf-8")))
    assert len(back) == 1
    assert back[0]["signal_evidence"] == evidence


def test_the_partial_file_is_not_left_behind_on_success(tmp_path):
    res = _apply([_row()], [_record()])
    out = tmp_path / "out.csv"
    write_list(res, out)
    assert out.exists()
    assert not (tmp_path / "out.csv.partial").exists()
