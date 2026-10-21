"""A hold decision re-applied with a corrected ``detail`` supersedes; it is not "already".

The router honours a recorded decision only when its ``detail`` equals the live raw detail
(`router._apply_decision`). `plan_apply` used to call an entry "already recorded" whenever the
DECISION matched, whatever the detail said, so a decision first recorded against the wrong
detail — the composed ``lane_reason`` the hold sheet exported — could never be corrected by
re-applying it: it was skipped, and the router went on ignoring the stale one. The 2026-09-25
restage worked around it by recording a different decision first and then the right one.

Second half: the hold sheet exported the composed ``lane_reason`` AS ``detail``, which is how
the wrong detail got recorded in the first place. Fictional fixtures only (§R9).
"""

from __future__ import annotations

import csv
import json

from gtm_core import lanes
from gtm_core.lanes import decisions as dec
from gtm_core.lanes.model import Routed
from gtm_core.lanes.router import _apply_decision
from gtm_core.lanes.sheet import build_sheet_payload

RAW = "tier A on the generic email"
COMPOSED = f"hold:tier-a-generic — {RAW}"
KEY = ("tier-a-generic", "d:brightpath.example")


def _entry(**kw) -> dec.DecisionEntry:
    base = {
        "email": "a@brightpath.example",
        "trigger": "tier-a-generic",
        "account_key": "d:brightpath.example",
        "decision": "generic",
        "detail": RAW,
    }
    base.update(kw)
    return dec.DecisionEntry(**base)


def test_same_decision_with_a_different_detail_is_a_detail_update():
    prior = {KEY: {"decision": "generic", "detail": COMPOSED, "stamp": "2026-09-24"}}
    plan = dec.plan_apply([_entry()], prior)
    assert [e.email for e in plan.detail_updated] == ["a@brightpath.example"]
    assert not plan.already and not plan.generic and not plan.conflicts
    assert "detail updated 1" in plan.render()


def test_identical_reapply_still_skips():
    prior = {KEY: {"decision": "generic", "detail": RAW, "stamp": "2026-09-24"}}
    plan = dec.plan_apply([_entry()], prior)
    assert [e.email for e in plan.already] == ["a@brightpath.example"]
    assert not plan.detail_updated


def test_prior_with_no_detail_key_is_already_honoured_for_any_detail():
    """Mirrors the router: a record with no ``detail`` key answers every detail."""
    prior = {KEY: {"decision": "generic", "stamp": "2026-08-27"}}
    plan = dec.plan_apply([_entry()], prior)
    assert plan.already and not plan.detail_updated


def _write_hold(path, rows):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=dec.HOLD_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def test_detail_update_is_recorded_and_the_router_then_honours_it(tmp_path, monkeypatch):
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))
    ledger = dec.decisions_path("acme")
    ledger.parent.mkdir(parents=True)
    stale = {
        "stamp": "2026-09-24",
        "email": "a@brightpath.example",
        "trigger": "tier-a-generic",
        "account_key": "d:brightpath.example",
        "decision": "generic",
        "salvage_kind": "",
        "note": "",
        "detail": COMPOSED,
    }
    ledger.write_text(json.dumps(stale) + "\n", encoding="utf-8")

    filled = ledger.with_name("hold-2026-09-25.csv")
    _write_hold(
        filled,
        [
            {
                "email": "a@brightpath.example",
                "company": "Brightpath",
                "company_domain": "brightpath.example",
                "trigger": "tier-a-generic",
                "lane_reason": COMPOSED,
                "detail": RAW,
                "decision": "generic",
            }
        ],
    )
    argv = ["hold-apply", "--profile", "acme", "--decisions", str(filled), "--apply"]
    assert lanes.main([*argv, "--stamp", "2026-09-25"]) == 0
    rows = dec._read_jsonl(ledger)
    assert len(rows) == 2, "the stale row stays (append-only); a superseding row is added"
    assert rows[-1]["detail"] == RAW and rows[-1]["decision"] == "generic"

    routed = Routed(row={"company_domain": "brightpath.example"}, lane="hold")
    assert _apply_decision(routed, "tier-a-generic", RAW, _NoPolicy(), dec.read_decisions(ledger))
    assert routed.lane == "generic"

    # A second identical apply is now truly "already" and records nothing.
    assert lanes.main([*argv, "--stamp", "2026-09-26"]) == 0
    assert len(dec._read_jsonl(ledger)) == 2


class _NoPolicy:
    policy_auto: dict = {}


def test_hold_sheet_exports_the_raw_detail_not_the_composed_lane_reason(tmp_path):
    held = [
        Routed(
            row={"email": "a@brightpath.example", "title": "CTO", "company": "Brightpath"},
            lane="hold",
            trigger="tier-a-generic",
            detail=RAW,
        ),
        # A flagged row: its lane_reason carries a trailing "[contested]" that prefix
        # stripping alone would leave inside the detail.
        Routed(
            row={"email": "b@copperfield.example", "title": "CTO", "company": "Copperfield"},
            lane="hold",
            trigger="tier-a-generic",
            detail=RAW,
            flags=["contested"],
        ),
    ]
    path = tmp_path / "hold-2026-09-25.csv"
    dec.write_hold_csv(held, path, {})
    hold_rows = list(csv.DictReader(path.open(encoding="utf-8")))
    _groups, rows = build_sheet_payload(hold_rows)
    assert [r["detail"] for r in rows] == [RAW, RAW]
    # ... and a filled hold CSV reads back the same raw detail.
    assert [e.detail for e in dec.read_filled(path)] == [RAW, RAW]


def test_a_detail_less_lane_reason_reads_back_as_an_empty_detail():
    """A held row with no detail composes ``lane_reason`` as the bare ``hold:<trigger>``;
    read back from a legacy hold CSV (no ``detail`` column) that is an empty detail, not the
    head string the router would never compute."""
    row = {"trigger": "prior-contact", "lane_reason": "hold:prior-contact"}
    assert dec._raw_detail(row) == ""
