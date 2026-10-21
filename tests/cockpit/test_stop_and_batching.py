"""F3 — `/stop`, batching, and the scheduled-run lock PROBE (test plan F3-3, F3-4, F3-5).

The turn runner (``store.run``) is faked; the queue, the stop handler, the lock probe and the real
``profile_lock`` file lock are real. The real-SDK half of "interrupt ends a running turn within 5 s" is in
tests/live_sdk/test_stop_live.py.
"""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from fakes import FakeMsg, make_cfg, stream_of, text_update  # noqa: E402

from agent import inflight  # noqa: E402
from cockpit import bot as botmod  # noqa: E402
from cockpit import copy  # noqa: E402
from gtm_core.locks import profile_lock  # noqa: E402

CHAT = 91


def _cockpit(tmp_path):
    return botmod.Cockpit(make_cfg(tmp_path, chat_ids={CHAT}))


def _send(cockpit, text):
    msg = FakeMsg(CHAT, text=text)
    update, context = text_update(CHAT, msg)
    asyncio.run(cockpit.on_text(update, context))
    return msg


def _stop(cockpit):
    msg = FakeMsg(CHAT, text="/stop")
    update, context = text_update(CHAT, msg)
    asyncio.run(cockpit.cmd_stop(update, context))
    return msg


def _body(msg) -> str:
    return msg.replies[-1].split("] ", 1)[-1]


# ── F3-4 batching ────────────────────────────────────────────────────────────


def test_f3_4_three_messages_during_a_turn_become_exactly_one_marked_follow_up_turn(
    monkeypatch, tmp_path
):
    c = _cockpit(tmp_path)
    prompts: list[str] = []

    async def run(chat_id, prompt):
        prompts.append(prompt)
        if (
            len(prompts) == 1
        ):  # while the first turn runs, three messages arrive (the processor buffers them)
            for t in ("alpha", "bravo", "charlie"):
                assert c._queue.buffer_text(CHAT, t)
        yield "ok"

    monkeypatch.setattr(c.store, "run", run)
    _send(c, "start")
    assert len(prompts) == 2  # the original turn + exactly ONE follow-up, not three
    follow = prompts[1]
    assert follow.startswith("(3 messages while you were busy)\n")
    assert follow.index("alpha") < follow.index("bravo") < follow.index("charlie")
    assert copy.BUSY_BATCH.format(n=3) == "(3 messages while you were busy)"
    assert not c._queue.is_busy(CHAT)  # the turn ended cleanly


