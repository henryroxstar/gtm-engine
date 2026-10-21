"""F4 — the cockpit's "outcome unknown" message and its two buttons (always-on hardening PRD).

A publish whose request did not finish is UNKNOWN: the cockpit says so in plain words and offers
[It went out] / [It didn't], which settle the intent through the same resolve path the CLI uses. No keys,
no hashes in the operator's message.
"""

from __future__ import annotations

import asyncio

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
from cockpit import copy, epoch  # noqa: E402

CHAT = 91
POST = "A fictional post that may or may not have gone out."


def _cockpit(tmp_path, publisher):
    c = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT}))
    c._publisher = publisher
    return c


def _stage(cockpit, monkeypatch):
    monkeypatch.setattr(cockpit.store, "run", stream_of(publish_block(POST)))
    msg = FakeMsg(CHAT, text="draft")
    update, context = text_update(CHAT, msg)
    asyncio.run(cockpit.on_text(update, context))
    return content_hash(POST, ())[:16]


def _press(cockpit, data):
    update, context, query = callback_update(CHAT, data)
    asyncio.run(cockpit.on_callback(update, context))
    return query


def _timeout():
    return PublishResult(ok=False, status="error", error="TimeoutError", sent_state="unknown")


def _last(query) -> str:
    """The cockpit's latest reply with its "[profile] " header removed."""
    return query.message.replies[-1].split("] ", 1)[1]


def _keyboard_labels(query):
    kb = query.message.reply_kwargs[-1].get("reply_markup")
    return [b.text for row in kb.inline_keyboard for b in row] if kb else []


def test_a_timed_out_publish_says_it_cannot_tell_and_offers_both_buttons(monkeypatch, tmp_path):
    pub = FakePublisher(_timeout())
    c = _cockpit(tmp_path, pub)
    token = _stage(c, monkeypatch)
    q = _press(c, f"pub:ok:{token}")
    text = _last(q)
    assert "can't tell whether this post went out" in text
    assert _keyboard_labels(q) == [copy.INTENT_SENT_LABEL, copy.INTENT_NOT_LABEL]
    assert copy.INTENT_SENT_LABEL == "It went out" and copy.INTENT_NOT_LABEL == "It didn't"
    for leaked in ("b-", "sha", content_hash(POST, ())[:8]):
        assert leaked not in text  # plain words: no batch keys, no hashes


def test_a_repeat_of_an_unknown_publish_is_refused_in_plain_words_with_the_buttons(
    monkeypatch, tmp_path
):
    pub = FakePublisher(_timeout())
    c = _cockpit(tmp_path, pub)
    token = _stage(c, monkeypatch)
    _press(c, f"pub:ok:{token}")
    token = _stage(c, monkeypatch)  # the operator drafts and approves the same post again
    q = _press(c, f"pub:ok:{token}")
    text = _last(q)
    assert text.startswith("Not sent: I can't tell whether the LinkedIn post «A fictional post")
    assert "Open the platform and check, then tell me:" in text
    assert len(pub.calls) == 1  # ZERO further publisher calls
    assert _keyboard_labels(q) == ["It went out", "It didn't"]


def test_it_went_out_keeps_it_blocked_and_it_didnt_releases_it(monkeypatch, tmp_path):
    pub = FakePublisher(_timeout())
    c = _cockpit(tmp_path, pub)
    token = _stage(c, monkeypatch)
    q = _press(c, f"pub:ok:{token}")
    kb = q.message.reply_kwargs[-1]["reply_markup"].inline_keyboard[0]
    went_out = kb[0].callback_data

    # [It went out]: settled, and a repeat now says "already went out"
    res = _press(c, went_out)
    assert _last(res).startswith("Noted — it went out")
    token = _stage(c, monkeypatch)
    again = _press(c, f"pub:ok:{token}")
    assert "already went out" in _last(again) and len(pub.calls) == 1

    # a second press of the same button is a no-op in plain words, not an error
    assert "already settled" in _last(_press(c, went_out))


def test_it_didnt_lets_the_post_go_again(monkeypatch, tmp_path):
    pub = FakePublisher(_timeout())
    c = _cockpit(tmp_path, pub)
    token = _stage(c, monkeypatch)
    q = _press(c, f"pub:ok:{token}")
    didnt = q.message.reply_kwargs[-1]["reply_markup"].inline_keyboard[0][1].callback_data
    assert _last(_press(c, didnt)).startswith("Noted — it didn't go out")
    pub.result = PublishResult(ok=True, status="published", post_id="p9")
    token = _stage(c, monkeypatch)
    _press(c, f"pub:ok:{token}")
    assert len(pub.calls) == 2  # released: it was allowed to go again


def test_the_intent_buttons_are_stamped_with_the_boot_epoch_when_one_is_set(monkeypatch, tmp_path):
    epoch.set_epoch("abc123")
    pub = FakePublisher(_timeout())
    c = _cockpit(tmp_path, pub)
    token = _stage(c, monkeypatch)
    q = _press(c, f"pub:ok:{token}")  # (the guard has already stripped the stamp from a real press)
    data = [b.callback_data for b in q.message.reply_kwargs[-1]["reply_markup"].inline_keyboard[0]]
    assert all(d.endswith("@abc123") for d in data)
    assert max(len(d.encode()) for d in data) <= 64


def test_a_clean_publish_is_unchanged(monkeypatch, tmp_path):
    pub = FakePublisher()
    c = _cockpit(tmp_path, pub)
    token = _stage(c, monkeypatch)
    q = _press(c, f"pub:ok:{token}")
    assert _last(q).startswith("✅ Published")
    assert q.message.reply_kwargs[-1].get("reply_markup") is None


def test_the_button_records_the_chat_that_settled_it(monkeypatch, tmp_path):
    pub = FakePublisher(_timeout())
    c = _cockpit(tmp_path, pub)
    token = _stage(c, monkeypatch)
    q = _press(c, f"pub:ok:{token}")
    didnt = q.message.reply_kwargs[-1]["reply_markup"].inline_keyboard[0][1].callback_data
    _press(c, didnt)
    import json

    rows = [
        json.loads(x)
        for x in (tmp_path / "example" / "dispatch-intents.jsonl").read_text().splitlines()
    ]
    assert [r for r in rows if r["kind"] == "resolve"][0]["by"] == f"cockpit:{CHAT}"
