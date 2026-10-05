"""R6.2: what was dispatched is written down at dispatch, per person, and never before.

`enrolments.jsonl` is append-only and no older code reads it. A declined, failed or dry-run
dispatch writes nothing; a lead-id enrolment (no addresses in the draft) writes nothing and says so.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from agent import email_dispatch
from gtm_core import enrolments

DRAFT = {
    "tool": "import_prospects_to_sequence",
    "sequence_id": "seq-1",
    "step_id": "step-1",
    "steps": [
        {"step_id": "step-1", "variants": [{"subject": "s", "content": "c", "preheader": ""}]}
    ],
    "prospect_list": [
        {"Email": "Ana@Northwind.example.test", "First Name": "Ana", "Company": "Northwind"},
        {"Email": "bo@contoso.example.test", "First Name": "Bo", "Company": "Contoso"},
    ],
    "card_ids": ["cell-a"],
    "profile": "realshape",
}
ATTRIBUTION = [
    {
        "email": "ana@northwind.example.test",
        "signal_class": "source_list",
        "premise_via": "source",
        "source_id": "reg-1",
    }
]


def _lines(root):
    path = root / "realshape" / "prospects" / "enrolments.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_each_person_gets_one_line_with_the_class_it_was_approved_under(tmp_path):
    n = enrolments.record(
        tmp_path, "realshape", {**DRAFT, "attribution": ATTRIBUTION}, now="2026-10-01T09:00:00Z"
    )
    rows = _lines(tmp_path)
    assert n == 2 and [r["email"] for r in rows] == [
        "ana@northwind.example.test",
        "bo@contoso.example.test",
    ]
    ana, bo = rows
    assert (ana["signal_class"], ana["premise_via"], ana["source_id"]) == (
        "source_list",
        "source",
        "reg-1",
    )
    assert (bo["signal_class"], bo["premise_via"], bo["source_id"]) == ("", "", "")
    assert ana["sequence_id"] == "seq-1" and ana["cell_id"] == "cell-a"
    assert ana["dispatched_at"] == "2026-10-01T09:00:00Z"


def test_a_draft_with_no_addresses_writes_nothing(tmp_path):
    lead_draft = {
        "tool": "add_leads_to_sequence",
        "sequence_id": "s",
        "step_id": "t",
        "lead_ids": ["1"],
    }
    assert enrolments.record(tmp_path, "realshape", lead_draft) == 0
    assert _lines(tmp_path) == []


def test_the_file_only_ever_grows(tmp_path):
    enrolments.record(tmp_path, "realshape", DRAFT)
    enrolments.record(tmp_path, "realshape", DRAFT)
    assert len(_lines(tmp_path)) == 4


def test_an_unsafe_profile_is_refused_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError):
        enrolments.record(tmp_path, "../x", DRAFT)


# The dispatcher: only a successful enrolment stamps.


class _Ledgers(SimpleNamespace):
    def append_history(self, rec):
        self.history.append(rec)


def _dispatch(tmp_path, monkeypatch, *, response, dry_run=False, draft=None):
    async def ok(*_a, **_k):
        return None

    monkeypatch.setattr(email_dispatch, "_lane_refusal", lambda *_a, **_k: None)
    monkeypatch.setattr(email_dispatch, "_live_copy_refusal", ok)
    # The records rule has its own tests (tests/agent/test_email_dispatch_preconditions.py).
    monkeypatch.setattr(email_dispatch, "_load_refusal", lambda *_a, **_k: None)

    async def rows(_k, lst):
        return lst, None

    monkeypatch.setattr(email_dispatch, "_import_rows", rows)

    async def imp(*_a, **_k):
        return response

    monkeypatch.setattr("agent.mcp.saleshandy.server._import_prospects_to_sequence_request", imp)
    cfg = SimpleNamespace(saleshandy_api_key="k", content_root=tmp_path)
    ledgers = _Ledgers(history=[], profile="realshape", content_root=tmp_path)
    out = asyncio.run(
        email_dispatch.dispatch_approved_enrollment(
            cfg, ledgers, draft=draft or DRAFT, dry_run=dry_run
        )
    )
    return out


def test_a_successful_enrolment_stamps_every_person(tmp_path, monkeypatch):
    out = _dispatch(tmp_path, monkeypatch, response="imported 2")
    assert out.ok and len(_lines(tmp_path)) == 2


def test_a_failed_enrolment_stamps_nothing(tmp_path, monkeypatch):
    out = _dispatch(tmp_path, monkeypatch, response="[saleshandy-error] 500")
    assert not out.ok and _lines(tmp_path) == []


def test_a_dry_run_stamps_nothing(tmp_path, monkeypatch):
    out = _dispatch(tmp_path, monkeypatch, response="imported 2", dry_run=True)
    assert out.status == "dry_run" and _lines(tmp_path) == []


def test_a_refused_enrolment_stamps_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(email_dispatch, "_lane_refusal", lambda *_a, **_k: "lane says no")
    out = asyncio.run(
        email_dispatch.dispatch_approved_enrollment(
            SimpleNamespace(saleshandy_api_key="k", content_root=tmp_path),
            _Ledgers(history=[], profile="realshape", content_root=tmp_path),
            draft=DRAFT,
        )
    )
    assert not out.ok and _lines(tmp_path) == []
