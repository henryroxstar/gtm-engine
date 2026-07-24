"""Characterization tests for ``Cockpit.on_callback`` prefix dispatch.

Pins the callback wire protocol before the handler-module split: every
``callback_data`` prefix routes to the right action, unknown data is answered
but ignored, and the whitelist fails closed *before* the callback is answered.
Sentinel/directive strings are asserted as literals — they are part of the wire
contract, not implementation details.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakePublisher,
    callback_update,
    make_cfg,
    recording_stream,
)

from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 555


def _make_cockpit(tmp_path):
    return botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))


def test_callback_unknown_data_is_answered_and_ignored(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "never"))

    update, context, query = callback_update(CHAT_ID, "bogus:whatever")
    asyncio.run(cockpit.on_callback(update, context))

    # The spinner is always cleared, but nothing else happens.
    assert query.answered
    assert calls == []
    assert query.message.replies == []
    assert cockpit._awaiting_edit == {}


def test_callback_requires_whitelist(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)  # whitelist = {CHAT_ID}
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "never"))

    update, context, query = callback_update(999, "gate:plan:approve")
    asyncio.run(cockpit.on_callback(update, context))

    # Fail closed BEFORE answering: a stranger's press is never acknowledged.
    assert not query.answered
    assert calls == []
    assert query.message.replies == []


def test_voice_mode_callback_sets_mode(tmp_path):
    cockpit = _make_cockpit(tmp_path)

    update, context, query = callback_update(CHAT_ID, "voice:mode:text")
    asyncio.run(cockpit.on_callback(update, context))

    assert cockpit.store.reply_mode(CHAT_ID) == "text"
    assert any("Reply mode" in t for t in query.text_edits)


def test_plan_approve_streams_approve_directive(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Plan promoted."))

    update, context, query = callback_update(CHAT_ID, "gate:plan:approve")
    asyncio.run(cockpit.on_callback(update, context))

    assert query.answered
    # The gate keyboard is dropped before streaming the directive.
    assert query.markup_edits == [None]
    assert len(calls) == 1
    chat_id, prompt = calls[0]
    assert chat_id == CHAT_ID
    assert prompt.startswith("Approved. Promote the pending content plan draft")
    # The directive's streamed result lands in the fresh placeholder reply.
    assert any("Plan promoted." in e for e in query.message.edits)


def test_plan_reject_clears_pending_edit_and_streams_reject_directive(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Draft discarded."))
    cockpit._awaiting_edit[CHAT_ID] = True  # a pending Edit is cancelled by Reject

    update, context, query = callback_update(CHAT_ID, "gate:plan:reject")
    asyncio.run(cockpit.on_callback(update, context))

    assert CHAT_ID not in cockpit._awaiting_edit
    assert len(calls) == 1
    assert calls[0][1].startswith("Rejected. Discard the pending content plan draft")


def test_plan_edit_parks_chat_for_edit_notes(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "never"))

    update, context, query = callback_update(CHAT_ID, "gate:plan:edit")
    asyncio.run(cockpit.on_callback(update, context))

    # Edit does NOT run the brain — it parks the chat for the next message.
    assert calls == []
    assert cockpit._awaiting_edit[CHAT_ID] is True
    assert query.markup_edits == [None]
    assert any("Send your edit notes" in r for r in query.message.replies)


def test_publish_unknown_token_reports_already_handled(tmp_path):
    cockpit = _make_cockpit(tmp_path)
    cockpit._publisher = FakePublisher()

    update, context, query = callback_update(CHAT_ID, "pub:ok:0123456789abcdef")
    asyncio.run(cockpit.on_callback(update, context))

    assert cockpit._publisher.calls == []
    assert any("already handled or expired" in r for r in query.message.replies)
