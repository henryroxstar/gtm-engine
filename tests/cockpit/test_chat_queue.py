"""F3 — the per-chat update processor (always-on hardening test plan F3-0, F3-1, F3-2).

Real python-telegram-bot ``Update`` objects and PTB's own ``SimpleUpdateProcessor`` for the "before" picture;
no network. F3-0 reproduces E5's *mechanism* — a handler awaiting a button press can never see it when updates
are processed one at a time — with PTB's real default processor (the offline equivalent of the plan's test-bot
reproduction, which the operator declined to run live).
"""

from __future__ import annotations

import asyncio
import functools

import pytest

pytest.importorskip("telegram")

from telegram.ext import SimpleUpdateProcessor  # noqa: E402
from test_guard import FakeBot, make_update  # noqa: E402

from cockpit.chatqueue import ChatQueue, PerChatProcessor, classify  # noqa: E402


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))

    return wrapper


BOT = FakeBot()


def upd(uid, kind, **kw):
    return make_update(BOT, uid, kind, **kw)


# ── classification ───────────────────────────────────────────────────────────


def test_classify_buttons_stop_text_and_the_rest():
    assert classify(upd(1, "callback", data="x"))[1] == "button"
    assert classify(upd(2, "command"))[1] == "command"
    assert classify(upd(3, "text"))[1] == "text"
    assert classify(upd(4, "voice"))[1] == "other"
    # a real /stop carries the bot_command entity
    from telegram import Update

    s = Update.de_json(
        {
            "update_id": 6,
            "message": {
                "message_id": 1,
                "date": 1,
                "chat": {"id": 42, "type": "private"},
                "text": "/stop@some_bot",
                "entities": [{"type": "bot_command", "offset": 0, "length": 14}],
            },
        },
        BOT,
    )
    assert classify(s) == (42, "stop")
    assert classify(upd(7, "text", text="/stopwatch"))[1] == "command"  # a command, but NOT /stop


# ── F3-0: the mechanism the processor exists to fix ──────────────────────────


@sync
async def test_f3_0_default_one_at_a_time_processing_cannot_deliver_the_button_a_handler_awaits():
    proc = SimpleUpdateProcessor(1)
    await proc.initialize()
    pressed = asyncio.Event()
    done = []

    async def waiting_handler():  # approve-and-resume: awaits the operator's button press
        await asyncio.wait_for(pressed.wait(), timeout=0.5)
        done.append("handler")

    async def button_handler():
        pressed.set()
        done.append("button")

    t1 = asyncio.create_task(proc.process_update(upd(1, "text"), waiting_handler()))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(
        proc.process_update(upd(2, "callback", data="perm:ok:x"), button_handler())
    )
    with pytest.raises(TimeoutError):
        await t1  # the ask can only time out, exactly as E5 inferred
    await t2
    assert done == ["button"]  # the button was processed only AFTER the ask had already timed out
    await proc.shutdown()


@sync
async def test_f3_0_the_per_chat_processor_delivers_the_button_while_the_handler_waits():
    proc = PerChatProcessor(ChatQueue(), max_concurrent_updates=8)
    await proc.initialize()
    pressed = asyncio.Event()
    done = []

    async def waiting_handler():
        await asyncio.wait_for(pressed.wait(), timeout=2)
        done.append("handler")

    async def button_handler():
        pressed.set()
        done.append("button")

    t1 = asyncio.create_task(proc.process_update(upd(1, "command"), waiting_handler()))
    await asyncio.sleep(0.05)
    await proc.process_update(upd(2, "callback", data="perm:ok:x"), button_handler())
    await t1
    assert done == ["button", "handler"]
    await proc.shutdown()


# ── F3-1: ordering within a chat, concurrency across chats ───────────────────


@sync
async def test_f3_1_one_chats_updates_never_interleave_but_two_chats_do():
    proc = PerChatProcessor(ChatQueue(), max_concurrent_updates=8)
    await proc.initialize()
    log: list[str] = []

    def handler(name):
        async def h():
            log.append(f"{name}:start")
            await asyncio.sleep(0.05)
            log.append(f"{name}:end")

        return h()

    def in_chat(uid, chat, kind="command"):
        u = upd(uid, kind)
        with u.message._unfrozen():
            u.message.chat._unfreeze() if hasattr(u.message.chat, "_unfreeze") else None
        return u

    # two photos (non-text, non-bypass => ordered) in chat 42, one in a second chat
    a1, a2 = upd(1, "voice"), upd(2, "voice")
    other = upd(3, "voice")
    with other.message.chat._unfrozen():
        other.message.chat.id = 77
    await asyncio.gather(
        proc.process_update(a1, handler("A1")),
        proc.process_update(a2, handler("A2")),
        proc.process_update(other, handler("B1")),
    )
    assert log.index("A1:end") < log.index("A2:start")  # same chat: strictly ordered
    assert log.index("B1:start") < log.index("A1:end")  # other chat: ran alongside A1
    await proc.shutdown()


# ── F3-2: buttons and /stop bypass the lock even while a turn is running ─────


@sync
async def test_f3_2_a_button_and_stop_are_handled_while_the_same_chats_turn_is_running():
    q = ChatQueue()
    proc = PerChatProcessor(q, max_concurrent_updates=8)
    await proc.initialize()
    release = asyncio.Event()
    events: list[str] = []

    async def long_turn():
        events.append("turn:start")
        await release.wait()
        events.append("turn:end")

    async def quick(name):
        events.append(name)

    q.begin(42)  # the chat has a turn in progress
    turn = asyncio.create_task(proc.process_update(upd(1, "voice"), long_turn()))
    await asyncio.sleep(0.05)
    await proc.process_update(upd(2, "callback", data="gate:plan:approve"), quick("button"))
    from telegram import Update

    stop = Update.de_json(
        {
            "update_id": 3,
            "message": {
                "message_id": 3,
                "date": 1,
                "chat": {"id": 42, "type": "private"},
                "text": "/stop",
                "entities": [{"type": "bot_command", "offset": 0, "length": 5}],
            },
        },
        BOT,
    )
    await proc.process_update(stop, quick("stop"))
    release.set()
    await turn
    assert events == ["turn:start", "button", "stop", "turn:end"]
    await proc.shutdown()


# ── batching state ───────────────────────────────────────────────────────────


@sync
async def test_text_that_arrives_during_a_turn_is_buffered_not_run_and_a_second_busy_update_waits():
    q = ChatQueue()
    proc = PerChatProcessor(q, max_concurrent_updates=8)
    await proc.initialize()
    ran: list[str] = []

    async def handler():
        ran.append("handler")

    q.begin(42)
    for i, t in enumerate(("one", "two", "three")):
        await proc.process_update(upd(10 + i, "text", text=t), handler())
    assert ran == []  # none of the three reached a handler
    assert q.take(42) == ["one", "two", "three"]  # in order
    assert q.take(42) == []  # taken once
    q.end(42)
    await proc.process_update(upd(20, "text", text="later"), handler())
    assert ran == ["handler"]  # not busy any more: it runs normally
    await proc.shutdown()


def test_discard_drops_the_buffer_and_reports_how_many():
    q = ChatQueue()
    q.begin(1)
    q.buffer_text(1, "a")
    q.buffer_text(1, "b")
    assert q.discard(1) == 2 and q.take(1) == [] and q.discard(1) == 0
    assert q.is_busy(1)  # discarding does not end the turn
    q.end(1)
    assert not q.is_busy(1)


def test_buffering_for_a_chat_that_is_not_busy_is_refused():
    q = ChatQueue()
    assert q.buffer_text(1, "x") is False
