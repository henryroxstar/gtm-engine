"""F1 — durable cockpit state (always-on hardening PRD, Phase 1).

Test plan: the always-on hardening test plan (2026-10-19) F1-1, F1-3..F1-7.
Real files in tmp_path; the writer lock is exercised, not mocked. Recipients/chat text are
fictional (gtm_core.fictionalize shapes).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.cockpit_state import (
    NOTICE_CORRUPT_PENDING,
    NOTICE_EXPIRED_SESSION,
    NOTICE_FAILED_RESUME,
    CockpitState,
    PendingStore,
)

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _state(root: Path, now: datetime = NOW) -> CockpitState:
    return CockpitState(root, clock=lambda: now)


def _pending(root: Path, profiles: dict[int, str] | None = None) -> PendingStore:
    chat_profile = profiles or {}
    return PendingStore(root, chat_profile=lambda c: chat_profile.get(c, "example"))


PUBLISH = {
    "post": "Canary-POST-text zq81",
    "media_urls": ["https://example.test/a.png"],
    "hash": "h" * 64,
    "scheduled_at": None,
    "identity_used": [],
    "chat_id": 42,
    "profile": "example",
}
REPLY = {
    "body": "Canary-REPLY-text qq17",
    "thread_id": "t-1",
    "to": "pat@example.test",
    "hash": "r" * 64,
    "chat_id": 42,
    "profile": "example",
}


# ── F1-1 round trip ──────────────────────────────────────────────────────────


def test_f1_1_system_state_round_trips(tmp_path: Path) -> None:
    s = _state(tmp_path)
    s.set_binding(42, "acme")
    s.record_session(42, "acme", "sess-aaa", turns_added=3)
    s.begin_update(900)
    s.end_update(900)
    s.begin_update(901)  # still in flight
    boot = s.start_boot()
    s2 = _state(tmp_path)  # a brand new process object
    assert s2.binding(42) == "acme"
    entry = s2.session_for(42, "acme")
    assert entry is not None and entry.session_id == "sess-aaa" and entry.turns == 3
    assert s2.epoch == s2.epoch and s2.read_status == "ok"
    assert boot.epoch  # epoch persisted for the next process to see
    assert (
        json.loads((tmp_path / "_system" / "cockpit-state.json").read_text())["epoch"] == boot.epoch
    )


def test_f1_1_pending_round_trips_every_field(tmp_path: Path) -> None:
    p = _pending(tmp_path)
    p.publish["tok1"] = dict(PUBLISH)
    p.reply["tok2"] = dict(REPLY)
    p.gate_run_id[42] = "r-20261019-1200"
    p.awaiting_edit[42] = True
    p.record_presented("publish", "tok1", chat_id=42, message_id=777, profile="example", epoch="e1")
    p2 = _pending(tmp_path)
    p2.load_all()
    assert p2.publish["tok1"]["post"] == PUBLISH["post"]
    assert p2.publish["tok1"]["media_urls"] == PUBLISH["media_urls"]
    assert p2.reply["tok2"]["to"] == REPLY["to"]
    assert p2.gate_run_id[42] == "r-20261019-1200"
    assert p2.awaiting_edit[42] is True
    pres = p2.presented_for("publish", "tok1")
    assert pres is not None and pres["message_id"] == 777 and pres["epoch"] == "e1"


def test_pending_pop_and_delete_are_durable(tmp_path: Path) -> None:
    p = _pending(tmp_path)
    p.publish["tok1"] = dict(PUBLISH)
    assert p.publish.pop("tok1")["hash"] == PUBLISH["hash"]
    assert p.publish.pop("tok1", None) is None
    p2 = _pending(tmp_path)
    p2.load_all()
    assert "tok1" not in p2.publish


# ── F1-3 profile isolation ───────────────────────────────────────────────────


def test_f1_3_session_is_keyed_by_chat_and_profile(tmp_path: Path) -> None:
    s = _state(tmp_path)
    s.record_session(42, "alpha", "sess-A")
    assert s.session_for(42, "alpha").session_id == "sess-A"
    assert s.session_for(42, "beta") is None  # never resumed into another profile
    s.record_session(42, "beta", "sess-B")
    assert s.session_for(42, "alpha").session_id == "sess-A"
    assert s.session_for(42, "beta").session_id == "sess-B"


def test_f1_3_chat_keyed_pending_only_restores_for_the_bound_profile(tmp_path: Path) -> None:
    p = _pending(tmp_path, {42: "alpha"})
    p.awaiting_edit[42] = True
    p.gate_run_id[42] = "r-20261019-0900"
    later = _pending(tmp_path, {42: "beta"})  # chat re-bound to another profile since
    later.load_all()
    assert 42 not in later.awaiting_edit and 42 not in later.gate_run_id
    same = _pending(tmp_path, {42: "alpha"})
    same.load_all()
    assert same.awaiting_edit[42] is True


# ── F1-4 degradation, with notice ────────────────────────────────────────────

BAD_BYTES = [
    b"",
    b"{",
    b'{"version": 1, "bindings": []}',  # wrong type for a known key
    b"[]",
    b"\xff\xfe\x00bad",
]
# The pending store has its own known keys, so its wrong-type blob is different.
BAD_PENDING = [*BAD_BYTES[:2], b'{"version": 1, "publish": []}', *BAD_BYTES[3:]]


@pytest.mark.parametrize("blob", BAD_BYTES)
def test_f1_4_bad_system_state_degrades_to_empty_and_chat_works(
    tmp_path: Path, blob: bytes
) -> None:
    path = tmp_path / "_system" / "cockpit-state.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)
    s = _state(tmp_path)
    assert s.read_status == "corrupt"
    assert s.binding(42) is None and s.session_for(42, "acme") is None
    s.set_binding(42, "acme")  # the chat still works, and heals the file
    assert _state(tmp_path).binding(42) == "acme"


@pytest.mark.parametrize("blob", BAD_PENDING)
def test_f1_4_bad_pending_degrades_to_empty_with_the_exact_notice(
    tmp_path: Path, blob: bytes
) -> None:
    path = tmp_path / "example" / "_cockpit" / "pending.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(blob)
    p = _pending(tmp_path)
    p.load_all()
    assert dict(p.publish) == {} and dict(p.reply) == {}
    assert p.notices == [NOTICE_CORRUPT_PENDING]
    assert NOTICE_CORRUPT_PENDING == (
        "I couldn't read my saved approvals — anything waiting may need to be re-run; check the plan"
    )


def test_f1_4_missing_files_are_not_corrupt(tmp_path: Path) -> None:
    s = _state(tmp_path)
    assert s.read_status == "missing"
    p = _pending(tmp_path)
    p.load_all()
    assert p.notices == []


def test_f1_4_unknown_extra_key_accepted_but_wrong_type_rejected(tmp_path: Path) -> None:
    path = tmp_path / "_system" / "cockpit-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "bindings": {"42": "acme"}, "future_key": {"x": 1}}))
    s = _state(tmp_path)
    assert s.read_status == "ok" and s.binding(42) == "acme"
    path.write_text(json.dumps({"version": 1, "bindings": {"42": 7}}))  # int is not a profile name
    assert _state(tmp_path).read_status == "corrupt"  # rejected, not coerced
    path.write_text(json.dumps({"version": 1, "last_update_id": "12"}))
    assert _state(tmp_path).read_status == "corrupt"


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\x00b", "x" * 300, "", "..", "a b"])
def test_f1_4_hostile_session_id_refused(tmp_path: Path, bad: str) -> None:
    s = _state(tmp_path)
    s.record_session(42, "acme", bad)
    assert s.session_for(42, "acme") is None  # never stored, so never reaches a path


# ── F1-5 no content in _system ───────────────────────────────────────────────


def test_f1_5_system_state_never_holds_content(tmp_path: Path) -> None:
    s = _state(tmp_path)
    p = _pending(tmp_path)
    s.set_binding(42, "example")
    s.record_session(42, "example", "sess-1")
    p.publish["tok1"] = dict(PUBLISH)
    p.reply["tok2"] = dict(REPLY)
    p.record_presented("publish", "tok1", chat_id=42, message_id=1, profile="example", epoch="e")
    s.start_boot()
    raw = (tmp_path / "_system" / "cockpit-state.json").read_text()
    for canary in (
        "Canary-POST-text",
        "Canary-REPLY-text",
        "pat@example.test",
        "example.test/a.png",
    ):
        assert canary not in raw
    for f in (tmp_path / "_system").rglob("*"):
        if f.is_file():
            assert "Canary-" not in f.read_text(errors="ignore")
    assert "Canary-POST-text" in (tmp_path / "example" / "_cockpit" / "pending.json").read_text()


# ── F1-6 expiry ──────────────────────────────────────────────────────────────


def test_f1_6_resume_decision(tmp_path: Path) -> None:
    s = _state(tmp_path)
    s.record_session(42, "acme", "sess-1", turns_added=10)
    d = s.resume_decision(42, "acme", max_idle_days=7, max_turns=500)
    assert d.session_id == "sess-1" and d.notice is None
    assert (
        _state(tmp_path, NOW + timedelta(days=8)).resume_decision(42, "acme", 7, 500).notice
        == NOTICE_EXPIRED_SESSION
    )
    s.record_session(42, "acme", "sess-1", turns_added=495)
    over = s.resume_decision(42, "acme", 7, 500)
    assert over.session_id is None and over.notice == NOTICE_EXPIRED_SESSION
    assert NOTICE_EXPIRED_SESSION == (
        "fresh conversation — the old one was idle for more than 7 days or reached 500 turns"
    )
    assert NOTICE_FAILED_RESUME == "I started a fresh conversation — I couldn't restore the old one"


def test_f1_6_reset_deletes_the_entry_and_failed_resume_forgets_it(tmp_path: Path) -> None:
    s = _state(tmp_path)
    s.record_session(42, "acme", "sess-1")
    s.drop_session(42, "acme")  # /reset
    assert _state(tmp_path).session_for(42, "acme") is None
    assert _state(tmp_path).resume_decision(42, "acme", 7, 500).session_id is None


# ── F1-7 concurrent mutation ─────────────────────────────────────────────────


def test_f1_7_concurrent_writers_lose_nothing(tmp_path: Path) -> None:
    s = _state(tmp_path)
    p = _pending(tmp_path)
    errors: list[BaseException] = []

    def bind(i: int) -> None:
        try:
            s.set_binding(1000 + i, f"prof{i}")
            s.record_session(1000 + i, f"prof{i}", f"sess-{i}")
            entry = dict(PUBLISH, hash=f"{i:064d}", profile="example")
            p.publish[f"tok{i}"] = entry
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=bind, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    s2, p2 = _state(tmp_path), _pending(tmp_path)
    p2.load_all()
    for i in range(40):
        assert s2.binding(1000 + i) == f"prof{i}"
        assert s2.session_for(1000 + i, f"prof{i}").session_id == f"sess-{i}"
        assert f"tok{i}" in p2.publish


# ── boot, epoch, safe mode, poison, dedup (F2-4/F2-5 state half) ─────────────


def test_boot_epoch_changes_every_boot_and_is_short(tmp_path: Path) -> None:
    epochs = {_state(tmp_path).start_boot().epoch for _ in range(5)}
    assert len(epochs) == 5 and all(len(e) == 6 for e in epochs)


def test_f2_5_three_boots_in_ten_minutes_trip_safe_mode_then_clear(tmp_path: Path) -> None:
    flags = [_state(tmp_path, NOW + timedelta(minutes=m)).start_boot().safe_mode for m in (0, 4, 8)]
    assert flags == [False, False, True]
    # ten stable minutes later the window has emptied
    assert _state(tmp_path, NOW + timedelta(minutes=30)).start_boot().safe_mode is False


def test_f2_5_poison_update_is_skipped_next_boot_and_not_a_loop(tmp_path: Path) -> None:
    a = _state(tmp_path)
    a.start_boot()
    a.begin_update(555)  # the process dies here
    b = _state(tmp_path, NOW + timedelta(minutes=1))
    boot = b.start_boot()
    assert 555 in boot.poison_ids and b.should_skip(555)
    c = _state(tmp_path, NOW + timedelta(minutes=2))
    c.start_boot()  # progress: the poison id was cleared, nothing is stuck in flight
    assert 555 not in c.in_flight


def test_f2_4_dedup_and_out_of_order(tmp_path: Path) -> None:
    s = _state(tmp_path)
    for uid in (10, 12):
        assert not s.should_skip(uid)
        s.begin_update(uid)
        s.end_update(uid)
    assert s.should_skip(12) and s.should_skip(10)  # handled twice == handled once
    assert not s.should_skip(11)  # an out-of-order, never-handled id is still processed
    s2 = _state(tmp_path)
    assert s2.should_skip(12)  # survives a restart


# ── F1-8 fail-closed on restart: memory-only state is marked so the operator can be told ──


def test_f1_8_volatile_marks_survive_a_restart_and_are_taken_once(tmp_path: Path) -> None:
    s = _state(tmp_path)
    s.set_volatile("permission", 42, True)
    s.set_volatile("wizard", 42, True)
    s.set_volatile("wizard", 42, False)  # the wizard finished before the "crash"
    taken = _state(tmp_path).take_volatile()
    assert taken == {"permission": [42]}
    assert _state(tmp_path).take_volatile() == {}  # cleared: the operator is told exactly once


def test_f1_8_volatile_marks_are_identifiers_only(tmp_path: Path) -> None:
    s = _state(tmp_path)
    s.set_volatile("permission", 42, True)
    raw = json.loads((tmp_path / "_system" / "cockpit-state.json").read_text())
    assert raw["volatile"] == {"permission": [42]}
