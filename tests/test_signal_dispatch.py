"""Unit tests for agent.signal_dispatch (PRD 2026-08-27 §3.6, W4).

§R9 — every fixture is fictional; ``acme.example`` is a reserved name that can never
resolve to a real organisation.

The behaviours worth pinning are the ones that decide whether an unattended lane is safe
to leave running: that it drains from the ledger rather than a parallel queue, that both
ceilings **defer** instead of dropping, and that a signal can never name a destination.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent import signal_dispatch as sd


class _Cfg:
    """Minimal stand-in for agent.config.Config — the dispatcher reads two roots."""

    def __init__(self, root: Path):
        self.content_root = root
        self.profiles_root = root / "profiles"


class _Ledgers:
    """Captures appends and mirrors them into history.jsonl, as the real one does."""

    def __init__(self, path: Path):
        self.path = path
        self.rows: list[dict] = []

    def append_history(self, record: dict) -> dict:
        record = {"ts": "2026-08-28T09:00:00Z", **record}
        self.rows.append(record)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return record


def _history(tmp_path: Path, profile: str = "acme") -> Path:
    p = tmp_path / profile / "history.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.touch()
    return p


def _signal_row(sid: str, action: str = "draft_reply", *, ts: str = "2026-08-28T08:00:00Z") -> dict:
    return {
        "ts": ts,
        "event": "signal",
        "skill": "inbound-triage",
        "signal_type": "reply_received",
        "who": "rani@acme.example",
        "source": "saleshandy-inbox:t1",
        "suggested_action": action,
        "source_items": [sid],
    }


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# --- the ledger is the queue -----------------------------------------------


class TestUndispatched:
    def test_a_recorded_signal_is_pending(self, tmp_path):
        h = _history(tmp_path)
        _write(h, [_signal_row("sig_a")])
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_a"]

    def test_a_dispatched_signal_is_not_pending_again(self, tmp_path):
        h = _history(tmp_path)
        _write(
            h,
            [
                _signal_row("sig_a"),
                {"event": sd.DISPATCHED_EVENT, "dispatched": True, "source_items": ["sig_a"]},
            ],
        )
        assert sd.undispatched(h) == []

    def test_a_deferred_signal_is_still_pending(self, tmp_path):
        """Defer must not read as done, or a capped signal is silently dropped."""
        h = _history(tmp_path)
        _write(
            h,
            [
                _signal_row("sig_a"),
                {
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": False,
                    "note": "deferred: daily dispatch cap 10 reached",
                    "source_items": ["sig_a"],
                },
            ],
        )
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_a"]

    def test_no_history_file_is_empty_not_an_error(self, tmp_path):
        assert sd.undispatched(tmp_path / "nope.jsonl") == []

    def test_a_malformed_line_is_skipped(self, tmp_path):
        h = _history(tmp_path)
        h.write_text("{not json\n" + json.dumps(_signal_row("sig_a")) + "\n", encoding="utf-8")
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_a"]

    def test_non_signal_events_are_ignored(self, tmp_path):
        h = _history(tmp_path)
        _write(h, [{"event": "optout_detected", "source_items": ["x"]}, _signal_row("sig_a")])
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_a"]


class TestTheDailyCounter:
    def test_it_counts_only_todays_successful_dispatches(self, tmp_path):
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T09:00:00Z",
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["a"],
                },
                {
                    "ts": "2026-08-27T09:00:00Z",
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["b"],
                },
            ],
        )
        assert sd.dispatched_today(h, today="2026-08-28") == 1

    def test_a_deferral_does_not_consume_the_ceiling(self, tmp_path):
        """Otherwise one capped day permanently poisons the next."""
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T09:00:00Z",
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": False,
                    "source_items": ["a"],
                }
            ],
        )
        assert sd.dispatched_today(h, today="2026-08-28") == 0


class TestTheDailyCounterIsTimezoneAware:
    """SC14: the counter used to compute "today" in UTC always, so a UTC+8 profile's
    ceiling reset at 08:00 local time instead of local midnight. Asia/Singapore
    (UTC+8, no DST — chosen so the fixture never depends on which half of the year it
    runs in) is the profile PENDING.md's SC14 note names.
    """

    def test_a_row_from_the_utc_morning_counts_toward_the_sgt_evening_before(self, tmp_path):
        """2026-08-29T02:00Z is 2026-08-29T10:00 in Singapore -- SGT's own calendar day.
        Under raw UTC-prefix counting this already matched; the point is it must STILL
        match once the comparison goes through a timezone conversion.
        """
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-29T02:00:00Z",
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["a"],
                }
            ],
        )
        assert sd.dispatched_today(h, today="2026-08-29", tz="Asia/Singapore") == 1

    def test_the_utc_prefix_match_undercounts_across_the_sgt_midnight_boundary(self, tmp_path):
        """The defect itself. 2026-08-28T17:00Z is 2026-08-29T01:00 SGT -- SGT's NEXT
        calendar day, even though the UTC date is still the 28th. A raw prefix match
        against UTC "today" (the pre-fix code) would call this the 28th and miss it;
        scoped correctly in Asia/Singapore it belongs to the 29th.
        """
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T17:00:00Z",  # 2026-08-29 01:00 SGT
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["a"],
                }
            ],
        )
        assert sd.dispatched_today(h, today="2026-08-29", tz="Asia/Singapore") == 1
        assert sd.dispatched_today(h, today="2026-08-28", tz="Asia/Singapore") == 0

    def test_utc_default_reproduces_the_old_behaviour_exactly(self, tmp_path):
        """Positive control: a profile that never sets a timezone must count IDENTICALLY
        to before this fix. The same row that lands on SGT's 29th stays on the 28th in
        plain UTC scoping."""
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T17:00:00Z",
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["a"],
                }
            ],
        )
        assert sd.dispatched_today(h, today="2026-08-28") == 1
        assert sd.dispatched_today(h, today="2026-08-29") == 0

    @pytest.mark.parametrize("bad_tz", [None, 123, "", "Not/AZone", "../../etc/passwd"])
    def test_any_unusable_tz_falls_back_to_utc_rather_than_raising(self, tmp_path, bad_tz):
        """`dispatched_today` is public and `tz` is whatever a caller passed. A bad zone
        NAME raises ZoneInfoNotFoundError/ValueError; a bad zone TYPE raises TypeError --
        and `None` is the likely mistake, since `today` right beside it takes None to mean
        "default". All of them must degrade to UTC, never raise mid-dispatch."""
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T09:00:00Z",
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["a"],
                }
            ],
        )
        assert sd.dispatched_today(h, today="2026-08-28", tz=bad_tz) == 1

    def test_an_unrecognised_zone_falls_back_to_utc_rather_than_raising(self, tmp_path):
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T09:00:00Z",
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["a"],
                }
            ],
        )
        assert sd.dispatched_today(h, today="2026-08-28", tz="Not/AZone") == 1


class TestProfileTimezone:
    def test_it_defaults_to_utc_when_settings_are_absent(self, tmp_path):
        assert sd.profile_timezone(_Cfg(tmp_path), "acme") == "UTC"

    def test_it_reads_the_profile_setting(self, tmp_path):
        p = tmp_path / "acme"
        p.mkdir(parents=True)
        (p / "settings.json").write_text('{"timezone": "Asia/Singapore"}', encoding="utf-8")
        assert sd.profile_timezone(_Cfg(tmp_path), "acme") == "Asia/Singapore"

    def test_an_unrecognised_zone_name_falls_back_to_utc(self, tmp_path):
        """A typo in a tenant-writable file must degrade to "counts like before",
        never raise mid-dispatch and never silently land on the wrong zone."""
        p = tmp_path / "acme"
        p.mkdir(parents=True)
        (p / "settings.json").write_text('{"timezone": "Singapore/Asia"}', encoding="utf-8")
        assert sd.profile_timezone(_Cfg(tmp_path), "acme") == "UTC"

    def test_corrupt_settings_fall_back_to_utc(self, tmp_path):
        p = tmp_path / "acme"
        p.mkdir(parents=True)
        (p / "settings.json").write_text("{not json", encoding="utf-8")
        assert sd.profile_timezone(_Cfg(tmp_path), "acme") == "UTC"


class TestProfileTimezoneDegradesOnAnyBadShape:
    """The narrower exception tuple this replaced missed AttributeError from a
    non-dict top-level JSON value (`stored.get(...)` on a list/int/str/bool/None),
    crashing `dispatch()` before any pending signal was processed -- found by review,
    not by the shapes `daily_cap`'s own tests already covered."""

    @pytest.mark.parametrize("body", ["[1, 2, 3]", "42", '"just a string"', "true", "null"])
    def test_a_non_object_top_level_falls_back_to_utc(self, tmp_path, body):
        p = tmp_path / "acme"
        p.mkdir(parents=True)
        (p / "settings.json").write_text(body, encoding="utf-8")
        assert sd.profile_timezone(_Cfg(tmp_path), "acme") == "UTC"

    def test_a_non_object_settings_file_does_not_crash_dispatch(self, wired):
        """End-to-end: the exact crash the review reproduced -- a pending signal with
        settings.json = `[1, 2, 3]` used to raise AttributeError out of dispatch()
        before any signal was recorded."""
        cfg, h, ledgers, ran, _ = wired
        (cfg.content_root / "acme" / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")
        _write(h, [_signal_row("sig_a")])
        assert _dispatch(cfg) == 0
        assert ran == [("inbound", "inbound-reply")]


class TestDispatchedTodayTreatsANaiveTimestampAsUtc:
    """`datetime.astimezone()` on a naive value silently assumes it is already in the
    HOST MACHINE's local time before converting -- so without an explicit UTC stamp, this
    function's answer would depend on which machine ran it. Every real writer
    (`gtm_core.ledgers._utc_now_iso`) always appends Z; this guards a hand-edited or
    third-party-written row.
    """

    def test_a_naive_ts_is_scoped_as_utc_not_host_local_time(self, tmp_path):
        h = _history(tmp_path)
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T23:30:00",  # no Z, no offset
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["a"],
                }
            ],
        )
        # Scoped in Singapore (UTC+8): if this Z-less ts is read as UTC, 23:30 UTC on
        # the 28th is 07:30 SGT on the 29th. If it were instead read as host-local time
        # (the bug) it would land on whatever date the TEST MACHINE's own zone produces
        # for 23:30 -- nondeterministic across machines, which is the property under
        # test: the UTC-scoped answer must be independent of the host's zone.
        assert sd.dispatched_today(h, today="2026-08-29", tz="Asia/Singapore") == 1
        assert sd.dispatched_today(h, today="2026-08-28", tz="Asia/Singapore") == 0


