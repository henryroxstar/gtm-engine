"""F2 — the restart guard: buttons by boot epoch, everything else by age (always-on hardening PRD).

Test plan F2-1..F2-4, F1-8. Real python-telegram-bot ``Update`` objects (parsed from Bot-API JSON, no
network) and the real state/pending stores on tmp_path. Only the Telegram transport is faked — and the
plan's §6 says exactly what that cannot prove (the real-bot check is the non-circular one).
"""

from __future__ import annotations

import asyncio
import functools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")

from telegram.ext import ApplicationHandlerStop  # noqa: E402

from agent.cockpit_state import CockpitSettings, CockpitState, PendingStore  # noqa: E402
from cockpit import copy, epoch  # noqa: E402
from cockpit.guard import UpdateGuard, digest_keyboard  # noqa: E402
from telegram import Update  # noqa: E402

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
CHAT = 42


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))

    return wrapper


class FakeBot:
    """Records the Bot API calls the guard makes. Accepts anything PTB objects forward to it."""

    defaults = None  # PTB reads bot.defaults.tzinfo while parsing dates

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*a, **kw):
            self.calls.append((name, {"args": a, **kw}))
            return SimpleNamespace(message_id=9000 + len(self.calls))

        return call

    def texts(self, method="send_message"):
        return [c[1].get("text") for c in self.calls if c[0] == method]

    def answers(self):
        return [c[1].get("text") for c in self.calls if c[0] == "answer_callback_query"]


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _chat():
    return {"id": CHAT, "type": "private"}


def make_update(
    bot, uid, kind, *, age_s=0.0, now=NOW, text="hello world", data=None, msg_age_s=None
):
    """Build a real Update. ``kind``: text|command|voice|photo|document|edited|callback."""
    date = _ts(now - timedelta(seconds=age_s))
    base = {
        "message_id": 100 + uid,
        "date": date,
        "chat": _chat(),
        "from": {"id": CHAT, "is_bot": False, "first_name": "T"},
    }
    if kind == "text":
        payload = {"message": {**base, "text": text}}
    elif kind == "command":
        payload = {
            "message": {
                **base,
                "text": "/cost",
                "entities": [{"type": "bot_command", "offset": 0, "length": 5}],
            }
        }
    elif kind == "voice":
        payload = {
            "message": {**base, "voice": {"file_id": "v1", "file_unique_id": "u1", "duration": 3}}
        }
    elif kind == "photo":
        payload = {
            "message": {
                **base,
                "photo": [{"file_id": "p1", "file_unique_id": "q1", "width": 9, "height": 9}],
                "caption": "a photo",
            }
        }
    elif kind == "document":
        payload = {
            "message": {
                **base,
                "document": {
                    "file_id": "d1",
                    "file_unique_id": "w1",
                    "file_name": "leads.csv",
                    "mime_type": "text/csv",
                },
                "caption": "csv",
            }
        }
    elif kind == "edited":
        payload = {"edited_message": {**base, "text": text, "edit_date": date}}
    elif kind == "callback":
        mdate = _ts(now - timedelta(seconds=msg_age_s if msg_age_s is not None else 5))
        payload = {
            "callback_query": {
                "id": f"cb{uid}",
                "from": {"id": CHAT, "is_bot": False, "first_name": "T"},
                "chat_instance": "ci",
                "data": data,
                "message": {"message_id": 7, "date": mdate, "chat": _chat(), "text": "gate"},
            }
        }
    else:  # pragma: no cover
        raise AssertionError(kind)
    return Update.de_json({"update_id": uid, **payload}, bot)


