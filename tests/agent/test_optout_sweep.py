"""Tests for the deterministic (non-LLM) opt-out sweep orchestrator (agent.optout_sweep).

Every external call is monkeypatched — no real Saleshandy HTTP call, no real Telegram
push, no wall-clock time. This exercises the wiring only: does a threads payload with
an opt-out reply end up escalated, logged, and watermarked; does a wrapper failure
propagate as a nonzero exit rather than a silent "0 found".
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from agent import optout_sweep


@dataclasses.dataclass
class _Cfg:
    content_root: Path
    profiles_root: Path = Path("/nonexistent")
    telegram_bot_token: str = "test-token"


def _fake_ledgers_module(monkeypatch, records: list):
    class _FakeLedgers:
        def __init__(self, cfg, profile):
            pass

        def append_history(self, record):
            records.append(record)

    monkeypatch.setattr(optout_sweep, "Ledgers", _FakeLedgers)


_CLEAN_THREADS = json.dumps({"message": "ok", "payload": {"threads": []}})


def _threads_payload(*threads):
    return json.dumps({"message": "ok", "payload": {"threads": list(threads)}})


def _thread_detail(thread_id, subject, messages):
    return json.dumps(
        {"message": "ok", "payload": {"id": thread_id, "subject": subject, "messages": messages}}
    )


def test_clean_inbox_finds_nothing_and_exits_0(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    async def fake_get_inbox_threads(**kw):
        return _CLEAN_THREADS

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)

    cfg = _Cfg(content_root=tmp_path)
    rc = asyncio.run(optout_sweep.run("example", cfg=cfg))
    assert rc == 0
    assert records == []


def test_optout_reply_is_escalated_and_logged(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    thread = {"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}

    async def fake_get_inbox_threads(**kw):
        return _threads_payload(thread)

    async def fake_get_thread(thread_id):
        assert thread_id == "t1"
        return _thread_detail(
            "t1",
            "re: agents on regulated data",
            [
                {
                    "from": "jordan@brackenhealth.example",
                    "body": "Unsubscribe",
                    "direction": "inbound",
                }
            ],
        )

    pushed: list = []

    async def fake_push_optout_alert(cfg, profiles_root, profile, match):
        pushed.append((profile, match.email))

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_thread", fake_get_thread)
    monkeypatch.setattr("agent.gate_notify.push_optout_alert", fake_push_optout_alert)

    cfg = _Cfg(content_root=tmp_path)
    rc = asyncio.run(optout_sweep.run("example", cfg=cfg))

    assert rc == 0
    assert pushed == [("example", "jordan@brackenhealth.example")]
    assert len(records) == 1
    assert records[0]["event"] == "optout_detected"
    assert records[0]["email"] == "jordan@brackenhealth.example"
    assert records[0]["escalated"] is True


def test_escalation_failure_still_logs_and_does_not_abort(tmp_path, monkeypatch):
    """A dead Telegram token must not swallow the finding — the ledger is the fallback
    trail, matching the "never let a silent failure lose the finding" design."""
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    thread = {"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}

    async def fake_get_inbox_threads(**kw):
        return _threads_payload(thread)

    async def fake_get_thread(thread_id):
        return _thread_detail(
            "t1", "s", [{"from": "a@b.com", "body": "unsubscribe", "direction": "inbound"}]
        )

    async def fake_push_optout_alert(*a, **kw):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_thread", fake_get_thread)
    monkeypatch.setattr("agent.gate_notify.push_optout_alert", fake_push_optout_alert)

    cfg = _Cfg(content_root=tmp_path)
    rc = asyncio.run(optout_sweep.run("example", cfg=cfg))

    assert rc == 0
    assert len(records) == 1
    assert records[0]["escalated"] is False


def test_wrapper_failure_on_inbox_fetch_is_a_hard_failure(tmp_path, monkeypatch):
    """`[saleshandy-error] …` must exit nonzero, never be read as "0 opt-outs found" —
    the two endpoints are unverified live (server.py `# VERIFY:`), so a wrong path or a
    missing key must be loud, not silently reported clean."""
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    async def fake_get_inbox_threads(**kw):
        return "[saleshandy-error] SALESHANDY_API_KEY is not set"

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)

    cfg = _Cfg(content_root=tmp_path)
    rc = asyncio.run(optout_sweep.run("example", cfg=cfg))
    assert rc == 1
    assert records == []


def test_a_bad_get_thread_call_is_skipped_not_fatal(tmp_path, monkeypatch):
    """One malformed thread detail must not sink the whole sweep — the other
    candidates in the batch still get checked."""
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    threads = [
        {"id": "bad", "lastMessageAt": "2026-08-11T13:00:00Z"},
        {"id": "good", "lastMessageAt": "2026-08-11T14:00:00Z"},
    ]

    async def fake_get_inbox_threads(**kw):
        return _threads_payload(*threads)

    async def fake_get_thread(thread_id):
        if thread_id == "bad":
            return "[saleshandy-error] HTTP 404"
        return _thread_detail(
            "good", "s", [{"from": "a@b.com", "body": "unsubscribe", "direction": "inbound"}]
        )

    async def fake_push_optout_alert(*a, **kw):
        return None

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_thread", fake_get_thread)
    monkeypatch.setattr("agent.gate_notify.push_optout_alert", fake_push_optout_alert)

    cfg = _Cfg(content_root=tmp_path)
    rc = asyncio.run(optout_sweep.run("example", cfg=cfg))

    assert rc == 0
    # The good thread is still found...
    assert [r["event"] for r in records] == ["optout_scan_failed", "optout_detected"]
    assert records[1]["email"] == "a@b.com"
    # ...and the unscannable one leaves a durable row rather than vanishing: the
    # watermark advances past it, so nothing else will ever retry it.
    assert records[0]["thread_id"] == "bad"


def test_watermark_advances_so_a_second_run_does_not_reescalate(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    thread = {"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}

    async def fake_get_inbox_threads(**kw):
        return _threads_payload(thread)

    async def fake_get_thread(thread_id):
        return _thread_detail(
            "t1", "s", [{"from": "a@b.com", "body": "unsubscribe", "direction": "inbound"}]
        )

    async def fake_push_optout_alert(*a, **kw):
        return None

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_thread", fake_get_thread)
    monkeypatch.setattr("agent.gate_notify.push_optout_alert", fake_push_optout_alert)

    cfg = _Cfg(content_root=tmp_path)
    asyncio.run(optout_sweep.run("example", cfg=cfg))
    assert len(records) == 1

    # Second run against the same (unchanged) inbox must not re-find/re-escalate t1.
    asyncio.run(optout_sweep.run("example", cfg=cfg))
    assert len(records) == 1


def test_content_root_is_profile_scoped(tmp_path, monkeypatch):
    """The watermark file must land under content/<profile>/… — never a shared path a
    second profile's sweep could collide on."""
    _fake_ledgers_module(monkeypatch, [])

    async def fake_get_inbox_threads(**kw):
        return _CLEAN_THREADS

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)

    cfg = _Cfg(content_root=tmp_path)
    asyncio.run(optout_sweep.run("example", cfg=cfg))
    expected = (
        tmp_path / "example" / "prospects" / "sequences" / ".watchstate" / "optout-watch.json"
    )
    assert expected.exists()


