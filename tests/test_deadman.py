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
