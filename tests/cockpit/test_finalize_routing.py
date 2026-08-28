"""Characterization tests for gate-aware finalization (the ``_finalize`` funnel).

Driven entirely through ``on_text`` (never calling ``_finalize`` directly) so
the routing contract is pinned at the instance surface: plan-gate keyboard
attach + run_id capture, publish precedence over plan, ⟦FILE:…⟧ sentinel
validation/delivery, the empty-output diagnostic, voice-only collapse rules,
and sanitized stream errors.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    FakePublisher,
    make_cfg,
    publish_block,
    raising_stream,
    stream_of,
    text_update,
)

from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 314


def _run_text(cockpit, monkeypatch, *chunks):
    monkeypatch.setattr(cockpit.store, "run", stream_of(*chunks))
    msg = FakeMsg(CHAT_ID, text="go")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))
    return msg


def _make_cockpit(tmp_path, **cfg_kw):
    return botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}, **cfg_kw))


def test_plan_sentinel_attaches_keyboard_and_strips_sentinel(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    msg = _run_text(cockpit, monkeypatch, "Here is the plan.\n⟦GATE:plan⟧")

    final, kwargs = msg.edits[-1], msg.edit_kwargs[-1]
    assert "Here is the plan." in final
    assert "⟦GATE:plan⟧" not in final
    kb = kwargs["reply_markup"]
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        "gate:plan:approve",
        "gate:plan:edit",
        "gate:plan:reject",
    ]


def test_plan_gate_captures_run_id(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    _run_text(cockpit, monkeypatch, "Plan for run r-20260715-1200 is ready.\n⟦GATE:plan⟧")

    assert cockpit._pending_gate_run_id[CHAT_ID] == "r-20260715-1200"


def test_publish_gate_takes_precedence_over_plan_gate(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    cockpit._publisher = FakePublisher()
    raw = publish_block("Both gates present.") + "\n⟦GATE:plan⟧"
    msg = _run_text(cockpit, monkeypatch, raw)

    # Publish staging happened…
    assert len(cockpit._pending_publish) == 1
    # …and the plan gate never fired: no run-id capture, no plan keyboard.
    assert cockpit._pending_gate_run_id == {}
    pub_markups = [kw.get("reply_markup") for kw in msg.reply_kwargs if kw.get("reply_markup")]
    assert len(pub_markups) == 1
    assert pub_markups[0].inline_keyboard[0][0].callback_data.startswith("pub:ok:")
    assert all(kw.get("reply_markup") is None for kw in msg.edit_kwargs)


def test_file_sentinel_sends_document_and_strips_marker(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    deliverable = tmp_path / "example" / "deliver.docx"
    deliverable.parent.mkdir(parents=True, exist_ok=True)
    deliverable.write_bytes(b"docx-bytes")

    msg = _run_text(cockpit, monkeypatch, f"Done — wrote the doc.\n⟦FILE:{deliverable}⟧")

    assert msg.documents == [("deliver.docx", b"docx-bytes")]
    assert "⟦FILE:" not in msg.edits[-1]
    assert "Done — wrote the doc." in msg.edits[-1]


def test_file_sentinel_outside_content_root_is_blocked(monkeypatch, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"secret")
    cockpit = _make_cockpit(tmp_path, content_root=root)

    msg = _run_text(cockpit, monkeypatch, f"Take this.\n⟦FILE:{outside}⟧")

    assert msg.documents == []  # never sent — path escapes the tenant boundary
    assert "⟦FILE:" not in msg.edits[-1]


def test_file_sentinel_missing_file_is_skipped(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    ghost = tmp_path / "example" / "ghost.pdf"  # inside root, does not exist

    msg = _run_text(cockpit, monkeypatch, f"Take this.\n⟦FILE:{ghost}⟧")

    assert msg.documents == []
    assert "⟦FILE:" not in msg.edits[-1]


def test_empty_brain_output_renders_diagnostic(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    msg = _run_text(cockpit, monkeypatch)  # zero chunks

    assert any("completed without producing text" in e for e in msg.edits)


def test_empty_brain_output_names_the_recent_denial(monkeypatch, tmp_path):
    # When a tool-denial loop is the likely cause (the documented failure mode),
    # naming the blocked tool inline beats sending the operator to "the server
    # logs" — which aren't reachable from Telegram anyway.
    from gtm_core.ledgers import Ledgers

    cockpit = _make_cockpit(tmp_path)
    Ledgers(cockpit.cfg, "example").append_denial(
        {
            "tool": "mcp__higgsfield_video__generate_video",
            "decision": "deny",
            "outcome": "denied",
            "detail": "",
        }
    )
    msg = _run_text(cockpit, monkeypatch)  # zero chunks

    final = msg.edits[-1]
    assert "mcp__higgsfield_video__generate_video" in final
    assert "denials.jsonl" in final


def test_empty_brain_output_ignores_a_stale_denial(monkeypatch, tmp_path):
    # A denial from an earlier, unrelated turn must not be misattributed to this one.
    from gtm_core.ledgers import Ledgers

    cockpit = _make_cockpit(tmp_path)
    Ledgers(cockpit.cfg, "example").append_denial(
        {
            "tool": "Bash",
            "decision": "deny",
            "outcome": "denied",
            "detail": "",
            "ts": "2020-01-01T00:00:00Z",
        }
    )
    msg = _run_text(cockpit, monkeypatch)  # zero chunks

    final = msg.edits[-1]
    assert "completed without producing text" in final
    assert "Bash" not in final


def test_voice_only_mode_collapses_reply_to_marker(monkeypatch, tmp_path):
    # api_key=None keeps _maybe_voice_reply inert (no TTS task in the test loop).
    cockpit = _make_cockpit(tmp_path, api_key=None)
    cockpit.store.set_reply_mode(CHAT_ID, "voice")
    msg = _run_text(cockpit, monkeypatch, "A normal spoken reply.")

    assert msg.edits[-1] == "[example] 🎙️"


def test_voice_only_gate_still_renders_full_text(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path, api_key=None)
    cockpit.store.set_reply_mode(CHAT_ID, "voice")
    msg = _run_text(cockpit, monkeypatch, "Plan body to read.\n⟦GATE:plan⟧")

    final = msg.edits[-1]
    assert "Plan body to read." in final  # gates override voice-only collapse
    assert msg.edit_kwargs[-1]["reply_markup"] is not None


def test_stream_error_renders_sanitized_error(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path)
    monkeypatch.setattr(
        cockpit.store, "run", raising_stream(RuntimeError("token=SECRET do not leak"))
    )
    msg = FakeMsg(CHAT_ID, text="go")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))

    final = msg.edits[-1]
    assert "The run failed: RuntimeError" in final
    assert "SECRET" not in final  # only the exception TYPE is surfaced
    assert msg.edit_kwargs[-1].get("reply_markup") is None


def test_long_reply_splits_into_multiple_messages_instead_of_truncating(monkeypatch, tmp_path):
    # A reply past Telegram's 4096-char single-message cap used to be cut off with
    # "…(truncated)" and dropped whatever text came after the cutoff. It must now
    # arrive in full, spread across the placeholder edit + follow-up replies.
    long_body = "\n".join(f"line {i} of the report" for i in range(400))
    assert len(long_body) > 4096
    msg = _run_text(_make_cockpit(tmp_path), monkeypatch, long_body)

    delivered = "".join(msg.edits[-1:] + msg.replies)
    assert "…(truncated)" not in delivered
    assert "line 0 of the report" in msg.edits[-1]
    assert "line 399 of the report" in delivered
    # No single Telegram call exceeds the hard cap.
    assert all(len(e) <= 4096 for e in msg.edits)
    assert all(len(r) <= 4096 for r in msg.replies)


def test_gate_sentinel_past_4096_chars_is_not_dropped(monkeypatch, tmp_path):
    # Regression: the old soft-limit `break` abandoned the SDK stream once
    # accumulated text crossed ~3800 chars — if the ⟦GATE:plan⟧ sentinel only
    # arrived after that point, it was never seen and the plan gate silently
    # never fired. The full stream must always be drained.
    padding = "x" * 4500
    cockpit = _make_cockpit(tmp_path)
    msg = _run_text(cockpit, monkeypatch, f"{padding}\n⟦GATE:plan⟧")

    kb = msg.edit_kwargs[-1].get("reply_markup") or msg.reply_kwargs[-1].get("reply_markup")
    assert kb is not None
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        "gate:plan:approve",
        "gate:plan:edit",
        "gate:plan:reject",
    ]
