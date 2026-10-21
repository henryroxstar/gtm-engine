"""Characterization tests for the reply gate (stage → approve/cancel).

Mirrors test_publish_gate.py for the inbound-triage reply analogue: staging binds the
Approve button to ``reply_content_hash(body, thread_id)[:16]``, approval re-verifies the
hash and pops the token (double-click safe), the default (no transport) path STAGES the
reply for manual send, and every approval logs a ``reply`` outcome + a history row from
Python — never the brain. Drives go through the Cockpit surface (``on_text`` stages,
``on_callback`` approves).
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    callback_update,
    make_cfg,
    reply_block,
    stream_of,
    text_update,
)

from agent.reply import ReplySender, ReplySettings, content_hash  # noqa: E402
from cockpit import bot as botmod  # noqa: E402
from cockpit.gates import _REPLY_NO_PREFIX, _REPLY_OK_PREFIX  # noqa: E402

CHAT_ID = 77


def _make_cockpit(tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    # Deterministic: force the inert (staged) sender regardless of ambient env.
    cockpit._reply_sender = ReplySender(ReplySettings())
    return cockpit


def _stage(cockpit, monkeypatch, body, thread_id="thr_1", to="Dana @ Acme"):
    monkeypatch.setattr(cockpit.store, "run", stream_of(reply_block(body, thread_id, to)))
    msg = FakeMsg(CHAT_ID, text="triage my replies")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))
    return msg


def _press(cockpit, data):
    update, context, query = callback_update(CHAT_ID, data)
    asyncio.run(cockpit.on_callback(update, context))
    return query


def _outcomes(tmp_path):
    path = tmp_path / "example" / "outcomes.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _history(tmp_path):
    path = tmp_path / "example" / "history.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_stage_binds_token_to_content_hash(tmp_path, monkeypatch):
    cockpit = _make_cockpit(tmp_path)
    _stage(cockpit, monkeypatch, "Tuesday 2pm works — here's my link.", "thr_9")
    assert len(cockpit._pending_reply) == 1
    token = next(iter(cockpit._pending_reply))
    assert token == content_hash("Tuesday 2pm works — here's my link.", "thr_9")[:16]


def test_approve_stages_reply_and_logs_outcome(tmp_path, monkeypatch):
    cockpit = _make_cockpit(tmp_path)
    _stage(cockpit, monkeypatch, "Great — Tuesday works.", "thr_5")
    token = next(iter(cockpit._pending_reply))

    _press(cockpit, f"{_REPLY_OK_PREFIX}{token}")

    # Default path: staged for manual send (no transport), token popped (double-click safe).
    assert cockpit._pending_reply == {}
    outcomes = _outcomes(tmp_path)
    assert len(outcomes) == 1
    assert outcomes[0]["channel"] == "email"
    assert outcomes[0]["outcome"] == "reply"
    assert outcomes[0]["ref"] == "thr_5"
    assert outcomes[0]["meta"]["status"] == "staged"
    # Audit row too.
    hist = [h for h in _history(tmp_path) if h.get("skill") == "inbound-triage"]
    assert hist and hist[0]["event"] == "reply_staged"


def test_double_click_is_safe(tmp_path, monkeypatch):
    cockpit = _make_cockpit(tmp_path)
    _stage(cockpit, monkeypatch, "Booked.", "thr_2")
    token = next(iter(cockpit._pending_reply))
    _press(cockpit, f"{_REPLY_OK_PREFIX}{token}")
    q2 = _press(cockpit, f"{_REPLY_OK_PREFIX}{token}")  # second click finds nothing
    # Only one outcome logged despite two Approve presses.
    assert len(_outcomes(tmp_path)) == 1
    assert any("already handled" in r.lower() for r in q2.message.replies)


def test_cancel_sends_nothing_and_logs_nothing(tmp_path, monkeypatch):
    cockpit = _make_cockpit(tmp_path)
    _stage(cockpit, monkeypatch, "No thanks for now.", "thr_3")
    token = next(iter(cockpit._pending_reply))
    q = _press(cockpit, f"{_REPLY_NO_PREFIX}{token}")
    assert cockpit._pending_reply == {}
    assert _outcomes(tmp_path) == []
    assert any("cancelled" in r.lower() for r in q.message.replies)


def test_configured_transport_sends(tmp_path, monkeypatch):
    """When a transport IS wired, an approve actually sends and logs status=sent."""
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))

    sent: list[dict] = []

    async def fake_transport(url, *, headers, json, timeout):
        sent.append(json)
        return 200, {"ok": True}

    cockpit._reply_sender = ReplySender(
        ReplySettings(url="https://reply.example.com", secret="k", enabled=True),
        transport=fake_transport,
    )
    _stage(cockpit, monkeypatch, "On my way.", "thr_7")
    token = next(iter(cockpit._pending_reply))
    _press(cockpit, f"{_REPLY_OK_PREFIX}{token}")

    assert sent == [{"reply": "On my way.", "thread_id": "thr_7"}]
    outcomes = _outcomes(tmp_path)
    assert outcomes and outcomes[0]["meta"]["status"] == "sent"
