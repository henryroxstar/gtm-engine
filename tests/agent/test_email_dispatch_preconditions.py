"""A5 — the dispatcher refuses unless the profile's own records say the load may go ahead.

`agent/email_dispatch.py` was the one path that loads people, and it never asked whether the
compliance preflight had passed (or run), whether the copy about to be loaded is the copy that
was checked, or whether a pilot-limited campaign was growing past its ceiling. It now reads those
from `history.jsonl` and `cells.toml` before it reads anything from Saleshandy.

These tests use a real tenant directory and the real `Ledgers`, with only the Saleshandy reads
and the HTTP layer faked, so the history row each refusal writes is the row the ledger holds.
Fixtures are fictional (§R9).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import email_dispatch
from gtm_core.copy_words import COPY_DIGEST_ALGO, copy_digests
from gtm_core.ledgers import Ledgers

PROFILE = "acme"
SEQ = "seqAAAA1111"

STEPS = [
    {"step_id": "st-1", "variants": [{"subject": "Quick question", "content": "<p>Hello</p>"}]},
    {"step_id": "st-2", "variants": [{"subject": "", "content": "<p>Following up</p>"}]},
]

DRAFT = {
    "tool": "import_prospects_to_sequence",
    "sequence_id": SEQ,
    "step_id": "st-1",
    "steps": STEPS,
    "prospect_list": [
        {"Email": "p0@example.test", "First Name": "Pat"},
        {"Email": "p1@example.test", "First Name": "Sam"},
    ],
}


class Harness:
    """A tenant on disk, the real Ledgers, and a Saleshandy that records what it was asked."""

    def __init__(self, root: Path, monkeypatch):
        self.root = root
        self.calls: list[str] = []
        self.network_reads = 0
        self.cfg = SimpleNamespace(saleshandy_api_key="key-for-test-only", content_root=root)
        self.ledgers = Ledgers(self.cfg, PROFILE)

        async def imp(*_a, **_k):
            self.calls.append("import")
            return '{"ok": true}'

        async def add(*_a, **_k):
            self.calls.append("add")
            return '{"ok": true}'

        async def live_copy(*_a, **_k):
            self.network_reads += 1
            return None

        async def rows(_key, prospects):
            return [{"fields": [{"id": "f", "value": p["Email"]}]} for p in prospects], None

        server = "agent.mcp.saleshandy.server"
        monkeypatch.setattr(f"{server}._import_prospects_to_sequence_request", imp)
        monkeypatch.setattr(f"{server}._add_leads_to_sequence_request", add)
        monkeypatch.setattr(email_dispatch, "_lane_refusal", lambda *_a, **_k: None)
        monkeypatch.setattr(email_dispatch, "_live_copy_refusal", live_copy)
        monkeypatch.setattr(email_dispatch, "_import_rows", rows)

    # the tenant's records -------------------------------------------------
    def history(self, *rows: dict) -> None:
        path = self.root / PROFILE / "history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def history_rows(self) -> list[dict]:
        path = self.root / PROFILE / "history.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []

    def cells(self, *entries: dict, **csvs: list[str]) -> None:
        seq_dir = self.root / PROFILE / "prospects" / "sequences"
        seq_dir.mkdir(parents=True, exist_ok=True)
        blocks = [
            "[[sequence]]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in e.items())
            for e in entries
        ]
        (seq_dir / "cells.toml").write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
        for name, emails in csvs.items():
            (seq_dir / f"{name}.csv").write_text(
                "email\n" + "".join(f"{e}\n" for e in emails), encoding="utf-8"
            )

    def run(self, draft=None, **kw):
        return asyncio.run(
            email_dispatch.dispatch_approved_enrollment(
                self.cfg, self.ledgers, draft=draft or DRAFT, **kw
            )
        )


@pytest.fixture
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def _pre(status="PASS", seq=SEQ, **extra):
    return {
        "event": "capability_asserted",
        "provider": "saleshandy",
        "sequence_id": seq,
        "status": status,
        **extra,
    }


def _staged(steps=STEPS, **extra):
    return {
        "event": "sequence_staged",
        "sequence_id": SEQ,
        "step_sha256": copy_digests(steps),
        "step_sha256_algo": COPY_DIGEST_ALGO,
        **extra,
    }


# ── the compliance check ──────────────────────────────────────────────────────


def test_no_recorded_check_means_nobody_is_loaded(h):
    out = h.run()
    assert (out.ok, out.status) == (False, "enroll_failed")
    assert out.detail.startswith("I haven't loaded anyone.")
    assert "compliance check" in out.detail
    assert h.calls == [], "a refused load must not reach the sequencer"


def test_a_refusal_is_audited_with_its_reason_and_names_no_one(h):
    h.run()
    (row,) = [r for r in h.history_rows() if r.get("event") == "enroll_failed"]
    assert row["reason_code"] == "load_preconditions"
    assert row["sequence_id"] == SEQ and row["lead_count"] == 2
    assert row["detail"].startswith("I haven't loaded anyone.")
    assert "example.test" not in json.dumps(row), "the audit row must not carry the people"


def test_a_failed_check_means_nobody_is_loaded(h):
    h.history(_pre("FAIL", detail=["FAIL [BLOCKS] saleshandy/stop_on_reply: not read"]))
    out = h.run()
    assert not out.ok and "failed" in out.detail and "stop_on_reply" in out.detail
    assert h.calls == []


def test_a_passing_check_lets_the_load_through_and_the_load_is_recorded(h):
    h.history(_pre("PASS"))
    out = h.run()
    assert (out.ok, out.status) == (True, "enrolled")
    assert h.calls == ["import"]
    assert [r["event"] for r in h.history_rows()][-1] == "enrolled"


def test_a_warning_check_lets_the_load_through(h):
    h.history(_pre("WARN"))
    assert h.run().ok


def test_the_latest_check_decides_a_later_failure_blocks_an_earlier_pass(h):
    h.history(_pre("PASS"), _pre("FAIL"))
    assert not h.run().ok and h.calls == []


def test_a_check_for_another_sequence_does_not_license_this_one(h):
    h.history(_pre("PASS", seq="seqOTHER"))
    assert not h.run().ok and h.calls == []


def test_a_check_with_a_failed_wider_preflight_blocks_even_if_the_capability_part_passed(h):
    h.history(_pre("PASS", overall="FAIL", failed_checks=["markets"]))
    out = h.run()
    assert not out.ok and "markets" in out.detail


def test_the_lead_id_tool_is_held_to_the_same_rule(h):
    draft = {
        "tool": "add_leads_to_sequence",
        "sequence_id": SEQ,
        "step_id": "st-1",
        "steps": STEPS,
        "lead_ids": [1, 2],
    }
    assert not h.run(draft).ok and h.calls == []
    h.history(_pre("PASS"))
    assert h.run(draft).ok and h.calls == ["add"]


# ── ordering, fail-closed, and what is unchanged ──────────────────────────────


def test_the_records_are_read_before_anything_is_read_from_the_sequencer(h):
    h.run()
    assert h.network_reads == 0


def test_an_unreadable_history_refuses(h):
    h.history(_pre("PASS"))
    path = h.root / PROFILE / "history.jsonl"
    path.write_text(path.read_text() + "{this line is damaged\n", encoding="utf-8")
    out = h.run()
    assert not out.ok and "history" in out.detail and h.calls == []


def test_a_dry_run_still_never_dispatches_and_needs_no_records(h):
    out = h.run(dry_run=True)
    assert out.status == "dry_run" and h.calls == []
    assert not (h.root / PROFILE / "history.jsonl").exists()


def test_no_key_is_still_not_configured(h):
    h.cfg.saleshandy_api_key = None
    assert h.run().status == "not_configured"


def test_the_other_refusals_keep_their_wording(h):
    h.history(_pre("PASS"))
    out = h.run({**DRAFT, "expires_on": "2000-01-01"})
    assert out.detail == "nothing enrolled — draft expired on 2000-01-01"
    (row,) = [r for r in h.history_rows() if r.get("event") == "enroll_failed"]
    assert "reason_code" not in row


def test_an_enrolled_row_has_exactly_the_shape_it_had_before(h):
    h.history(_pre("PASS"))
    h.run()
    row = h.history_rows()[-1]
    assert set(row) == {
        "event",
        "skill",
        "tool",
        "sequence_id",
        "step_id",
        "lead_count",
        "detail",
        "ts",
        "prev_sha256",
    }
    assert row["event"] == "enrolled" and row["detail"] is None


# ── the copy that was checked ─────────────────────────────────────────────────


def test_copy_changed_since_it_was_staged_means_nobody_is_loaded(h):
    h.history(_pre("PASS"), _staged())
    changed = json.loads(json.dumps(DRAFT))
    changed["steps"][1]["variants"][0]["content"] = "<p>Following up, now with a hard sell</p>"
    out = h.run(changed)
    assert not out.ok and "step 2" in out.detail and h.calls == []


def test_unchanged_copy_with_a_recorded_digest_goes_through(h):
    h.history(_pre("PASS"), _staged())
    assert h.run().ok


def test_a_digest_written_before_the_algorithm_existed_is_not_a_reason_to_refuse(h):
    h.history(
        _pre("PASS"),
        {
            "event": "sequence_staged",
            "sequence_id": SEQ,
            "step_sha256": ["c5029744044e63b8", "322dc5eb145b4f67"],
        },
    )
    assert h.run().ok


# ── the pilot ─────────────────────────────────────────────────────────────────


def test_with_no_pilot_field_a_large_load_is_unchanged(h):
    h.history(_pre("PASS"))
    big = {**DRAFT, "prospect_list": [{"Email": f"p{i}@example.test"} for i in range(200)]}
    assert h.run(big).ok


def test_a_pilot_ceiling_stops_a_larger_list_until_the_read_is_recorded(h):
    h.history(_pre("PASS"))
    over = {**DRAFT, "pilot_size": 1, "campaign": "camp-1"}
    out = h.run(over)
    assert not out.ok and "pilot of 1" in out.detail and h.calls == []
    h.history(_pre("PASS"), {"event": "pilot_read_ok", "campaign": "camp-1"})
    assert h.run(over).ok


def test_a_pilot_declared_in_cells_toml_counts_the_registered_list(h):
    h.history(_pre("PASS"))
    h.cells(
        {"id": SEQ, "csv": "a.csv", "spec": "s.md", "campaign": "camp-1", "pilot_size": 2},
        a=["p0@example.test", "p1@example.test", "p2@example.test"],
    )
    out = h.run()
    assert not out.ok and "camp-1" in out.detail and h.calls == []
