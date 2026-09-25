"""Tests for Cockpit gate copy, glyph cleanup, and dead-end elimination (R-13).

Pins:
1. Publish/schedule/reply previews drop ⚠️ and use title glyph ⏸ ("waiting on you").
2. Cancel messages contain no ❌ and read "Cancelled — nothing sent.".
3. /help lists all registered commands derived from bot.py registration (not hardcoded).
4. /help moves env vars to one calm line at the bottom.
5. Run-failed message contains "Say 'try again'" and no "server logs" or ⚠️.
6. Empty output diagnostic tells operator what was blocked without VPS dead-ends.
7. Voice-only mode still delivers the diagnostic as text (structured sentinel test).
8. Gate 1 push notification carries spend ceiling estimate.
9. Pack gate push asks server operator instead of providing VPS shell command.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    callback_update,
    make_cfg,
    publish_block,
    stream_of,
    text_update,
)
from telegram.ext import Application, CommandHandler  # noqa: E402

from agent.publish import content_hash  # noqa: E402
from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 91


def _make_cockpit(tmp_path):
    return botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))


def _get_registered_commands(tmp_path) -> list[str]:
    """Derive registered commands directly from Cockpit registration on a PTB Application."""
    app = Application.builder().token("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11").build()
    cockpit = _make_cockpit(tmp_path)
    cockpit.register(app)
    cmds = []
    for h in app.handlers.get(0, []):
        if isinstance(h, CommandHandler):
            cmds.extend(h.commands)
    return sorted(cmds)


def test_publish_preview_has_no_warning_glyph_and_uses_pause_glyph(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    post = "A thoughtful post without warning glyphs."
    monkeypatch.setattr(cockpit.store, "run", stream_of(publish_block(post, ())))
    msg = FakeMsg(CHAT_ID, text="draft post")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))

    assert len(msg.replies) >= 1
    preview = msg.replies[-1]
    assert "⚠️" not in preview
    assert "⏸ <b>Ready to publish to LinkedIn</b>" in preview
    assert "Review the <b>exact</b> text above." in preview


def test_schedule_preview_has_no_warning_glyph_and_uses_pause_glyph(monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta

    from fakes import FakePublisher

    cockpit = _make_cockpit(tmp_path)
    cockpit._publisher = FakePublisher()
    post = "A scheduled post."
    when = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = f"Drafted it.\n⟦GATE:publish⟧\n⟦POST⟧\n{post}\n⟦/POST⟧\n⟦SCHEDULE⟧{when}⟦/SCHEDULE⟧"
    monkeypatch.setattr(cockpit.store, "run", stream_of(block))
    msg = FakeMsg(CHAT_ID, text="draft scheduled post")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))

    assert len(msg.replies) >= 1
    preview = msg.replies[-1]
    assert "⚠️" not in preview
    assert "⏸ <b>Ready to schedule for LinkedIn</b>" in preview
    assert "Review the <b>exact</b> text above." in preview


def test_reply_preview_has_no_warning_glyph_and_uses_pause_glyph(monkeypatch, tmp_path):
    from fakes import reply_block

    cockpit = _make_cockpit(tmp_path)
    block = reply_block(
        body="Thank you for reaching out.",
        thread_id="t-1",
        to="founder@example.com",
    )
    monkeypatch.setattr(cockpit.store, "run", stream_of(block))
    msg = FakeMsg(CHAT_ID, text="draft reply")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))

    assert len(msg.replies) >= 1
    preview = msg.replies[-1]
    assert "⚠️" not in preview
    assert "⏸ <b>Ready to reply</b>" in preview
    assert "Review the <b>exact</b> text above." in preview


def test_cancel_messages_contain_no_cross_glyph(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    post = "Cancel me."
    monkeypatch.setattr(cockpit.store, "run", stream_of(publish_block(post, ())))
    msg = FakeMsg(CHAT_ID, text="draft post")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))

    token = content_hash(post, ())[:16]
    update, context, query = callback_update(CHAT_ID, f"pub:no:{token}")
    asyncio.run(cockpit.on_callback(update, context))

    assert any("❌" not in r and "Cancelled — nothing sent." in r for r in query.message.replies)


def test_help_lists_all_registered_commands_and_no_vps_env_vars(tmp_path):
    cockpit = _make_cockpit(tmp_path)
    msg = FakeMsg(CHAT_ID, text="/help")
    update, context = text_update(CHAT_ID, msg)

    asyncio.run(cockpit.cmd_help(update, context))

    assert len(msg.replies) == 1
    text = msg.replies[0]

    registered = _get_registered_commands(tmp_path)
    assert len(registered) >= 13

    for cmd in registered:
        assert f"/{cmd}" in text, f"Command /{cmd} missing from /help output"

    assert "HERMES_PUBLISH_ENABLED" not in text
    assert "HERMES_SCHEDULE_ENABLED" not in text
    assert (
        "Publishing and scheduling are switched off unless the server operator turns them on."
        in text
    )


def test_run_failed_message_tells_operator_what_to_say_next(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)

    async def _failing_stream(*a, **kw):
        raise RuntimeError("simulated pipeline crash")
        yield "never"

    monkeypatch.setattr(cockpit.store, "run", _failing_stream)
    msg = FakeMsg(CHAT_ID, text="crash please")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))

    assert len(msg.edits) >= 1
    err_text = msg.edits[-1]
    assert "⚠️" not in err_text
    assert "server logs" not in err_text.lower()
    assert "The run stopped before finishing. Nothing was sent." in err_text
    assert "Say 'try again', or 'show me the error' for the technical detail." in err_text


def test_empty_output_diagnostic_and_fallback(tmp_path):
    cockpit = _make_cockpit(tmp_path)
    # When denials ledger has no recent entry, fallback is returned
    diag = cockpit._delivery._empty_output_diagnostic(CHAT_ID)
    assert "⚠️" not in diag
    assert "server logs" not in diag.lower()
    assert "Say 'try again'" in diag


def test_voice_only_mode_delivers_empty_output_diagnostic_as_text(tmp_path):
    cockpit = _make_cockpit(tmp_path)
    msg = FakeMsg(CHAT_ID)

    # Empty raw text in voice-only mode should deliver the diagnostic text, NOT "🎙️"
    asyncio.run(cockpit._delivery._finalize(msg, header="", raw="", voice_only=True))
    assert len(msg.edits) >= 1
    assert "🎙️" not in msg.edits[-1]
    assert "Say 'try again'" in msg.edits[-1]

    # Normal non-empty text in voice-only mode SHOULD collapse to "🎙️"
    msg_normal = FakeMsg(CHAT_ID)
    asyncio.run(
        cockpit._delivery._finalize(
            msg_normal, header="", raw="Here is your report.", voice_only=True
        )
    )
    assert len(msg_normal.edits) >= 1
    assert msg_normal.edits[-1] == "🎙️"


def test_gate1_push_includes_spend_estimate(monkeypatch):
    from agent import gate_notify

    captured: dict = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, data=None, **kw):
            captured["data"] = data
            return _Resp()

    fake = types.ModuleType("httpx")
    fake.AsyncClient = _Client
    monkeypatch.setitem(sys.modules, "httpx", fake)
    monkeypatch.setattr("agent.profiles.load_gate1_chat_id", lambda *a, **kw: 12345, raising=False)

    cfg = types.SimpleNamespace(telegram_bot_token="tok", per_run_cap_usd=25.0)
    asyncio.run(gate_notify.push_gate1(cfg, Path("/nonexistent"), "acme", "r-100"))

    text = captured["data"]["text"]
    assert "Spend estimate: up to your per-run cap of $25.00" in text


def test_pack_gate_push_asks_operator_and_has_no_shell_command(monkeypatch):
    from agent import gate_notify

    captured: dict = {}

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, data=None, **kw):
            captured["data"] = data
            return _Resp()

    fake = types.ModuleType("httpx")
    fake.AsyncClient = _Client
    monkeypatch.setitem(sys.modules, "httpx", fake)
    monkeypatch.setattr("agent.profiles.load_gate1_chat_id", lambda *a, **kw: 12345, raising=False)

    cfg = types.SimpleNamespace(telegram_bot_token="tok", per_run_cap_usd=25.0)
    asyncio.run(
        gate_notify.push_pack_gate(
            cfg,
            Path("/nonexistent"),
            "acme",
            "r-100",
            ["node1"],
            pack="prospecting",
            variant="outreach",
        )
    )

    text = captured["data"]["text"]
    assert "Ask your server operator to approve this." in text
    assert "--gate-decision" not in text
    assert "python -m agent" not in text