class TestDispatchUsesTheProfileTimezone:
    def test_a_row_the_utc_scoping_would_miss_still_counts_against_the_cap(self, wired):
        """End-to-end, using the same `wired` fixture `test_over_the_daily_cap_a_signal_defers`
        does. A prior dispatch already recorded at 17:00 UTC — 01:00 SGT the NEXT calendar
        day — plus a fresh signal, cap 1, "today" the 29th. Correctly scoped to Singapore
        the prior row IS the 29th's dispatch, so the fresh signal must defer. Under the
        pre-fix UTC scoping the prior row's date read as "the 28th" and would have been
        invisible to a "today" of the 29th, letting the cap silently overrun by one.
        """
        cfg, h, ledgers, ran, _ = wired
        (cfg.content_root / "acme" / "settings.json").write_text(
            '{"signal_dispatch_daily_cap": 1, "timezone": "Asia/Singapore"}', encoding="utf-8"
        )
        _write(
            h,
            [
                {
                    "ts": "2026-08-28T17:00:00Z",  # 2026-08-29 01:00 SGT
                    "event": sd.DISPATCHED_EVENT,
                    "dispatched": True,
                    "source_items": ["already-sent"],
                },
                _signal_row("pending-one"),
            ],
        )
        asyncio.run(sd.dispatch("acme", cfg=cfg, today="2026-08-29"))
        assert ran == [], "the SGT-scoped cap was already spent; nothing should run"
        deferred = [r for r in ledgers.rows if r["source_items"] == ["pending-one"]]
        assert deferred, "the pending signal should have deferred against the SGT-scoped cap"
        assert deferred[0]["dispatched"] is False
        assert "daily dispatch cap" in deferred[0]["note"]


