"""Characterization tests for the whitelist boundary (``_is_allowed``).

The cockpit fails CLOSED: an empty whitelist rejects everything, a
non-whitelisted chat is silently ignored (never answered — the bot cannot be
enumerated), and a whitelisted chat is served. Pinned before the split because
every handler's first line depends on this guard.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import FakeMsg, make_cfg, recording_stream, text_update  # noqa: E402

from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 808


def _drive_start_and_text(cockpit, chat_id):
    msg_start = FakeMsg(chat_id)
    update, context = text_update(chat_id, msg_start)
    asyncio.run(cockpit.cmd_start(update, context))

    msg_text = FakeMsg(chat_id, text="hello brain")
    update, context = text_update(chat_id, msg_text)
    asyncio.run(cockpit.on_text(update, context))
    return msg_start, msg_text


def test_empty_whitelist_fails_closed(monkeypatch, tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids=set()))
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "never"))

    msg_start, msg_text = _drive_start_and_text(cockpit, CHAT_ID)

    assert calls == []  # the brain is never invoked
    assert msg_start.replies == []  # and the stranger is never answered
    assert msg_text.replies == []


def test_unlisted_chat_is_ignored(monkeypatch, tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "never"))

    msg_start, msg_text = _drive_start_and_text(cockpit, 999)

    assert calls == []
    assert msg_start.replies == []
    assert msg_text.replies == []


def test_listed_chat_is_served(monkeypatch, tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Here you go."))

    msg_start, msg_text = _drive_start_and_text(cockpit, CHAT_ID)

    assert any("GTM Engine online" in r for r in msg_start.replies)
    assert calls == [(CHAT_ID, "hello brain")]
