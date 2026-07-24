"""Characterization tests for the plan-gate Edit bridge (``_awaiting_edit``).

The Edit press parks the chat; the NEXT inbound message — typed or voice —
becomes the edit notes, wrapped in the revise-and-re-present directive. This is
the one piece of state shared between the callback handler and message ingress,
so it gets pinned before the split.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    FakeVoice,
    callback_update,
    make_cfg,
    recording_stream,
    text_update,
)

from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 606


def _park_for_edit(cockpit):
    update, context, query = callback_update(CHAT_ID, "gate:plan:edit")
    asyncio.run(cockpit.on_callback(update, context))
    assert cockpit._awaiting_edit[CHAT_ID] is True
    return query


def _send_text(cockpit, text):
    msg = FakeMsg(CHAT_ID, text=text)
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))
    return msg


def test_edit_press_then_text_wraps_edit_directive(monkeypatch, tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Revised."))

    _park_for_edit(cockpit)
    _send_text(cockpit, "make it punchier")

    assert len(calls) == 1
    prompt = calls[0][1]
    assert prompt.startswith("Revise the pending content plan draft")
    assert prompt.endswith("Edit notes: make it punchier")


def test_edit_flag_is_consumed_once(monkeypatch, tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "ok"))

    _park_for_edit(cockpit)
    _send_text(cockpit, "tighten the hook")
    _send_text(cockpit, "hello again")

    assert CHAT_ID not in cockpit._awaiting_edit
    assert len(calls) == 2
    assert calls[0][1].startswith("Revise the pending content plan draft")
    assert calls[1][1] == "hello again"  # second message is NOT edit notes


def test_edit_notes_via_voice_note_also_wrap_directive(monkeypatch, tmp_path):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}, api_key="xi-key"))
    cockpit.store.set_reply_mode(CHAT_ID, "text")  # keep the reply path off TTS
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Revised."))

    async def _fake_transcribe(cfg, data, **kw):
        return "trim slide two"

    monkeypatch.setattr(botmod.voice, "transcribe", _fake_transcribe)

    _park_for_edit(cockpit)
    msg = FakeMsg(CHAT_ID)
    msg.voice = FakeVoice()
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_voice(update, context))

    assert len(calls) == 1
    prompt = calls[0][1]
    assert prompt.startswith("Revise the pending content plan draft")
    assert prompt.endswith("Edit notes: trim slide two")