def test_a_full_first_page_is_followed_to_exhaustion(tmp_path, monkeypatch):
    """A full page means "there may be more". Stopping there would advance the watermark
    past page 2's threads and strand them permanently — the silent miss this whole
    module exists to prevent."""
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    page1 = [
        {"id": f"t{i}", "lastMessageAt": "2026-08-11T14:00:00Z"}
        for i in range(optout_sweep._PAGE_SIZE)
    ]
    page2 = [{"id": "late", "lastMessageAt": "2026-08-11T09:00:00Z"}]
    seen_pages: list = []

    async def fake_get_inbox_threads(page=1, **kw):
        seen_pages.append(page)
        return _threads_payload(*(page1 if page == 1 else page2))

    async def fake_get_thread(thread_id):
        body = "unsubscribe" if thread_id == "late" else "thanks, will review"
        return _thread_detail(
            thread_id, "s", [{"from": "a@b.com", "body": body, "direction": "inbound"}]
        )

    async def fake_push_optout_alert(*a, **kw):
        return None

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_thread", fake_get_thread)
    monkeypatch.setattr("agent.gate_notify.push_optout_alert", fake_push_optout_alert)

    rc = asyncio.run(optout_sweep.run("example", cfg=_Cfg(content_root=tmp_path)))
    assert rc == 0
    assert seen_pages == [1, 2]
    # The opt-out lived on page 2, below page 1's newest timestamp. Filter by event:
    # page 1's threads are ordinary replies, which the sweep now also records as
    # `reply_received` signals (W1b), so a bare len() would assert the wrong thing.
    optouts = [r for r in records if r.get("event") == "optout_detected"]
    assert len(optouts) == 1
    assert optouts[0]["thread_id"] == "late"


