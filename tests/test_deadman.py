"""Tests for gtm_core.deadman — the pipeline-silence dead-man's-switch."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.deadman import check, newest_event_ts


def _write_history(path: Path, timestamps: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"event": "x", "ts": ts}) for ts in timestamps) + "\n")


NOW = datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC)


def test_newest_event_ts_picks_max_not_last_line(tmp_path: Path) -> None:
    # Newest is not the last physical line — must compare, not just tail.
    h = tmp_path / "p" / "history.jsonl"
    _write_history(h, ["2026-06-26T09:00:00Z", "2026-06-20T09:00:00Z"])
    assert newest_event_ts(h) == datetime(2026, 6, 26, 9, 0, 0, tzinfo=UTC)


def test_fresh_profile_not_stale(tmp_path: Path) -> None:
    _write_history(tmp_path / "example2" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    report = check(tmp_path, now=NOW, max_age_hours=192)
    assert report == [
        {
            "profile": "example2",
            "newest_ts": "2026-06-26T09:00:00Z",
            "age_hours": 3.0,
            "stale": False,
        }
    ]


def test_stale_profile_tripped(tmp_path: Path) -> None:
    _write_history(tmp_path / "example" / "history.jsonl", ["2026-06-18T09:00:00Z"])
    [r] = check(tmp_path, now=NOW, max_age_hours=48)
    assert r["stale"] is True


def test_weekly_cadence_not_false_positive_at_192h(tmp_path: Path) -> None:
    # A healthy weekly cadence (last event 7 days ago) must NOT trip at 8 days.
    _write_history(tmp_path / "example" / "history.jsonl", ["2026-06-19T12:00:00Z"])
    [r] = check(tmp_path, now=NOW, max_age_hours=192)
    assert r["stale"] is False


def test_empty_history_is_stale(tmp_path: Path) -> None:
    # A pipeline that never produced an event is the silent-start failure to catch.
    (tmp_path / "ghost").mkdir()
    (tmp_path / "ghost" / "history.jsonl").write_text("")
    [r] = check(tmp_path, now=NOW, max_age_hours=192)
    assert r["newest_ts"] is None and r["stale"] is True


def test_only_profiles_filter(tmp_path: Path) -> None:
    _write_history(tmp_path / "a" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    _write_history(tmp_path / "b" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    report = check(tmp_path, now=NOW, max_age_hours=192, only_profiles=["a"])
    assert [r["profile"] for r in report] == ["a"]


def test_reserved_and_hidden_dirs_ignored(tmp_path: Path) -> None:
    _write_history(tmp_path / "_system" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    _write_history(tmp_path / ".staging" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    _write_history(tmp_path / "_smoke" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    _write_history(tmp_path / "real_profile" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    report = check(tmp_path, now=NOW, max_age_hours=192)
    assert [r["profile"] for r in report] == ["real_profile"]


def test_exclude_profiles_filter(tmp_path: Path) -> None:
    _write_history(tmp_path / "a" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    _write_history(tmp_path / "b" / "history.jsonl", ["2026-06-26T09:00:00Z"])
    report = check(tmp_path, now=NOW, max_age_hours=192, exclude_profiles=["b"])
    assert [r["profile"] for r in report] == ["a"]


# ── F4 (always-on hardening): unresolved dispatch intents older than an hour ────────────────────────────


def _intent_store(root: Path, profile: str = "example"):
    from types import SimpleNamespace

    from gtm_core.dispatch_intents import FileIntentStore
    from gtm_core.ledgers import Ledgers

    return FileIntentStore(Ledgers(SimpleNamespace(content_root=root), profile))


def _age_intents(root: Path, profile: str, hours: float) -> None:
    """Rewrite every row's ts to ``hours`` before NOW (a test-only time machine; the real ledger is append-only)."""
    from datetime import timedelta

    path = root / profile / "dispatch-intents.jsonl"
    ts = (NOW - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("\n".join(json.dumps({**r, "ts": ts}) for r in rows) + "\n")


def test_an_unresolved_intent_older_than_an_hour_is_reported(tmp_path: Path) -> None:
    import asyncio

    from gtm_core.deadman import unresolved_intents

    store = _intent_store(tmp_path)
    c = asyncio.run(store.claim("seq-1", ["a@acme.example", "b@acme.example"], tool="import"))
    asyncio.run(store.record_outcome(c.batch_id, "unknown"))
    _age_intents(tmp_path, "example", 3.0)
    [r] = unresolved_intents(tmp_path, now=NOW, max_age_hours=1.0)
    assert r["profile"] == "example" and r["batch_id"] == c.batch_id and r["recipients"] == 2
    assert r["age_hours"] == 3.0


def test_a_recent_or_resolved_intent_is_not_reported(tmp_path: Path) -> None:
    import asyncio

    from gtm_core.deadman import unresolved_intents

    store = _intent_store(tmp_path)
    c = asyncio.run(store.claim("seq-1", ["a@acme.example"], tool="import"))
    asyncio.run(store.record_outcome(c.batch_id, "unknown"))
    _age_intents(tmp_path, "example", 0.2)  # 12 minutes: a human has not had long enough
    assert unresolved_intents(tmp_path, now=NOW, max_age_hours=1.0) == []
    _age_intents(tmp_path, "example", 5.0)
    asyncio.run(store.resolve(c.batch_id, "not_sent"))
    assert unresolved_intents(tmp_path, now=NOW, max_age_hours=1.0) == []


def test_an_unreadable_intent_ledger_is_itself_reported_never_skipped(tmp_path: Path) -> None:
    from gtm_core.deadman import unresolved_intents

    (tmp_path / "example").mkdir()
    (tmp_path / "example" / "dispatch-intents.jsonl").write_text("{broken\n")
    [r] = unresolved_intents(tmp_path, now=NOW, max_age_hours=1.0)
    assert r["profile"] == "example" and r["unreadable"] is True


def test_main_trips_on_a_stale_intent_even_when_the_pipeline_is_fresh(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import asyncio

    from gtm_core import deadman

    _write_history(tmp_path / "example" / "history.jsonl", [NOW.isoformat().replace("+00:00", "Z")])
    store = _intent_store(tmp_path)
    c = asyncio.run(store.claim("seq-1", ["a@acme.example"], tool="import"))
    asyncio.run(store.record_outcome(c.batch_id, "unknown"))
    _age_intents(tmp_path, "example", 4.0)
    monkeypatch.setenv("GTM_CONTENT_ROOT", str(tmp_path))

    class _FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(deadman, "datetime", _FixedClock)
    assert deadman.main(["--max-age-hours", "192"]) == 1
    err = capsys.readouterr().err
    assert "unresolved" in err and c.batch_id in err
