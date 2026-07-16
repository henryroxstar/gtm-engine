"""Characterization tests for the wizard's message interception.

While a chat is mid-wizard, inbound messages (typed or voice) are consumed as
wizard answers and NEVER reach the brain; ``/wizard_cancel`` frees the chat.
This ingress↔wizard hand-off is pinned before the split.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import FakeBot, FakeMsg, FakeVoice, make_cfg, recording_stream  # noqa: E402

from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 909


def _make_cockpit(tmp_path, **cfg_kw):
    cfg = make_cfg(tmp_path, chat_ids={CHAT_ID}, **cfg_kw)
    profile_dir = cfg.profiles_root / "example"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "PROFILE.md").write_text("# example\n")
    return botmod.Cockpit(cfg)


def _update(msg, args=None):
    from types import SimpleNamespace

    update = SimpleNamespace(effective_chat=SimpleNamespace(id=CHAT_ID), message=msg)
    context = SimpleNamespace(bot=FakeBot(), args=args or [])
    return update, context


def _start_wizard(cockpit):
    msg = FakeMsg(CHAT_ID)
    update, context = _update(msg)
    asyncio.run(cockpit.cmd_wizard(update, context))
    assert any("Profile wizard" in r for r in msg.replies)
    return msg


def test_wizard_consumes_next_text_message(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "never"))

    _start_wizard(cockpit)
    msg = FakeMsg(CHAT_ID, text="We sell rocket boosters to launch providers.")
    update, context = _update(msg)
    asyncio.run(cockpit.on_text(update, context))

    assert calls == []  # consumed by the wizard, never routed to the brain
    assert len(msg.replies) == 1  # the next wizard question


def test_wizard_cancel_frees_the_chat(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "Back to normal."))

    _start_wizard(cockpit)
    cancel_msg = FakeMsg(CHAT_ID, text="/wizard_cancel")
    update, context = _update(cancel_msg)
    asyncio.run(cockpit.cmd_wizard_cancel(update, context))
    assert any("Wizard cancelled" in r for r in cancel_msg.replies)
    assert cockpit._wizard_state == {}

    msg = FakeMsg(CHAT_ID, text="hello brain")
    update, context = _update(msg)
    asyncio.run(cockpit.on_text(update, context))
    assert calls == [(CHAT_ID, "hello brain")]  # ingress is back to the brain


def test_wizard_answer_via_voice_is_consumed(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path, api_key="xi-key")
    cockpit.store.set_reply_mode(CHAT_ID, "text")
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", recording_stream(calls, "never"))

    async def _fake_transcribe(cfg, data, **kw):
        return "Answer given by voice."

    monkeypatch.setattr(botmod.voice, "transcribe", _fake_transcribe)

    _start_wizard(cockpit)
    msg = FakeMsg(CHAT_ID)
    msg.voice = FakeVoice()
    update, context = _update(msg)
    asyncio.run(cockpit.on_voice(update, context))

    assert calls == []  # the voice answer fed the wizard, not the brain
    assert len(msg.replies) == 1  # the next wizard question
