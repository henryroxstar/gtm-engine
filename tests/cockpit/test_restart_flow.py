"""F1/F2/F7 end to end across a simulated restart: a second Cockpit built on the same directory.

Test plan F1-1 (cockpit half), F1-8, F1-10, F2-5, F7-1 (the heartbeat task exists with no JobQueue),
and the Gate-Legibility check: after a restart the re-presented Gate 2 shows the SAME exact bytes that
the operator approved at staging (hash compared). Only the Telegram transport is faked.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import (  # noqa: E402
    FakeMsg,
    FakePublisher,
    make_cfg,
    publish_block,
    stream_of,
    text_update,
)

from agent.cockpit_state import NOTICE_CORRUPT_PENDING  # noqa: E402
from agent.publish import content_hash  # noqa: E402
from cockpit import bot as botmod  # noqa: E402
from cockpit import copy, epoch  # noqa: E402

CHAT = 91


class FakeBot:
    defaults = None

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._next = 1000

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*a, **kw):
            self.calls.append((name, kw))
            self._next += 1
            return SimpleNamespace(message_id=self._next)

        return call

    def sent(self):
        return [kw for n, kw in self.calls if n == "send_message"]

    def edited(self):
        return [kw for n, kw in self.calls if n == "edit_message_text"]


@pytest.fixture(autouse=True)
def _epoch_reset():
    yield
    epoch.set_epoch("")


def _cockpit(tmp_path, *, publisher=None):
    c = botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT}))
    if publisher is not None:
        c._publisher = publisher
    c._app = SimpleNamespace(bot=FakeBot())
    return c


def _stage(cockpit, monkeypatch, post, message_id=55):
    monkeypatch.setattr(cockpit.store, "run", stream_of(publish_block(post, ())))
    msg = FakeMsg(CHAT, text="draft")
    msg.message_id = message_id
    update, context = text_update(CHAT, msg)
    asyncio.run(cockpit.on_text(update, context))
    return msg


def _boot(cockpit):
    asyncio.run(cockpit.boot_announce())
    return cockpit._app.bot


def test_a_pending_publish_survives_a_restart_and_is_represented_once(monkeypatch, tmp_path):
    post = "The exact post the operator approved."
    first = _cockpit(tmp_path, publisher=FakePublisher())
    _stage(first, monkeypatch, post)
    token = content_hash(post, ())[:16]
    staged = dict(first._pending_publish[token])

    second = _cockpit(tmp_path, publisher=FakePublisher())  # the restart
    assert second._pending_publish[token]["hash"] == staged["hash"]  # F1-1, cockpit half
    bot = _boot(second)

    gate = [s for s in bot.sent() if s.get("reply_markup") is not None]
    assert len(gate) == 1
    assert gate[0]["text"].startswith(copy.RESENT)
    # Gate legibility: the same exact preview bytes the operator approved at staging.
    assert staged["preview"] in gate[0]["text"]
    assert content_hash(second._pending_publish[token]["post"], ()) == staged["hash"]
    data = [b.callback_data for row in gate[0]["reply_markup"].inline_keyboard for b in row]
    assert all(epoch.split(d)[1] == epoch.current() != "" for d in data)
    # the previous message loses its buttons and says it was superseded
    assert any(copy.SUPERSEDED in e["text"] and e["message_id"] == 55 for e in bot.edited())

    third = _cockpit(tmp_path, publisher=FakePublisher())  # restarted again straight away
    bot3 = _boot(third)
    assert not [
        s for s in bot3.sent() if s.get("reply_markup") is not None
    ]  # within 30 minutes: not again


def test_a_gate_already_published_elsewhere_is_dropped_with_one_line(monkeypatch, tmp_path):
    post = "Already went out."
    first = _cockpit(tmp_path, publisher=FakePublisher())
    _stage(first, monkeypatch, post)
    hist = tmp_path / "example" / "history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "event": "published",
        "content_sha256": content_hash(post, ()),
        "ts": "2026-10-19T00:00:00Z",
    }
    hist.write_text(json.dumps(row) + "\n")

    second = _cockpit(tmp_path, publisher=FakePublisher())
    bot = _boot(second)
    assert not [s for s in bot.sent() if s.get("reply_markup") is not None]
    assert any(
        s["text"].startswith("I dropped a pending approval for example:") for s in bot.sent()
    )
    assert content_hash(post, ())[:16] not in second._pending_publish


def test_corrupt_pending_file_resets_and_tells_the_operator(tmp_path):
    p = tmp_path / "example" / "_cockpit" / "pending.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    c = _cockpit(tmp_path)
    bot = _boot(c)
    assert any(s["text"] == NOTICE_CORRUPT_PENDING for s in bot.sent())
    assert dict(c._pending_publish) == {}


def test_f1_8_a_lost_permission_ask_or_wizard_is_announced_once(tmp_path):
    first = _cockpit(tmp_path)
    first._state.set_volatile("permission", CHAT, True)
    first._state.set_volatile("wizard", CHAT, True)
    second = _cockpit(tmp_path)
    bot = _boot(second)
    assert [s["text"] for s in bot.sent()].count(copy.RESTART_LOST_STATE) == 1
    third = _cockpit(tmp_path)
    assert copy.RESTART_LOST_STATE not in [s["text"] for s in _boot(third).sent()]


def test_f2_5_three_restarts_in_ten_minutes_trip_safe_mode_with_one_alert(monkeypatch, tmp_path):
    post = "Pending when the crash loop starts."
    seed = _cockpit(tmp_path, publisher=FakePublisher())
    _stage(seed, monkeypatch, post)
    alerts = 0
    for i in range(3):
        c = _cockpit(tmp_path, publisher=FakePublisher())
        c._begin_boot()
        bot = _boot(c)
        n = [s for s in bot.sent() if s["text"] == copy.SAFE_MODE_ALERT]
        alerts += len(n)
        if i == 2:
            assert c._boot.safe_mode is True
            assert c.drop_pending_updates is True
            assert not [
                s for s in bot.sent() if s.get("reply_markup") is not None
            ]  # no re-presentation
        else:
            assert c.drop_pending_updates is False
    assert alerts == 1
    assert copy.SAFE_MODE_ALERT == "I restarted 3 times in 10 minutes; skipped my backlog"


def test_build_application_wires_the_guard_the_heartbeat_and_no_job_queue(monkeypatch, tmp_path):
    cfg = make_cfg(tmp_path, chat_ids={CHAT})
    cfg.telegram_bot_token = "123:fake"
    app = botmod.build_application(cfg)
    groups = {g: [type(h).__name__ for h in hs] for g, hs in app.handlers.items()}
    assert (
        "TypeHandler" in groups[-1] and "TypeHandler" in groups[100]
    )  # guard before, finish after
    assert app.job_queue is None  # E16: the heartbeat must not need one
    assert app.post_init is not None and app.post_stop is not None
    assert epoch.current() != ""  # the epoch was minted when the handlers were registered


def test_kill_switch_keep_pending_updates_off_drops_them(monkeypatch, tmp_path):
    monkeypatch.setenv("COCKPIT_KEEP_PENDING_UPDATES", "0")
    c = _cockpit(tmp_path)
    c._begin_boot()
    assert c.drop_pending_updates is True
