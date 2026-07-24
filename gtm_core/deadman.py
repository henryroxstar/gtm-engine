"""Dead-man's-switch — alert when a profile's pipeline has gone silent.

Per-failure alerting (``notify.sh`` on a non-zero exit) cannot catch the two
worst cases: a timer that never fired, or an alert path that is itself broken.
Both happened on 2026-06-26 — every pipeline run died at ``ExecStartPre`` (Doppler
``$HOME``) AND ``notify.sh`` had no tokens, so 8 days of failures were silent.

This heartbeat reads the existing per-profile ``history.jsonl`` (no new state
file) and asserts each profile produced *some* audited event within a freshness
window. If any profile is stale it exits non-zero; the ``gtm-deadman.service``
unit's ``ExecStopPost`` then fires the Telegram ping via the same ``notify.sh``
path — which, being Doppler-wrapped, actually has its tokens.

Threshold note: the default window is 8 days, not 48h. The content pipeline fires
daily but only writes a ``history.jsonl`` event on its (weekly) cron day, so a
tighter window would false-positive on a perfectly healthy weekly cadence. 8 days
still catches the multi-day silent-outage class this guard exists for. Override
per profile cadence with ``--max-age-hours``.

Pure stdlib. Never calls ``datetime.now()`` at import time.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.paths import resolve_content_root

DEFAULT_MAX_AGE_HOURS = 192  # 8 days — see module docstring (weekly cadence).


def _parse_ts(raw: str) -> datetime | None:
    """Parse an ISO-8601 ``ts`` (trailing ``Z`` allowed) into an aware datetime."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def newest_event_ts(history_path: Path) -> datetime | None:
    """Return the newest ``ts`` across all events in a ``history.jsonl`` file."""
    newest: datetime | None = None
    try:
        text = history_path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(str(record.get("ts", "")))
        if ts and (newest is None or ts > newest):
            newest = ts
    return newest


def check(
    content_root: Path,
    *,
    now: datetime,
    max_age_hours: float,
    only_profiles: list[str] | None = None,
) -> list[dict]:
    """Return a per-profile staleness report.

    Each entry: ``{profile, newest_ts, age_hours, stale}``. A profile with no
    parseable history (``newest_ts is None``) is reported stale — a pipeline that
    has *never* produced an event is exactly the silent-start failure to catch.
    """
    report: list[dict] = []
    for history_path in sorted(content_root.glob("*/history.jsonl")):
        profile = history_path.parent.name
        if only_profiles and profile not in only_profiles:
            continue
        newest = newest_event_ts(history_path)
        if newest is None:
            report.append({"profile": profile, "newest_ts": None, "age_hours": None, "stale": True})
            continue
        age_hours = (now - newest).total_seconds() / 3600.0
        report.append(
            {
                "profile": profile,
                "newest_ts": newest.isoformat().replace("+00:00", "Z"),
                "age_hours": round(age_hours, 1),
                "stale": age_hours > max_age_hours,
            }
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gtm_core.deadman",
        description="Alert if any profile's history.jsonl has gone stale.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=f"Staleness threshold in hours (default: {DEFAULT_MAX_AGE_HOURS}).",
    )
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Limit to this profile (repeatable). Default: all profiles found.",
    )
    args = parser.parse_args(argv)

    content_root = resolve_content_root()
    now = datetime.now(UTC)
    report = check(
        content_root,
        now=now,
        max_age_hours=args.max_age_hours,
        only_profiles=args.profiles,
    )

    if not report:
        print(f"[deadman] no history.jsonl found under {content_root}; nothing to check.")
        # No profiles to monitor is itself suspicious, but not a pipeline outage —
        # treat as healthy so a fresh box doesn't spuriously alert.
        return 0

    stale = [r for r in report if r["stale"]]
    for r in report:
        flag = "STALE" if r["stale"] else "ok"
        ts = r["newest_ts"] or "<none>"
        age = "n/a" if r["age_hours"] is None else f"{r['age_hours']}h"
        print(f"[deadman] {flag:5} {r['profile']:12} newest={ts} age={age}")

    if stale:
        names = ", ".join(r["profile"] for r in stale)
        print(
            f"[deadman] DEAD-MAN TRIPPED: {len(stale)} profile(s) stale "
            f"(> {args.max_age_hours}h): {names}",
            file=sys.stderr,
        )
        return 1

    print(f"[deadman] all {len(report)} profile(s) fresh (<= {args.max_age_hours}h).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