def test_an_inbox_past_the_page_cap_fails_loud_rather_than_banking_a_partial_sweep(
    tmp_path, monkeypatch
):
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    full = [
        {"id": f"t{i}", "lastMessageAt": "2026-08-11T14:00:00Z"}
        for i in range(optout_sweep._PAGE_SIZE)
    ]

    async def fake_get_inbox_threads(**kw):
        return _threads_payload(*full)

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)

    rc = asyncio.run(optout_sweep.run("example", cfg=_Cfg(content_root=tmp_path)))
    assert rc == 1
    # Critically: no watermark written, so the next sweep re-covers this ground.
    assert not (
        tmp_path / "example" / "prospects" / "sequences" / ".watchstate" / "optout-watch.json"
    ).exists()


def test_main_requires_profile_flag():
    with pytest.raises(SystemExit):
        optout_sweep.main([])


# ── W1b: ordinary replies become signals, not silence ────────────────────────
#
# Before this, a thread that came back saying "sure, send pricing" was fetched, read for
# opt-out language, found clean, and DISCARDED — while gtm_core.signals mapped
# reply_received -> draft_reply with no producer anywhere in the tree. These assert the
# producer exists and behaves: one signal per new thread, deduped, and never for an
# opt-out (which has its own escalation path and must not also read as a warm reply).
#
# Fixtures are fictional per docs/RULES.md §R9.


def _run_sweep(tmp_path, monkeypatch, records, threads, bodies):
    async def fake_get_inbox_threads(**kw):
        return _threads_payload(*threads)

    async def fake_get_thread(thread_id):
        return _thread_detail(
            thread_id,
            "Re: your note",
            [{"from": bodies[thread_id][0], "body": bodies[thread_id][1], "direction": "inbound"}],
        )

    async def fake_push(*a, **kw):
        return None

    monkeypatch.setattr("agent.mcp.saleshandy.server.get_inbox_threads", fake_get_inbox_threads)
    monkeypatch.setattr("agent.mcp.saleshandy.server.get_thread", fake_get_thread)
    monkeypatch.setattr("agent.gate_notify.push_optout_alert", fake_push)
    return asyncio.run(optout_sweep.run("example", cfg=_Cfg(content_root=tmp_path)))


