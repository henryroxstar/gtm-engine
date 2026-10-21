"""Tests for the ``/voice`` reply-mode toggle and its disk persistence.

Bare ``/voice`` is a binary toggle (voice-only ⇄ text-only); the choice is written
to ``<content_root>/_system/cockpit-prefs.json`` so a cockpit redeploy doesn't
silently reset everyone back to the ``both`` default. Network-free — no STT/TTS is
exercised, only the handler's mode bookkeeping and the SessionStore persistence.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from agent.session import SessionStore  # noqa: E402
from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 4242


class _FakeMsg:
    async def reply_text(self, text, **_kw):
        return self


def _make_cockpit(monkeypatch, content_root):
    cfg = SimpleNamespace(
        telegram_allowed_chat_ids={CHAT_ID},
        elevenlabs_api_key="xi-key",
        elevenlabs_voice_id="DefaultVoiceId0000000",
        default_profile="example",
        content_root=content_root,
    )
    for var in ("HERMES_PUBLISH_ENABLED", "HERMES_PUBLISH_URL", "HERMES_PUBLISH_SECRET"):
        monkeypatch.delenv(var, raising=False)
    return botmod.Cockpit(cfg)


def _voice_update(args):
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=CHAT_ID),
        message=_FakeMsg(),
    )
    context = SimpleNamespace(args=args)
    return update, context


def test_bare_voice_toggles_voice_then_text(monkeypatch, tmp_path):
    cockpit = _make_cockpit(monkeypatch, tmp_path)
    # Default is "both"; the first bare /voice flips to voice-only (the spoken
    # reply the operator expects on the first tap of a fresh chat).
    asyncio.run(cockpit.cmd_voice(*_voice_update([])))
    assert cockpit.store.reply_mode(CHAT_ID) == "voice"
    # A second bare /voice flips voice-only → text-only.
    asyncio.run(cockpit.cmd_voice(*_voice_update([])))
    assert cockpit.store.reply_mode(CHAT_ID) == "text"
    # And back to voice-only — it is a binary toggle, never landing on "both".
    asyncio.run(cockpit.cmd_voice(*_voice_update([])))
    assert cockpit.store.reply_mode(CHAT_ID) == "voice"


def test_explicit_arg_sets_exact_mode(monkeypatch, tmp_path):
    cockpit = _make_cockpit(monkeypatch, tmp_path)
    asyncio.run(cockpit.cmd_voice(*_voice_update(["both"])))
    assert cockpit.store.reply_mode(CHAT_ID) == "both"


def test_reply_mode_persists_across_store_instances(tmp_path):
    cfg = SimpleNamespace(content_root=tmp_path)
    store = SessionStore(cfg)
    store.set_reply_mode(CHAT_ID, "voice")

    prefs = tmp_path / "_system" / "cockpit-prefs.json"
    assert json.loads(prefs.read_text())["reply_modes"] == {str(CHAT_ID): "voice"}

    # A fresh store (simulating a cockpit redeploy) reloads the persisted mode
    # instead of falling back to the "both" default.
    reborn = SessionStore(cfg)
    assert reborn.reply_mode(CHAT_ID) == "voice"


def test_corrupt_prefs_file_degrades_to_default(tmp_path):
    sysdir = tmp_path / "_system"
    sysdir.mkdir()
    (sysdir / "cockpit-prefs.json").write_text("{ not valid json")

    cfg = SimpleNamespace(content_root=tmp_path)
    store = SessionStore(cfg)  # must not raise on a corrupt file
    assert store.reply_mode(CHAT_ID) == "both"


def test_unknown_persisted_mode_is_skipped(tmp_path):
    sysdir = tmp_path / "_system"
    sysdir.mkdir()
    (sysdir / "cockpit-prefs.json").write_text(
        json.dumps({"reply_modes": {str(CHAT_ID): "screaming", "7": "voice"}})
    )

    cfg = SimpleNamespace(content_root=tmp_path)
    store = SessionStore(cfg)
    # The bogus mode is dropped (falls back to default); the valid one loads.
    assert store.reply_mode(CHAT_ID) == "both"
    assert store.reply_mode(7) == "voice"