def test_a_single_buffered_message_is_marked_in_the_singular(monkeypatch, tmp_path):
    c = _cockpit(tmp_path)
    prompts: list[str] = []

    async def run(chat_id, prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            c._queue.buffer_text(CHAT, "just one")
        yield "ok"

    monkeypatch.setattr(c.store, "run", run)
    _send(c, "start")
    assert prompts[1].startswith("(1 message while you were busy)\n")


# ── F3-3 /stop ───────────────────────────────────────────────────────────────


def test_f3_3_stop_interrupts_drops_the_buffer_and_says_exactly_what_it_did(monkeypatch, tmp_path):
    c = _cockpit(tmp_path)
    interrupted: list[int] = []

    async def fake_interrupt(chat_id):
        interrupted.append(chat_id)
        return True

    monkeypatch.setattr(c.store, "interrupt", fake_interrupt)
    c._queue.begin(CHAT)
    c._queue.buffer_text(CHAT, "one")
    c._queue.buffer_text(CHAT, "two")
    msg = _stop(c)
    assert interrupted == [CHAT]
    assert _body(msg) == "stopped; dropped 2 waiting messages"
    assert c._queue.take(CHAT) == []  # neither runs afterwards


def test_stop_pluralises_and_stays_short_when_nothing_was_waiting(monkeypatch, tmp_path):
    c = _cockpit(tmp_path)

    async def fake_interrupt(chat_id):
        return True

    monkeypatch.setattr(c.store, "interrupt", fake_interrupt)
    c._queue.begin(CHAT)
    c._queue.buffer_text(CHAT, "only")
    assert _body(_stop(c)) == "stopped; dropped 1 waiting message"
    assert _body(_stop(c)) == "stopped"


def test_stop_with_nothing_running_says_so(monkeypatch, tmp_path):
    c = _cockpit(tmp_path)

    async def fake_interrupt(chat_id):
        return False

    monkeypatch.setattr(c.store, "interrupt", fake_interrupt)
    assert _body(_stop(c)) == "nothing is running"


def test_f3_3_stop_never_cancels_a_dispatch_in_flight_and_says_it_continues(monkeypatch, tmp_path):
    c = _cockpit(tmp_path)

    async def fake_interrupt(chat_id):
        return True

    monkeypatch.setattr(c.store, "interrupt", fake_interrupt)
    c._queue.begin(CHAT)
    finished = threading.Event()
    release = threading.Event()

    def dispatch():
        with inflight.track("email"):
            release.wait(2)
        finished.set()

    t = threading.Thread(target=dispatch)
    t.start()
    time.sleep(0.05)
    try:
        msg = _stop(c)
        assert (
            _body(msg) == "stopped; the email send is still running; it can't be stopped from here"
        )
        assert not finished.is_set()  # /stop did not touch it
    finally:
        release.set()
        t.join()
    assert finished.is_set()


def test_stop_is_registered_and_described(tmp_path):
    from cockpit.commands import _COMMAND_DESCRIPTIONS

    c = _cockpit(tmp_path)
    assert "stop" in [name for name, _ in c.registered_commands()]
    assert "stop" in _COMMAND_DESCRIPTIONS


# ── F3-5 lock probe, not hold ────────────────────────────────────────────────


def _hold_lock(root: str, ready, release) -> None:  # child process
    with profile_lock(Path(root), "example"):
        ready.set()
        release.wait(10)


def test_f3_5_a_turn_during_a_scheduled_run_is_refused_with_the_text_echoed_and_nothing_queued(
    monkeypatch, tmp_path
):
    c = _cockpit(tmp_path)
    ran: list[str] = []

    async def run(chat_id, prompt):
        ran.append(prompt)
        yield "ok"

    monkeypatch.setattr(c.store, "run", run)
    ctx = multiprocessing.get_context("fork")
    ready, release = ctx.Event(), ctx.Event()
    p = ctx.Process(target=_hold_lock, args=(str(tmp_path), ready, release))
    p.start()
    assert ready.wait(5)
    try:
        msg = _send(c, "please run the radar now")
    finally:
        release.set()
        p.join()
    assert ran == []  # the brain did not run
    body = _body(msg)
    assert body.startswith(
        "a scheduled run is working on example — send this again when it finishes"
    )
    assert "please run the radar now" in body  # echoed back so the operator can resend it
    assert (
        copy.TIMER_BUSY.format(profile="example")
        == "a scheduled run is working on example — send this again when it finishes"
    )
    # nothing was queued: sending it again once the lock is free simply runs
    _send(c, "please run the radar now")
    assert ran == ["please run the radar now"]


def test_f3_5_the_cockpit_does_not_hold_the_lock_across_a_turn(monkeypatch, tmp_path):
    """Holding it would self-deadlock a turn that starts a pipeline stage (whose runner takes the same
    file lock) and would stall timer runs behind a parked approval. A second probe MID-turn succeeds."""
    c = _cockpit(tmp_path)
    mid_turn: list[str] = []

    async def run(chat_id, prompt):
        from gtm_core.locks import LockBusy

        try:
            with profile_lock(tmp_path, "example", blocking=False):  # what a runner stage does
                mid_turn.append("acquired")
        except LockBusy:
            mid_turn.append("BUSY")
        yield "ok"

    monkeypatch.setattr(c.store, "run", run)
    _send(c, "hello")
    assert mid_turn == ["acquired"]


def test_a_lock_probe_failure_never_blocks_the_operator(monkeypatch, tmp_path):
    c = _cockpit(tmp_path)
    monkeypatch.setattr(
        "cockpit.delivery.profile_lock", lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
    )
    monkeypatch.setattr(c.store, "run", stream_of("fine"))
    msg = _send(c, "hi")
    assert "fine" in "".join(msg.edits + msg.replies)


# ── wiring and the kill switch ───────────────────────────────────────────────


def test_the_application_uses_the_per_chat_processor_and_the_kill_switch_restores_the_default(
    monkeypatch, tmp_path
):
    from telegram.ext import SimpleUpdateProcessor

    from cockpit.chatqueue import PerChatProcessor

    cfg = make_cfg(tmp_path, chat_ids={CHAT})
    cfg.telegram_bot_token = "123:fake"
    app = botmod.build_application(cfg)
    assert isinstance(app.update_processor, PerChatProcessor)

    monkeypatch.setenv("COCKPIT_PER_CHAT_QUEUE", "0")
    off = botmod.build_application(cfg)
    assert isinstance(
        off.update_processor, SimpleUpdateProcessor
    )  # today's one-at-a-time behaviour


def test_a_turn_cleans_up_its_busy_state_even_when_the_brain_errors(monkeypatch, tmp_path):
    c = _cockpit(tmp_path)

    async def run(chat_id, prompt):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(c.store, "run", run)
    _send(c, "hello")
    assert not c._queue.is_busy(CHAT)  # a crashed turn must not leave the chat 'busy' for ever