class Rig:
    def __init__(self, tmp_path, *, safe_mode=False, boot_offset_s=3600):
        self.bot = FakeBot()
        self.clock_now = NOW
        self.state = CockpitState(tmp_path, clock=lambda: self.clock_now)
        self.boot = self.state.start_boot()
        epoch.set_epoch(self.boot.epoch)
        self.pending = PendingStore(
            tmp_path, chat_profile=lambda c: "example", clock=lambda: self.clock_now
        )
        self.replayed: list[Update] = []
        self.represented: list[int] = []

        async def replay(update):
            self.replayed.append(update)

        async def represent(chat_id):
            self.represented.append(chat_id)

        async def send(chat_id, text, reply_markup=None):
            m = await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
            return m.message_id

        self.guard = UpdateGuard(
            state=self.state,
            pending=self.pending,
            settings=CockpitSettings(max_update_age_s=900),
            send=send,
            replay=replay,
            represent=represent,
            is_allowed=lambda update: True,
            profile_of=lambda chat: "example",
            clock=lambda: self.clock_now,
            boot_time=NOW
            - timedelta(seconds=boot_offset_s // 6),  # booted 10 min before NOW by default
            collect_window_s=0.0,
        )

    def upd(self, uid, kind, **kw):
        return make_update(self.bot, uid, kind, **kw)

    async def feed(self, update):
        """Run the pre-handler; True if the update reached the handlers, False if the guard consumed it."""
        try:
            await self.guard.pre(update, None)
        except ApplicationHandlerStop:
            return False
        return True


@pytest.fixture
def rig(tmp_path):
    epoch.set_epoch("")
    r = Rig(tmp_path)
    yield r
    epoch.set_epoch("")


# ── F2-1 epoch on buttons ────────────────────────────────────────────────────


def test_f2_1_stamp_and_split_round_trip(rig):
    s = epoch.stamp("pub:ok:abcdef0123456789")
    assert s.endswith("@" + rig.boot.epoch)
    assert epoch.split(s) == ("pub:ok:abcdef0123456789", rig.boot.epoch)
    assert epoch.split("pub:ok:abc") == ("pub:ok:abc", None)
    assert epoch.split("@") == ("@", None)


def test_f2_1_longest_real_callback_data_fits_telegram_64_bytes(rig):
    from cockpit.gates import _plan_gate_keyboard, _publish_keyboard, _reply_keyboard

    token = "f" * 16
    kbs = [
        _publish_keyboard(token),
        _reply_keyboard(token),
        _plan_gate_keyboard(),
        digest_keyboard(),
    ]
    longest = max(
        len(b.callback_data.encode()) for kb in kbs for row in kb.inline_keyboard for b in row
    )
    assert longest <= 64
    assert (
        "perm:ok:" + "f" * 16 + "@" + rig.boot.epoch
        and len(epoch.stamp("perm:ok:" + "f" * 16)) <= 64
    )


@pytest.mark.parametrize("suffix", ["", "@", "@zzzzzz", "@@@", "@" + "x" * 40])
@sync
async def test_f2_1_old_missing_and_malformed_epochs_are_refused_with_the_resent_reply(rig, suffix):
    q = rig.upd(1, "callback", data="pub:ok:abcdef0123456789" + suffix)
    reached = await rig.feed(q)
    assert reached is False
    assert rig.bot.answers() == [copy.STALE_BUTTON]
    assert rig.represented == [CHAT]  # the gate is re-sent, not just refused
    assert copy.STALE_BUTTON == "that button is from before a restart — I've re-sent it below"


@sync
async def test_f2_1_current_epoch_is_accepted_even_hours_after_the_message(rig):
    data = epoch.stamp("pub:ok:abcdef0123456789")
    q = rig.upd(2, "callback", data=data, msg_age_s=6 * 3600)  # message is 6 hours old
    assert await rig.feed(q) is True
    assert q.callback_query.data == "pub:ok:abcdef0123456789"  # stamp removed for the handlers
    assert rig.bot.answers() == []


@sync
async def test_f2_1_a_stale_permission_ask_is_refused_without_a_false_resent_claim(rig):
    q = rig.upd(3, "callback", data="perm:ok:0123456789abcdef@old000")
    assert await rig.feed(q) is False
    assert rig.bot.answers() == [copy.STALE_PERMISSION]
    assert rig.represented == []


@sync
async def test_hooks_buttons_are_outside_the_epoch_rule(rig):
    q = rig.upd(4, "callback", data="hooks:revive:some-hook")
    assert await rig.feed(q) is True


# ── F2-2 age gate for every message type ─────────────────────────────────────

CONTENT = ["text", "voice", "photo", "document", "edited"]


@pytest.mark.parametrize("kind", [*CONTENT, "command"])
@sync
async def test_f2_2_fresh_messages_are_delivered(rig, kind):
    u = rig.upd(10, kind, age_s=30)
    assert await rig.feed(u) is True
    assert rig.pending.digest_items(CHAT, "example") == []


@pytest.mark.parametrize("kind", [*CONTENT, "command"])
@sync
async def test_f2_2_boundary_900_is_fresh_901_is_old(rig, kind):
    assert await rig.feed(rig.upd(11, kind, age_s=900)) is True
    assert await rig.feed(rig.upd(12, kind, age_s=901)) is False


@sync
async def test_f2_2_sent_while_offline_gets_the_prefix_on_the_brain_input(tmp_path):
    epoch.set_epoch("")
    r = Rig(tmp_path, boot_offset_s=3600)  # booted 600 s before NOW
    u = r.upd(13, "text", age_s=700)  # sent before this boot but within the 900 s window
    assert await r.feed(u) is True
    hhmm = (NOW - timedelta(seconds=700)).strftime("%H:%M")
    assert r.guard.offline_prefix(u.message) == f"(sent at {hhmm} while I was offline) "
    live = r.upd(14, "text", age_s=5)
    await r.feed(live)
    assert r.guard.offline_prefix(live.message) == ""


@sync
async def test_f2_2_an_old_command_is_refused_not_run_and_not_kept(rig):
    assert await rig.feed(rig.upd(15, "command", age_s=5000)) is False
    assert rig.bot.texts() == [copy.STALE_COMMAND]
    assert rig.pending.digest_items(CHAT, "example") == []
    assert copy.STALE_COMMAND == "that command is from before a restart — send it again"


@sync
async def test_f2_2_unknown_age_is_stale(rig):
    u = rig.upd(16, "text", age_s=1)
    with u.message._unfrozen():
        u.message.date = None
    assert await rig.feed(u) is False


# ── F2-3 no unattended spend ─────────────────────────────────────────────────


@sync
async def test_f2_3_a_backlog_of_every_type_starts_zero_turns_and_sends_one_digest(tmp_path):
    epoch.set_epoch("")
    r = Rig(tmp_path)
    kinds = (CONTENT * 4)[:20]
    for i, kind in enumerate(kinds):
        assert (
            await r.feed(r.upd(100 + i, kind, age_s=4000 + i, text=f"run prospecting {i}")) is False
        )
    await r.guard.flush_digest(CHAT)
    assert not (tmp_path / "example" / "costs.jsonl").exists()  # zero cost rows
    assert r.replayed == []  # zero turns started
    digests = [c for c in r.bot.calls if c[0] == "send_message"]
    assert len(digests) == 1
    markup = digests[0][1]["reply_markup"]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == [copy.DIGEST_RUN_LABEL, copy.DIGEST_DISCARD_LABEL]
    assert digests[0][1]["text"].startswith("20 messages arrived while I was offline (from ")
    assert "«run prospecting 0»" in digests[0][1]["text"]
    for row in markup.inline_keyboard:
        for b in row:
            assert epoch.split(b.callback_data)[1] == r.boot.epoch  # current-epoch buttons


@sync
async def test_f2_3_discard_clears_and_run_replays_as_one_marked_turn(tmp_path):
    epoch.set_epoch("")
    r = Rig(tmp_path)
    for i in range(3):
        await r.feed(r.upd(200 + i, "text", age_s=4000 + i, text=f"note {i}"))
    await r.feed(r.upd(210, "voice", age_s=4100))
    await r.guard.flush_digest(CHAT)
    run_data = [b.callback_data for b in r.bot.calls[-1][1]["reply_markup"].inline_keyboard[0]][0]
    q = r.upd(300, "callback", data=run_data)
    assert await r.feed(q) is False  # consumed by the guard
    texts = [u for u in r.replayed if u.message and u.message.text]
    assert len(texts) == 1  # the three texts become ONE turn
    body = texts[0].message.text
    assert body.startswith("(3 messages from while I was offline)")
    assert body.index("note 0") < body.index("note 1") < body.index("note 2")
    assert any(u.message and u.message.voice for u in r.replayed)  # media replays on its own
    assert r.pending.digest_items(CHAT, "example") == []

    # discard path
    r2 = Rig(tmp_path / "second")
    await r2.feed(r2.upd(400, "text", age_s=5000, text="stale"))
    await r2.guard.flush_digest(CHAT)
    drop = [
        b.callback_data for row in r2.bot.calls[-1][1]["reply_markup"].inline_keyboard for b in row
    ][1]
    assert await r2.feed(r2.upd(401, "callback", data=drop)) is False
    assert r2.replayed == [] and r2.pending.digest_items(CHAT, "example") == []
    assert r2.bot.answers()[-1] == copy.DIGEST_DISCARDED


@sync
async def test_f2_3_old_digest_buttons_from_a_previous_boot_do_nothing(tmp_path):
    epoch.set_epoch("")
    r = Rig(tmp_path)
    await r.feed(r.upd(500, "text", age_s=4000))
    q = r.upd(501, "callback", data="dig:run@old000")
    assert await r.feed(q) is False
    assert r.replayed == []
    assert (
        r.pending.digest_items(CHAT, "example") != []
    )  # still waiting; the new digest has live buttons


@sync
async def test_digest_survives_a_restart_because_it_is_persisted(tmp_path):
    epoch.set_epoch("")
    r = Rig(tmp_path)
    await r.feed(r.upd(600, "text", age_s=4000, text="keep me"))
    r2 = Rig(tmp_path)
    r2.pending.load_all()
    assert len(r2.pending.digest_items(CHAT, "example")) == 1


# ── F2-4 dedup and order ─────────────────────────────────────────────────────


@sync
async def test_f2_4_same_update_id_twice_is_handled_once(rig):
    u = rig.upd(700, "text", age_s=1)
    assert await rig.feed(u) is True
    await rig.guard.finish(u)  # the post-handler marks it handled
    assert await rig.feed(rig.upd(700, "text", age_s=1)) is False


@sync
async def test_f2_4_out_of_order_id_is_still_handled(rig):
    for uid in (710, 712):
        u = rig.upd(uid, "text", age_s=1)
        assert await rig.feed(u) is True
        await rig.guard.finish(u)
    assert await rig.feed(rig.upd(711, "text", age_s=1)) is True
    assert await rig.feed(rig.upd(712, "text", age_s=1)) is False


@sync
async def test_in_flight_is_recorded_before_the_handler_and_cleared_after(rig):
    u = rig.upd(720, "text", age_s=1)
    await rig.feed(u)
    assert 720 in rig.state.in_flight
    await rig.guard.finish(u)
    assert 720 not in rig.state.in_flight


@sync
async def test_a_consumed_update_does_not_stay_in_flight(rig):
    await rig.feed(rig.upd(730, "command", age_s=5000))
    assert 730 not in rig.state.in_flight
    assert rig.state.should_skip(730)


@sync
async def test_disallowed_chats_pass_through_untouched(rig):
    rig.guard._is_allowed = lambda update: False
    assert (
        await rig.feed(rig.upd(740, "text", age_s=99999)) is True
    )  # the handlers' own whitelist drops it
    assert rig.bot.calls == []


@sync
async def test_kill_switch_keep_pending_updates_off_restores_age_blindness(tmp_path):
    epoch.set_epoch("")
    r = Rig(tmp_path)
    r.guard._settings = CockpitSettings(keep_pending_updates=False)
    assert await r.feed(r.upd(750, "text", age_s=99999)) is True
