"""Re-validate signal_observed freshness on existing prospect accounts.

Prevents stale news triggers (>210 days) on older rows from silently reaching
outreach during heat-rescore passes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from gtm_core.merge_hygiene import SIGNAL_MAX_AGE_DAYS
from gtm_core.prospect_paths import latest_json


@dataclass
class FreshnessResult:
    """Outcome of signal freshness audit."""

    fresh: list[dict[str, Any]] = field(default_factory=list)
    stale: list[dict[str, Any]] = field(default_factory=list)
    missing: list[dict[str, Any]] = field(default_factory=list)


def parse_date(date_str: str | None) -> date | None:
    """Safely parse an ISO YYYY-MM-DD string into a date."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        # Standard YYYY-MM-DD
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def audit_signal_freshness(
    items: list[dict[str, Any]],
    as_of: date | None = None,
    max_age_days: int = SIGNAL_MAX_AGE_DAYS,
    mutate: bool = False,
) -> FreshnessResult:
    """Audit signal freshness across rows, optionally downgrading stale rows to re-angle.

    Args:
        items: List of account dictionaries.
        as_of: Evaluation reference date (defaults to today).
        max_age_days: Threshold in days before a signal is deemed stale (default 210).
        mutate: If True, mutates stale items in-place (verdict -> re-angle).

    Returns:
        FreshnessResult categorizing each item.
    """
    ref_date = as_of or date.today()
    result = FreshnessResult()

    for item in items:
        obs_raw = item.get("signal_observed")
        obs_date = parse_date(obs_raw)

        if obs_date is None:
            result.missing.append(item)
            continue

        age_days = (ref_date - obs_date).days

        if age_days <= max_age_days:
            result.fresh.append(item)
        else:
            result.stale.append(item)
            if mutate:
                item["verdict"] = "re-angle"
                prev_reason = item.get("verdict_reason", "")
                stale_tag = f"signal_stale_{age_days}d"
                if prev_reason:
                    item["verdict_reason"] = f"{prev_reason}; {stale_tag}"
                else:
                    item["verdict_reason"] = stale_tag

    return result


def revalidate_profile_signals(
    profile: str,
    as_of: date | None = None,
    max_age_days: int = SIGNAL_MAX_AGE_DAYS,
    dry_run: bool = False,
    content_root: Path | None = None,
) -> FreshnessResult:
    """Audit and update latest.json for a profile based on signal freshness.

    Args:
        profile: Tenant profile name.
        as_of: Reference date.
        max_age_days: Age threshold.
        dry_run: If True, do not persist changes to disk.
        content_root: Optional content root override.

    Returns:
        FreshnessResult.
    """
    target = latest_json(profile, content_root)
    if not target.exists():
        return FreshnessResult()

    try:
        content = target.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("items", data.get("accounts", []))
        else:
            return FreshnessResult()
    except (json.JSONDecodeError, OSError, ValueError):
        return FreshnessResult()

    res = audit_signal_freshness(
        items,
        as_of=as_of,
        max_age_days=max_age_days,
        mutate=not dry_run,
    )

    if not dry_run and res.stale:
        # Save back atomically
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=target.parent,
            prefix="latest_",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as tf:
            tf.write(payload)
            tf.flush()
            os.fsync(tf.fileno())
            tmp_name = tf.name
        os.replace(tmp_name, target)

    return res


def main(argv: list[str] | None = None) -> int:
    """CLI for auditing and updating stale signals."""
    parser = argparse.ArgumentParser(
        prog="gtm_core.signal_freshness",
        description="Audit and revalidate signal freshness in latest.json.",
    )
    parser.add_argument("--profile", required=True, help="Tenant profile name")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=SIGNAL_MAX_AGE_DAYS,
        help=f"Max age in days (default: {SIGNAL_MAX_AGE_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without mutating latest.json",
    )
    args = parser.parse_args(argv)

    res = revalidate_profile_signals(
        profile=args.profile,
        max_age_days=args.max_age_days,
        dry_run=args.dry_run,
    )

    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode}Signal Freshness Audit for '{args.profile}':")
    print(f"  Fresh (<= {args.max_age_days}d): {len(res.fresh)}")
    print(f"  Stale (> {args.max_age_days}d):  {len(res.stale)}")
    print(f"  Missing / unparseable: {len(res.missing)}")

    if res.stale:
        print("\nStale Accounts:")
        for item in res.stale:
            c = item.get("company", "unknown")
            d = item.get("signal_observed", "unknown")
            print(f"  - {c} (observed: {d})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
