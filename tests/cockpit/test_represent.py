"""F1-10 — re-presenting pending gates after a restart (always-on hardening PRD).

A pending gate is only re-sent after its authoritative record is checked: already decided, expired or older
than COCKPIT_PENDING_MAX_AGE_DAYS means DROPPED with one line, never re-presented. A valid one is re-sent
once per boot epoch, no more than once per 30 minutes, with fresh current-epoch buttons; the old message
loses its buttons.
"""

from __future__ import annotations

import asyncio
import functools
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("telegram")

from agent.cockpit_state import CockpitSettings, PendingStore  # noqa: E402
from cockpit import copy, epoch  # noqa: E402
from cockpit.represent import GateRepresenter  # noqa: E402

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
CHAT = 42
PREVIEW = "⏸ <b>Ready to publish</b>\n<pre>Canary exact bytes</pre>"


def sync(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return asyncio.run(fn(*a, **k))

    return wrapper


class Rig:
    def __init__(self, tmp_path, *, decided=lambda kind, entry, profile: False):
        epoch.set_epoch("newepc")
        self.now = NOW
        self.pending = PendingStore(
            tmp_path, chat_profile=lambda c: "example", clock=lambda: self.now
        )
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.decided_calls: list = []

        async def send(chat_id, text, reply_markup=None, parse_mode=None):
            self.sent.append(
                {"chat": chat_id, "text": text, "markup": reply_markup, "mode": parse_mode}
            )
            return 500 + len(self.sent)

        async def edit(chat_id, message_id, text, parse_mode=None):
            self.edits.append({"chat": chat_id, "id": message_id, "text": text, "mode": parse_mode})

        def is_decided(kind, entry, profile):
            self.decided_calls.append((kind, entry.get("hash"), profile))
            return decided(kind, entry, profile)

        self.rep = GateRepresenter(
            pending=self.pending,
            settings=CockpitSettings(pending_max_age_days=7),
            send=send,
            edit=edit,
            is_decided=is_decided,
            clock=lambda: self.now,
        )

    def stage_publish(
        self, token="tok1", *, staged_ago=timedelta(hours=1), message_id=11, epoch_="oldepc"
    ):
        self.pending.publish[token] = {
            "post": "Canary exact bytes",
            "media_urls": [],
            "hash": token * 8,
            "scheduled_at": None,
            "identity_used": [],
            "chat_id": CHAT,
            "profile": "example",
            "preview": PREVIEW,
            "staged_at": (NOW - staged_ago).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.pending.record_presented(
            "publish", token, chat_id=CHAT, message_id=message_id, profile="example", epoch=epoch_
        )


@pytest.fixture(autouse=True)
def _reset_epoch():
    yield
    epoch.set_epoch("")


@sync
async def test_a_valid_pending_gate_is_resent_once_with_fresh_buttons_and_the_old_one_loses_its(
    tmp_path,
):
    r = Rig(tmp_path)
    r.stage_publish()
    report = await r.rep.represent_all()
    assert report.sent == 1 and report.dropped == []
    gate = [s for s in r.sent if s["markup"] is not None]
    assert len(gate) == 1
    assert gate[0]["text"].startswith(copy.RESENT + "\n")
    assert PREVIEW in gate[0]["text"]  # the exact preview bytes, untouched
    data = [b.callback_data for row in gate[0]["markup"].inline_keyboard for b in row]
    assert all(epoch.split(d)[1] == "newepc" for d in data)  # current-epoch buttons
    assert r.edits and r.edits[0]["id"] == 11 and "superseded — see below" in r.edits[0]["text"]
    assert copy.SUPERSEDED == "superseded — see below" and copy.RESENT == "(re-sent after restart)"
    pres = r.pending.presented_for("publish", "tok1")
    assert pres["epoch"] == "newepc" and pres["message_id"] != 11


@sync
async def test_a_gate_is_not_sent_twice_in_one_epoch(tmp_path):
    r = Rig(tmp_path)
    r.stage_publish()
    await r.rep.represent_all()
    r.now += timedelta(hours=2)
    again = await r.rep.represent_all()
    assert again.sent == 0 and len([s for s in r.sent if s["markup"]]) == 1


@sync
async def test_the_same_gate_is_not_resent_within_30_minutes_across_restarts(tmp_path):
    r = Rig(tmp_path)
    r.stage_publish()
    r.pending.presented["publish:tok1"]["resent_at"] = (NOW - timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    report = await r.rep.represent_all()
    assert report.sent == 0 and report.dropped == []


@sync
async def test_stale_press_path_resends_regardless_of_the_30_minute_rule_but_once_per_epoch(
    tmp_path,
):
    r = Rig(tmp_path)
    r.stage_publish()
    r.pending.presented["publish:tok1"]["resent_at"] = (NOW - timedelta(minutes=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert (await r.rep.represent_chat(CHAT)).sent == 1
    assert (await r.rep.represent_chat(CHAT)).sent == 0  # a second stale press does not spam


@sync
async def test_decided_gates_are_dropped_with_a_one_line_notice(tmp_path):
    r = Rig(tmp_path, decided=lambda kind, entry, profile: True)
    r.stage_publish()
    report = await r.rep.represent_all()
    assert report.sent == 0 and report.dropped == ["example"]
    assert "tok1" not in r.pending.publish  # gone from the durable store too
    assert r.pending.presented_for("publish", "tok1") is None
    assert not [s for s in r.sent if s["markup"]]
    assert r.rep.dropped_notice("example").startswith("I dropped a pending approval for example:")


@sync
async def test_gates_older_than_seven_days_are_dropped_not_resent(tmp_path):
    r = Rig(tmp_path)
    r.stage_publish(staged_ago=timedelta(days=8))
    report = await r.rep.represent_all()
    assert report.sent == 0 and report.dropped == ["example"]
    assert r.decided_calls == []  # age is cheaper than the ledger and is checked first


@sync
async def test_an_expired_schedule_is_dropped(tmp_path):
    r = Rig(tmp_path)
    r.stage_publish()
    entry = dict(r.pending.publish["tok1"])
    entry["scheduled_at"] = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r.pending.publish["tok1"] = entry
    assert (await r.rep.represent_all()).dropped == ["example"]


@sync
async def test_replies_and_plan_gates_are_represented_too(tmp_path):
    r = Rig(tmp_path)
    r.pending.reply["rtok"] = {
        "body": "b",
        "thread_id": "t",
        "to": "x",
        "hash": "r" * 64,
        "chat_id": CHAT,
        "profile": "example",
        "preview": "<b>Ready to reply</b>",
    }
    r.pending.record_presented(
        "plan",
        str(CHAT),
        chat_id=CHAT,
        message_id=31,
        profile="example",
        epoch="oldepc",
        extra={"text": "[example] Plan for the week"},
    )
    report = await r.rep.represent_all()
    assert report.sent == 2
    texts = [s["text"] for s in r.sent if s["markup"]]
    assert any("Ready to reply" in t for t in texts) and any(
        "Plan for the week" in t for t in texts
    )


@sync
async def test_one_intro_line_per_profile_not_per_gate(tmp_path):
    r = Rig(tmp_path)
    r.stage_publish("tok1")
    r.stage_publish("tok2", message_id=12)
    await r.rep.represent_all()
    intros = [s for s in r.sent if s["markup"] is None]
    assert len(intros) == 1 and "2 approval(s)" in intros[0]["text"]


@sync
async def test_a_failed_edit_of_the_old_message_does_not_block_the_resend(tmp_path):
    r = Rig(tmp_path)
    r.stage_publish()

    async def boom(*a, **k):
        raise RuntimeError("message to edit not found")

    r.rep._edit = boom
    assert (await r.rep.represent_all()).sent == 1