def test_a_non_optout_reply_is_recorded_as_a_reply_received_signal(tmp_path, monkeypatch):
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    rc = _run_sweep(
        tmp_path,
        monkeypatch,
        records,
        [{"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}],
        {"t1": ("dana@acme.example", "sure, send over pricing")},
    )
    assert rc == 0
    signals = [r for r in records if r.get("event") == "signal"]
    assert len(signals) == 1, f"expected exactly one signal, got {records}"
    assert signals[0]["signal_type"] == "reply_received"
    assert signals[0]["who"] == "dana@acme.example"
    # The whole point of the map: a reply routes to a drafted (gated) response.
    assert signals[0]["suggested_action"] == "draft_reply"
    # Recorded with its id in source_items so the next pass dedups against it.
    assert signals[0]["source_items"] and signals[0]["source_items"][0].startswith("sig_")


def test_an_optout_reply_does_not_also_become_a_warm_reply_signal(tmp_path, monkeypatch):
    """An unsubscribe is not a lead. It has its own escalation; double-recording it would
    put an opt-out into the draft-a-reply queue, which is the one place it must never go."""
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    rc = _run_sweep(
        tmp_path,
        monkeypatch,
        records,
        [{"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}],
        {"t1": ("dana@acme.example", "please unsubscribe me")},
    )
    assert rc == 0
    assert [r["event"] for r in records if r.get("event") == "optout_detected"]
    assert not [r for r in records if r.get("event") == "signal"]


def test_the_same_thread_is_not_re_signalled_on_a_later_sweep(tmp_path, monkeypatch):
    """Dedup is against history.jsonl, so it survives a restart — a thread sitting in the
    inbox does not re-alert every 4 hours. Uses the REAL Ledgers so the file is written."""
    threads = [{"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}]
    bodies = {"t1": ("dana@acme.example", "sure, send over pricing")}

    rc = _run_sweep(tmp_path, monkeypatch, [], threads, bodies)
    assert rc == 0
    history = tmp_path / "example" / "history.jsonl"
    assert history.exists(), "the real Ledgers should have written history.jsonl"
    first = [json.loads(line) for line in history.read_text().splitlines()]
    assert len([r for r in first if r.get("event") == "signal"]) == 1

    # Second sweep, same thread, watermark reset so the thread is re-scanned.
    (
        tmp_path / "example" / "prospects" / "sequences" / ".watchstate" / "optout-watch.json"
    ).unlink()
    rc = _run_sweep(tmp_path, monkeypatch, [], threads, bodies)
    assert rc == 0
    second = [json.loads(line) for line in history.read_text().splitlines()]
    assert len([r for r in second if r.get("event") == "signal"]) == 1, (
        "the same thread was signalled twice — history dedup is not working"
    )


# --- W4: the sweep also dispatches what it records ---------------------------


def test_a_recorded_signal_is_dispatched_in_the_same_sweep(tmp_path, monkeypatch):
    """W1b gave the signal map a producer; W4 gives it a consumer.

    Without this call a `reply_received` sits in history.jsonl until a human notices —
    which is the wiring gap this whole PRD is about, one layer down from where it started.
    """
    records: list = []
    _fake_ledgers_module(monkeypatch, records)
    dispatched: list[str] = []

    async def fake_dispatch(profile, *, cfg=None, **kw):
        dispatched.append(profile)
        return 0

    monkeypatch.setattr("agent.signal_dispatch.dispatch", fake_dispatch)

    rc = _run_sweep(
        tmp_path,
        monkeypatch,
        records,
        [{"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}],
        {"t1": ("dana@acme.example", "sure, send over pricing")},
    )
    assert rc == 0
    assert dispatched == ["example"], "the sweep recorded a signal and never dispatched it"


def test_a_dispatch_failure_does_not_abort_the_sweep(tmp_path, monkeypatch):
    """Opt-out detection is the compliance-critical half and must survive the other one.

    A dispatch raising must leave the sweep's exit code, its opt-out escalation, and its
    watermark untouched — the signals stay in history.jsonl for the next run.
    """
    records: list = []
    _fake_ledgers_module(monkeypatch, records)

    async def boom(profile, *, cfg=None, **kw):
        raise RuntimeError("pack run exploded")

    monkeypatch.setattr("agent.signal_dispatch.dispatch", boom)

    rc = _run_sweep(
        tmp_path,
        monkeypatch,
        records,
        [{"id": "t1", "lastMessageAt": "2026-08-11T14:00:00Z"}],
        {"t1": ("dana@acme.example", "please unsubscribe me")},
    )
    assert rc == 0
    assert [r for r in records if r.get("event") == "optout_detected"], (
        "a dispatch failure suppressed the opt-out record — the one thing that must not happen"
    )
