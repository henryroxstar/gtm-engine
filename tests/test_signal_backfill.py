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


@pytest.fixture(autouse=True)
def _setup_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    from gtm_core.signal_sources import store_capture

    text = (
        "Cascade Systems today published a runtime policy engine that evaluates agent "
        "actions before they execute."
    )
    store_capture(
        "https://cascade.example/news/policy-engine",
        text,
        sources_dir=tmp_path / "sources",
    )


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


# --- the ready-to-load.csv shape: why_now, no signal_clause column --------

READY_TO_LOAD_FIELDNAMES = ["first", "last", "email", "title", "company", "why_now"]


def _ready_to_load_row(**kw) -> dict:
    base = {
        "first": "Rowan",
        "last": "Vaske",
        "email": "rowan@cascade.example",
        "title": "Chief Information Security Officer",
        "company": "Cascade Systems",
        "why_now": "Cascade Systems published a runtime policy engine for agent actions",
    }
    base.update(kw)
    return base


def test_re_angle_on_a_why_now_only_list_clears_why_now_not_signal_clause():
    """ready-to-load.csv has no signal_clause column -- its clause lives in why_now. A
    re-angle verdict with no clause supplied must blank the stale sentence a human actually
    reads, not a column that does not exist on this file."""
    res = apply_records(
        [_ready_to_load_row()],
        READY_TO_LOAD_FIELDNAMES,
        {
            "rowan@cascade.example": {
                "email": "rowan@cascade.example",
                "verdict": "re-angle",
                "verdict_reason": "source no longer supports the claim",
                "category_relation": "prospect",
            }
        },
        as_of=AS_OF,
    )
    assert res.applied == 1
    assert res.cleared == 1
    assert not res.refusals
    assert res.rows[0]["why_now"] == ""
    assert "signal_clause" not in res.fieldnames


def test_send_on_a_why_now_only_list_writes_the_verified_clause_into_why_now():
    """A send verdict with a supplied, verified clause must land in why_now -- the field
    that actually renders into outreach copy -- not disappear into an unused signal_clause
    key that this file's header never had."""
    new_clause = "Cascade Systems published a runtime policy engine for agent actions"
    res = apply_records(
        [_ready_to_load_row(why_now="stale sentence nobody re-checked")],
        READY_TO_LOAD_FIELDNAMES,
        {"rowan@cascade.example": _record(signal_clause=new_clause)},
        as_of=AS_OF,
    )
    assert res.applied == 1
    assert not res.refusals
    assert res.rows[0]["why_now"] == new_clause
    assert "signal_clause" not in res.fieldnames


def test_a_why_now_only_list_round_trips_the_mirrored_clause(tmp_path):
    res = apply_records(
        [_ready_to_load_row()],
        READY_TO_LOAD_FIELDNAMES,
        {
            "rowan@cascade.example": {
                "email": "rowan@cascade.example",
                "verdict": "drop",
                "verdict_reason": "no source found",
                "category_relation": "prospect",
            }
        },
        as_of=AS_OF,
    )
    out = tmp_path / "out.csv"
    write_list(res, out)
    back = list(csv.DictReader(out.open(newline="", encoding="utf-8")))
    assert back[0]["why_now"] == ""
    assert "signal_clause" not in (back[0].keys())


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
    assert set(RECORD_INPUT_FIELDS) == {
        "email",
        "signal_clause",
        SIGNAL_COLUMN,
        "hook_cell",
        *RECORD_COLUMNS,
    }


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


# --- derived-target guard -------------------------------------------------


def test_refuses_to_write_a_regenerated_pool_artifact():
    """A record written into a build output is discarded by the next sweep.

    The 2026-08-29 incident this guards: 34 verified records were written into
    `ready-to-load.csv`, vanished, were diagnosed as a concurrency race, re-applied under
    `gtm_core.locks.profile_lock`, and vanished again. A lock serialises writers; it does
    nothing about a file `consolidate` rebuilds from `master-list.csv`, the exports and
    `latest.json`. The refusal names the durable path instead of just saying no.
    """
    from gtm_core.signal_backfill import refuse_derived_target

    for name in (
        "ready-to-load.csv",
        "ready-to-load-signal.csv",
        "ready-to-load-generic.csv",
        "master-list.csv",
        "needs-verification.csv",
    ):
        reason = refuse_derived_target(Path("/anywhere") / name)
        assert reason, f"{name} must be refused"
        assert "--promote" in reason, "refusal must name the durable path"