class TestTheDailyCap:
    def test_it_defaults_when_settings_are_absent(self, tmp_path):
        assert sd.daily_cap(_Cfg(tmp_path), "acme") == sd.DEFAULT_DAILY_CAP

    def test_it_reads_the_profile_setting(self, tmp_path):
        p = tmp_path / "acme"
        p.mkdir(parents=True)
        (p / "settings.json").write_text('{"signal_dispatch_daily_cap": 3}', encoding="utf-8")
        assert sd.daily_cap(_Cfg(tmp_path), "acme") == 3

    def test_corrupt_settings_fall_back_to_the_default(self, tmp_path):
        """Never to unlimited — the same posture as monthly_cap_usd."""
        p = tmp_path / "acme"
        p.mkdir(parents=True)
        (p / "settings.json").write_text("{not json", encoding="utf-8")
        assert sd.daily_cap(_Cfg(tmp_path), "acme") == sd.DEFAULT_DAILY_CAP


# --- dispatch behaviour ----------------------------------------------------


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A dispatcher whose pack runs and notifications are recorded, not performed."""
    h = _history(tmp_path)
    ledgers = _Ledgers(h)
    ran: list[tuple[str, str]] = []
    notified: list[dict] = []

    async def fake_pack(cfg, profile, target, signal):
        ran.append((target.pack, target.variant))

    async def fake_notify(cfg, profile, signal):
        notified.append(signal)

    monkeypatch.setattr(sd, "Ledgers", lambda cfg, profile: ledgers)
    monkeypatch.setattr(sd, "vps_budget_ok", lambda cfg, profile: True)
    monkeypatch.setattr(sd, "_run_pack_for", fake_pack)
    monkeypatch.setattr(
        sd,
        "ACTION_DISPATCH",
        {**sd.ACTION_DISPATCH, "escalate_to_operator": sd.NotifyTarget(fake_notify)},
    )
    return _Cfg(tmp_path), h, ledgers, ran, notified


def _dispatch(cfg, **kw):
    """Sync wrapper — this repo drives async entry points with asyncio.run in tests
    (see tests/test_calendly_poll.py) rather than taking a pytest-asyncio dependency."""
    return asyncio.run(sd.dispatch("acme", cfg=cfg, today="2026-08-28", **kw))


class TestDispatch:
    def test_a_draft_reply_signal_runs_the_inbound_pack(self, wired):
        cfg, h, ledgers, ran, _ = wired
        _write(h, [_signal_row("sig_a")])
        assert _dispatch(cfg) == 0
        assert ran == [("inbound", "inbound-reply")]
        assert [r["dispatched"] for r in ledgers.rows] == [True]

    def test_an_escalation_notifies_and_runs_no_pack(self, wired):
        cfg, h, ledgers, ran, notified = wired
        _write(h, [_signal_row("sig_a", "escalate_to_operator")])
        _dispatch(cfg)
        assert ran == []
        assert len(notified) == 1

    def test_a_review_signal_is_recorded_and_not_acted_on(self, wired):
        cfg, h, ledgers, ran, notified = wired
        _write(h, [_signal_row("sig_a", "review")])
        _dispatch(cfg)
        assert ran == [] and notified == []
        assert ledgers.rows[0]["dispatched"] is False

    def test_dispatching_twice_runs_the_pack_once(self, wired):
        """Idempotence is what makes an hourly unit safe."""
        cfg, h, ledgers, ran, _ = wired
        _write(h, [_signal_row("sig_a")])
        _dispatch(cfg)
        _dispatch(cfg)
        assert ran == [("inbound", "inbound-reply")]

    def test_a_failing_dispatch_does_not_strand_the_rest(self, wired, monkeypatch):
        cfg, h, ledgers, ran, _ = wired
        calls = {"n": 0}

        async def flaky(cfg_, profile, target, signal):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            ran.append((target.pack, target.variant))

        monkeypatch.setattr(sd, "_run_pack_for", flaky)
        _write(h, [_signal_row("sig_a"), _signal_row("sig_b")])
        _dispatch(cfg)
        assert ran == [("inbound", "inbound-reply")]
        assert [r["dispatched"] for r in ledgers.rows] == [False, True]

    def test_a_failed_dispatch_is_retried_next_run(self, wired, monkeypatch):
        """A crash costs a retry, never a lost reply."""
        cfg, h, ledgers, ran, _ = wired

        async def boom(cfg_, profile, target, signal):
            raise RuntimeError("boom")

        monkeypatch.setattr(sd, "_run_pack_for", boom)
        _write(h, [_signal_row("sig_a")])
        _dispatch(cfg)
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_a"]


class TestTheCeilingsDeferRatherThanDrop:
    def test_over_the_daily_cap_a_signal_defers(self, wired):
        cfg, h, ledgers, ran, _ = wired
        (cfg.content_root / "acme" / "settings.json").write_text(
            '{"signal_dispatch_daily_cap": 1}', encoding="utf-8"
        )
        _write(h, [_signal_row("sig_a"), _signal_row("sig_b")])
        _dispatch(cfg)
        assert len(ran) == 1
        deferred = [r for r in ledgers.rows if not r["dispatched"]]
        assert len(deferred) == 1
        assert "daily dispatch cap" in deferred[0]["note"]
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_b"]

    def test_over_the_monthly_budget_nothing_dispatches(self, wired, monkeypatch):
        """§R2 runs before every batch — W4 makes run count signal-driven, so a
        once-per-run check would be the wrong shape."""
        cfg, h, ledgers, ran, _ = wired
        monkeypatch.setattr(sd, "vps_budget_ok", lambda cfg_, profile: False)
        _write(h, [_signal_row("sig_a")])
        _dispatch(cfg)
        assert ran == []
        assert "monthly cost cap" in ledgers.rows[0]["note"]
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_a"]


class TestDryRun:
    def test_it_spends_nothing_and_runs_nothing(self, wired, capsys):
        cfg, h, ledgers, ran, notified = wired
        _write(h, [_signal_row("sig_a")])
        assert _dispatch(cfg, dry_run=True) == 0
        assert ran == [] and notified == [] and ledgers.rows == []
        assert "sig_a" in capsys.readouterr().out

    def test_a_dry_run_leaves_the_signal_pending(self, wired):
        cfg, h, _, _, _ = wired
        _write(h, [_signal_row("sig_a")])
        _dispatch(cfg, dry_run=True)
        assert [s["id"] for s in sd.undispatched(h)] == ["sig_a"]


class TestNoDestinationIsRepresentable:
    def test_a_pack_target_carries_no_destination_field(self):
        assert set(sd.PackTarget.__dataclass_fields__) == {"pack", "variant"}

    def test_signal_meta_cannot_reach_the_target(self):
        """A signal is untrusted data. It selects an action; it never builds a target."""
        for target in sd.ACTION_DISPATCH.values():
            if isinstance(target, sd.PackTarget):
                assert target.pack and target.variant  # literals, fixed at import
