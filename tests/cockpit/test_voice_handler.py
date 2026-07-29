"""Handler tests for the Telegram voice-note pipeline (cockpit.bot.on_voice).

Network-free: ElevenLabs STT and the brain session are both faked. Asserts that a
voice note is transcribed and routed through the SAME path as typed text (the
transcript reaches ``store.run`` as the prompt), and that the no-key and
empty-transcript branches reject cleanly without ever calling the brain.

Reply mode is forced to ``text`` so the success path never reaches TTS (which
would need the network) — input/output modes are independent by design.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 123


def _make_cockpit(monkeypatch, *, api_key="xi-key"):
    cfg = SimpleNamespace(
        telegram_allowed_chat_ids={CHAT_ID},
        elevenlabs_api_key=api_key,
        elevenlabs_voice_id="DefaultVoiceId0000000",
        default_profile="example",
        content_root=Path("/tmp"),
    )
    # PublishSettings.from_env reads only HERMES_PUBLISH_* (all optional) — keep env clean.
    for var in ("HERMES_PUBLISH_ENABLED", "HERMES_PUBLISH_URL", "HERMES_PUBLISH_SECRET"):
        monkeypatch.delenv(var, raising=False)
    return botmod.Cockpit(cfg)


class _FakeMsg:
    """Stands in for both the inbound message and the streamed placeholder."""

    def __init__(self, voice=None):
        self.voice = voice
        self.caption = None
        self.chat_id = CHAT_ID
        self.replies: list[str] = []
        self.edits: list[str] = []

    async def reply_text(self, text, **_kw):
        self.replies.append(text)
        return self  # acts as the placeholder for _run_and_deliver

    async def edit_text(self, text, **_kw):
        self.edits.append(text)


class _FakeFile:
    async def download_as_bytearray(self):
        return bytearray(b"ogg-bytes")


class _FakeVoice:
    def __init__(self):
        self.file_size = 1024
        self.mime_type = "audio/ogg"

    async def get_file(self):
        return _FakeFile()


class _FakeBot:
    async def send_chat_action(self, **_kw):
        return None

    async def send_voice(self, **_kw):  # only reachable if mode != text (not in these tests)
        return None


def _update_and_context(msg):
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=CHAT_ID),
        message=msg,
    )
    context = SimpleNamespace(bot=_FakeBot())
    return update, context


def _fake_run_recorder(calls):
    async def _run(chat_id, prompt):
        calls.append((chat_id, prompt))
        yield "Here is the reply."

    return _run


# ── tests ─────────────────────────────────────────────────────────────────────


def test_voice_note_routes_transcript_to_brain(monkeypatch):
    cockpit = _make_cockpit(monkeypatch)
    cockpit.store.set_reply_mode(CHAT_ID, "text")  # keep success path off the network

    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", _fake_run_recorder(calls))

    async def _fake_transcribe(cfg, data, **kw):
        assert data == b"ogg-bytes"
        return "what is our pipeline status"

    monkeypatch.setattr(botmod.voice, "transcribe", _fake_transcribe)

    msg = _FakeMsg(voice=_FakeVoice())
    update, context = _update_and_context(msg)
    asyncio.run(cockpit.on_voice(update, context))

    # The transcript reached the brain as the prompt — same path as typed text.
    assert calls == [(CHAT_ID, "what is our pipeline status")]


def test_voice_note_without_api_key_is_rejected(monkeypatch):
    cockpit = _make_cockpit(monkeypatch, api_key=None)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", _fake_run_recorder(calls))

    msg = _FakeMsg(voice=_FakeVoice())
    update, context = _update_and_context(msg)
    asyncio.run(cockpit.on_voice(update, context))

    assert calls == []  # brain never invoked
    assert any("ELEVENLABS_API_KEY" in r for r in msg.replies)


def test_empty_transcript_does_not_invoke_brain(monkeypatch):
    cockpit = _make_cockpit(monkeypatch)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", _fake_run_recorder(calls))

    async def _blank(cfg, data, **kw):
        return ""

    monkeypatch.setattr(botmod.voice, "transcribe", _blank)

    msg = _FakeMsg(voice=_FakeVoice())
    update, context = _update_and_context(msg)
    asyncio.run(cockpit.on_voice(update, context))

    assert calls == []
    assert any("make out" in r for r in msg.replies)


def test_transcribe_failure_surfaces_error(monkeypatch):
    cockpit = _make_cockpit(monkeypatch)
    calls: list = []
    monkeypatch.setattr(cockpit.store, "run", _fake_run_recorder(calls))

    async def _boom(cfg, data, **kw):
        raise botmod.voice.VoiceError("ElevenLabs STT returned 502")

    monkeypatch.setattr(botmod.voice, "transcribe", _boom)

    msg = _FakeMsg(voice=_FakeVoice())
    update, context = _update_and_context(msg)
    asyncio.run(cockpit.on_voice(update, context))

    assert calls == []
    assert any("Could not transcribe" in r for r in msg.replies)