def test_allows_the_default_side_by_side_output():
    """Negative control: the guard must not block the safe default or a scratch path.

    `--out` defaults to `<list>-recorded.csv`, a NEW file that nothing regenerates. A guard
    that also refused that would make the tool unusable, so this pins the boundary rather
    than only asserting the refusal above.
    """
    from gtm_core.signal_backfill import refuse_derived_target

    assert refuse_derived_target(Path("ready-to-load-recorded.csv")) == ""
    assert refuse_derived_target(Path("/tmp/scratch.csv")) == ""


# --- promotion to the durable home ---------------------------------------


def test_promote_builds_a_full_copy_item_per_account(tmp_path, monkeypatch):
    """A promoted item must be the account's WHOLE record with the fields replaced.

    ``upsert_latest`` replaces an item wholesale apart from its sticky fields, so a partial
    item silently drops score, tier and contact data. This pins that the untouched fields
    survive alongside the new record.
    """
    import json as _json

    from gtm_core import signal_backfill as sb

    pdir = tmp_path / "acme" / "prospects"
    pdir.mkdir(parents=True)
    (pdir / "latest.json").write_text(
        _json.dumps(
            {
                "kind": "prospects",
                "profile": "acme",
                "items": [
                    {
                        "company": "Northwind",
                        "account_id": "a-1",
                        "tier": "A",
                        "score": 9,
                        "contact_name": "Jo Chen",
                        "status": "contacted",
                    }
                ],
            }
        )
    )
    rows = [{"email": "jo@northwind.example", "account_id": "a-1", "company": "Northwind"}]
    records = {
        "jo@northwind.example": {
            "email": "jo@northwind.example",
            "signal_source_url": "https://northwind.example/partner",
            "signal_observed": "2026-06-01",
            "signal_evidence": "Northwind names Contoso as its agent partner.",
            "signal_subject": "Northwind",
            "signal_agent_kind": "ai",
            "category_relation": "prospect",
            "verdict": "send",
        }
    }
    items, orphans = sb.promote_records(records, rows, "acme", content_root=tmp_path)

    assert orphans == []
    (item,) = items
    assert item["signal_source_url"] == "https://northwind.example/partner"
    assert item["verdict"] == "send"
    # ...and the account's own fields are still there.
    assert item["tier"] == "A" and item["score"] == 9
    assert item["contact_name"] == "Jo Chen" and item["status"] == "contacted"


def test_promote_reports_a_record_with_no_account_instead_of_dropping_it(tmp_path):
    """A record with nowhere durable to live must be RETURNED, never silently skipped.

    This is the 2026-08-29 shape: 8 pool rows had no ledger account at all, so their
    records could not be promoted. Reporting zero while quietly discarding them is how the
    same batch gets lost a third time.
    """
    import json as _json

    from gtm_core import signal_backfill as sb

    pdir = tmp_path / "acme" / "prospects"
    pdir.mkdir(parents=True)
    (pdir / "latest.json").write_text(
        _json.dumps({"kind": "prospects", "profile": "acme", "items": []})
    )

    rows = [{"email": "jo@northwind.example", "account_id": "", "company": "Northwind"}]
    records = {"jo@northwind.example": {"email": "jo@northwind.example", "verdict": "send"}}
    items, orphans = sb.promote_records(records, rows, "acme", content_root=tmp_path)

    assert items == []
    assert len(orphans) == 1 and "no account" in orphans[0][1]


def test_promote_stamps_the_research_date_on_a_record_that_carries_a_verdict(tmp_path):
    """`verdict_on` is what lets a newer research verdict lift a pool row's re-angle; only
    a record that carries a verdict gets one, and it is the promotion's own date."""
    import json as _json

    from gtm_core import signal_backfill as sb

    pdir = tmp_path / "acme" / "prospects"
    pdir.mkdir(parents=True)
    (pdir / "latest.json").write_text(
        _json.dumps(
            {
                "kind": "prospects",
                "profile": "acme",
                "items": [
                    {"company": "Northwind", "account_id": "a-1", "verdict": "re-angle"},
                    {"company": "Fabrikam", "account_id": "a-2"},
                ],
            }
        )
    )
    rows = [
        {"email": "jo@northwind.example", "account_id": "a-1"},
        {"email": "al@fabrikam.example", "account_id": "a-2"},
    ]
    records = {
        "jo@northwind.example": {"verdict": "send", "signal_observed": "2026-06-01"},
        "al@fabrikam.example": {"signal_observed": "2026-06-01"},
    }
    items, _ = sb.promote_records(records, rows, "acme", content_root=tmp_path, today="2026-09-25")
    by_id = {i["account_id"]: i for i in items}
    assert by_id["a-1"]["verdict_on"] == "2026-09-25"
    assert "verdict_on" not in by_id["a-2"]
