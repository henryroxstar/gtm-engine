"""Tests for gtm_core.signals — structured signal events + history dedup."""

from __future__ import annotations

import json

from gtm_core import signals
from gtm_core.ledgers import Ledgers


class _Cfg:
    def __init__(self, content_root):
        self.content_root = content_root


def test_suggested_action_map_and_default():
    assert signals.suggested_action("meeting_request") == "propose_booking_link"
    assert signals.suggested_action("BUYER_INTENT") == "escalate_to_operator"
    assert signals.suggested_action("something_unknown") == "review"


def test_signal_id_is_stable_and_scoped():
    a = signals.signal_id("Dana @ Acme", "meeting_request", "syften")
    assert a == signals.signal_id("Dana @ Acme", "meeting_request", "syften")
    assert a != signals.signal_id("Dana @ Acme", "buyer_intent", "syften")
    assert a.startswith("sig_")


def test_build_signal_attaches_action():
    s = signals.build_signal("Dana", "meeting_request", "syften", ts="2026-07-20T00:00:00Z")
    assert s["suggested_action"] == "propose_booking_link"
    assert s["id"].startswith("sig_")
    assert s["ts"] == "2026-07-20T00:00:00Z"


def test_new_signals_dedupes_against_history(tmp_path):
    profile = "example"
    hist = tmp_path / profile / "history.jsonl"
    ledgers = Ledgers(_Cfg(tmp_path), profile)

    s1 = signals.build_signal("Dana", "meeting_request", "syften", ts="2026-07-20T00:00:00Z")
    s2 = signals.build_signal("Ravi", "buyer_intent", "vibe", ts="2026-07-20T00:00:00Z")

    # First pass: both are new.
    fresh = signals.new_signals([s1, s2], hist)
    assert {x["id"] for x in fresh} == {s1["id"], s2["id"]}

    signals.record_signals(ledgers, fresh)

    # Second pass: same signals are now seen → dropped.
    assert signals.new_signals([s1, s2], hist) == []

    # The recorded rows carry the id in source_items (radar dedup convention).
    rows = [json.loads(x) for x in hist.read_text().splitlines() if x.strip()]
    assert all(r["event"] == "signal" for r in rows)
    assert s1["id"] in rows[0]["source_items"]


def test_new_signals_dedupes_within_one_batch(tmp_path):
    hist = tmp_path / "example" / "history.jsonl"
    s = signals.build_signal("Dana", "meeting_request", "syften", ts="2026-07-20T00:00:00Z")
    # Same signal appearing twice in one pull collapses to one.
    assert len(signals.new_signals([s, dict(s)], hist)) == 1
