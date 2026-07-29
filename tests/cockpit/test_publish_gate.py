"""Characterization tests for Gate 2 — the publish gate (stage → approve/cancel).

Pins the security-critical publish flow before the handler-module split:
staging binds the Approve button to ``content_hash(post, media)[:16]``, approval
re-verifies the hash and pops the token (double-click safe), the kill switch and
the durable ledger idempotency hold end-to-end, and every outcome is audited to
``history.jsonl`` by the cockpit (not the brain). All drives go through the
Cockpit instance surface (``on_text`` stages, ``on_callback`` approves) so the
tests survive the refactor unchanged.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    FakePublisher,
    callback_update,
    make_cfg,
    publish_block,
    stream_of,
    text_update,
)

from agent.publish import PublishResult, content_hash  # noqa: E402
from cockpit import bot as botmod  # noqa: E402

CHAT_ID = 91


def _make_cockpit(tmp_path, *, publisher=None):
    cockpit = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT_ID}))
    if publisher is not None:
        cockpit._publisher = publisher
    return cockpit


def _stage(cockpit, monkeypatch, post, media=()):
    """Drive on_text with a fake brain turn that ends in a publish gate."""
    monkeypatch.setattr(cockpit.store, "run", stream_of(publish_block(post, media)))
    msg = FakeMsg(CHAT_ID, text="draft the post")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))
    return msg


def _press(cockpit, data):
    update, context, query = callback_update(CHAT_ID, data)
    asyncio.run(cockpit.on_callback(update, context))
    return query


def _history_records(tmp_path):
    path = tmp_path / "example" / "history.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── staging ──────────────────────────────────────────────────────────────────


def test_publish_sentinel_stages_exact_post_with_keyboard(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path, publisher=FakePublisher())
    post = "Hello LinkedIn, this is the exact post."
    msg = _stage(cockpit, monkeypatch, post)

    token = content_hash(post, ())[:16]
    assert cockpit._pending_publish[token]["post"] == post
    assert cockpit._pending_publish[token]["hash"] == content_hash(post, ())
    # The exact bytes are re-displayed in the preview reply…
    assert any(post in r for r in msg.replies)
    # …and the Approve/Cancel keyboard is bound to the content-hash token.
    kb = msg.reply_kwargs[-1]["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == f"pub:ok:{token}"
    assert kb.inline_keyboard[0][1].callback_data == f"pub:no:{token}"


def test_publish_sentinel_without_post_block_stages_nothing(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path, publisher=FakePublisher())
    monkeypatch.setattr(
        cockpit.store, "run", stream_of("Prose only.\n⟦GATE:publish⟧\n(no post block)")
    )
    msg = FakeMsg(CHAT_ID, text="draft the post")
    update, context = text_update(CHAT_ID, msg)
    asyncio.run(cockpit.on_text(update, context))

    assert cockpit._pending_publish == {}
    assert any("no valid post block" in r for r in msg.replies)


def test_invalid_post_is_not_staged(monkeypatch, tmp_path):
    cockpit = _make_cockpit(tmp_path, publisher=FakePublisher())
    msg = _stage(cockpit, monkeypatch, "x" * 3001)  # over max_chars=3000

    assert cockpit._pending_publish == {}
    assert any("Not staged for publish" in r for r in msg.replies)


def test_oversize_preview_refuses_to_stage(monkeypatch, tmp_path):
    # The preview must show the FULL exact content; if it can't fit one Telegram
    # message, staging is refused. The streamed turn stays under the 3800-char
    # stream soft-limit, but html-escaping the &-laden media urls inflates the
    # preview past 4096 — exactly the backstop this branch exists for.
    cockpit = _make_cockpit(tmp_path, publisher=FakePublisher())
    post = "p" * 1500
    media = [
        f"https://m.example.com/{i:02d}?" + "&".join(f"k{j}=v" for j in range(20))
        for i in range(15)
    ]
    msg = _stage(cockpit, monkeypatch, post, media)

    assert cockpit._pending_publish == {}
    assert any("too long to show in full" in r for r in msg.replies)


# ── approve / cancel ─────────────────────────────────────────────────────────


def test_approve_pops_token_so_double_click_is_noop(monkeypatch, tmp_path):
    publisher = FakePublisher()
    cockpit = _make_cockpit(tmp_path, publisher=publisher)
    post = "Ship it once."
    _stage(cockpit, monkeypatch, post)
    token = content_hash(post, ())[:16]

    first = _press(cockpit, f"pub:ok:{token}")
    assert len(publisher.calls) == 1
    assert publisher.calls[0][0] == post
    assert any("Published to LinkedIn" in r for r in first.message.replies)

    second = _press(cockpit, f"pub:ok:{token}")
    assert len(publisher.calls) == 1  # second click found nothing to publish
    assert any("already handled or expired" in r for r in second.message.replies)


def test_approve_verifies_content_hash_before_publishing(monkeypatch, tmp_path):
    publisher = FakePublisher()
    cockpit = _make_cockpit(tmp_path, publisher=publisher)
    post = "Approved bytes only."
    _stage(cockpit, monkeypatch, post)
    token = content_hash(post, ())[:16]

    # Tamper with the staged draft after approval was offered: the recomputed
    # hash no longer matches, so the approval must not publish ANYTHING.
    cockpit._pending_publish[token]["post"] = "tampered bytes"
    query = _press(cockpit, f"pub:ok:{token}")

    assert publisher.calls == []
    assert any("integrity check failed" in r for r in query.message.replies)


def test_approve_success_appends_published_history(monkeypatch, tmp_path):
    publisher = FakePublisher(result=PublishResult(ok=True, status="published", post_id="p1"))
    cockpit = _make_cockpit(tmp_path, publisher=publisher)
    post = "Audited post."
    _stage(cockpit, monkeypatch, post)
    _press(cockpit, f"pub:ok:{content_hash(post, ())[:16]}")

    records = _history_records(tmp_path)
    assert len(records) == 1
    rec = records[0]
    assert rec["event"] == "published"
    assert rec["platform"] == "linkedin"
    assert rec["content_sha256"] == content_hash(post, ())
    assert rec["chars"] == len(post)
    assert rec["media_count"] == 0


def test_publish_failure_appends_publish_failed_history(monkeypatch, tmp_path):
    publisher = FakePublisher(result=PublishResult(ok=False, status="error", error="HTTP 500"))
    cockpit = _make_cockpit(tmp_path, publisher=publisher)
    post = "Doomed post."
    _stage(cockpit, monkeypatch, post)
    query = _press(cockpit, f"pub:ok:{content_hash(post, ())[:16]}")

    records = _history_records(tmp_path)
    assert [r["event"] for r in records] == ["publish_failed"]
    assert records[0]["status"] == "error"
    assert any("Publish failed" in r for r in query.message.replies)


def test_cancel_publish_drops_draft(monkeypatch, tmp_path):
    publisher = FakePublisher()
    cockpit = _make_cockpit(tmp_path, publisher=publisher)
    post = "Never mind."
    _stage(cockpit, monkeypatch, post)
    token = content_hash(post, ())[:16]

    query = _press(cockpit, f"pub:no:{token}")

    assert cockpit._pending_publish == {}
    assert publisher.calls == []
    assert any("Publish cancelled — nothing sent." in r for r in query.message.replies)


# ── end-to-end guards with the REAL publisher ────────────────────────────────


def test_kill_switch_disabled_end_to_end(monkeypatch, tmp_path):
    # Real LinkedInPublisher, env cleared by conftest → kill switch closed.
    # The approval flow runs to completion but nothing leaves the process.
    cockpit = _make_cockpit(tmp_path)
    post = "Kill-switch check."
    _stage(cockpit, monkeypatch, post)
    query = _press(cockpit, f"pub:ok:{content_hash(post, ())[:16]}")

    assert any("Publishing is disabled" in r for r in query.message.replies)
    records = _history_records(tmp_path)
    assert [r["event"] for r in records] == ["publish_failed"]
    assert records[0]["status"] == "disabled"


def test_ledger_idempotency_blocks_duplicate(monkeypatch, tmp_path):
    # Arm the real publisher (env BEFORE Cockpit construction), pre-seed the
    # durable history ledger with this content hash, and prove the approval is
    # blocked as a duplicate WITHOUT any HTTP transport call.
    monkeypatch.setenv("HERMES_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_PUBLISH_URL", "https://hooks.example.com/publish")
    monkeypatch.setenv("HERMES_PUBLISH_SECRET", "s3cret")
    cockpit = _make_cockpit(tmp_path)

    post = "Already shipped."
    hist_dir = tmp_path / "example"
    hist_dir.mkdir(parents=True, exist_ok=True)
    (hist_dir / "history.jsonl").write_text(
        json.dumps({"event": "published", "content_sha256": content_hash(post, ())}) + "\n"
    )

    async def _no_http(*_a, **_kw):
        raise AssertionError("HTTP transport must not be called for a duplicate")

    cockpit._publisher.transport = _no_http

    _stage(cockpit, monkeypatch, post)
    query = _press(cockpit, f"pub:ok:{content_hash(post, ())[:16]}")

    assert any("Already published" in r for r in query.message.replies)
    records = _history_records(tmp_path)
    assert records[-1]["event"] == "publish_failed"
    assert records[-1]["status"] == "duplicate"
