"""Deterministic PII data retention sweeper for prospecting state.

Enforces data retention policies by archiving and purging temporary cleartext
prospect CSVs (e.g. prospects-*-hubspot.csv, ready-to-load.csv) after a configurable
TTL (default 7 days). Closes SECURITY-SELF-ASSESSMENT §D-04 and EU AI Act Art. 12 residual gap.

**Unenrolled-file guard (2026-09-19).** ``ready-to-load*.csv`` files in the sequences
directory are the operator's staging area — they contain contacts that have been
enriched, scored, and routed but may not yet have been loaded into the email sequencer.
Archiving one of these before enrollment means the next ``email-sequence`` call finds
the file missing and must re-route from the ledger: extra work, operator confusion, and
(if the ledger was edited in the meantime) potentially different routing. The sweep now
**refuses to archive any ``ready-to-load*.csv``** unless the caller passes ``force=True``
(CLI ``--force``), and reports them separately as ``guarded`` so the operator can see
exactly which files were held back and why.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from gtm_core.prospect_paths import (
    accounts_dir,
    archive_dir,
    latest_json,
    prospects_dir,
    sequences_dir,
)
from gtm_core.prospects_state import RETIRED_STATUSES, _atomic_write, load_latest, snapshot

PROTECTED_NAMES: frozenset[str] = frozenset(
    {
        "latest.json",
        "master-list.csv",
        "suppression.csv",
        "needs-verification.csv",
        "outcomes.jsonl",
        ".run_lock",
        ".run_lock.json",
        "run_state.json",
        "run_summary.json",
    }
)


@dataclass
class SweepResult:
    """Outcome of a PII retention sweep."""

    archived: list[Path] = field(default_factory=list)
    guarded: list[tuple[Path, str]] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    archived_dossiers: list[Path] = field(default_factory=list)
    purged_accounts: list[str] = field(default_factory=list)


def _is_target_pii_file(path: Path) -> bool:
    """Determine if a file is a temporary cleartext prospect CSV."""
    name = path.name
    if name in PROTECTED_NAMES:
        return False
    if not name.endswith(".csv"):
        return False
    # Target patterns
    if name.startswith("prospects-") and name.endswith("-hubspot.csv"):
        return True
    if name == "ready-to-load.csv":
        return True
    if name.startswith("prospects-") and not name.startswith("prospects-outreach"):
        return True
    return False


def _is_unenrolled_load_file(path: Path) -> bool:
    """A ready-to-load file that may not have been enrolled in the sequencer yet.

    These files are the operator's staging area: enriched, scored, routed contacts
    waiting to be loaded into the email sequencer. Archiving them before enrollment
    means the operator loses a file they expected to find, and re-routing from the
    ledger may produce different results if the ledger was edited since the route.
    """
    return path.name.startswith("ready-to-load")


def _sweep_account_dossiers(
    profile: str,
    content_root: Path | None,
    arc_dir: Path,
    dossier_ttl_days: int,
    now: float,
    dry_run: bool,
    result: SweepResult,
) -> None:
    acc_dir = accounts_dir(profile, content_root)
    if not acc_dir.exists():
        return

    dossier_ttl_seconds = dossier_ttl_days * 86400.0
    for account_folder in acc_dir.iterdir():
        if not account_folder.is_dir():
            continue

        for file_path in account_folder.iterdir():
            if not file_path.is_file():
                continue

            try:
                mtime = file_path.stat().st_mtime
                age_s = now - mtime
                if age_s < dossier_ttl_seconds:
                    result.skipped.append(
                        (
                            file_path,
                            f"fresh dossier (age={age_s / 86400:.1f}d < {dossier_ttl_days}d)",
                        )
                    )
                    continue

                if dry_run:
                    result.archived_dossiers.append(file_path)
                    continue

                arc_accounts = arc_dir / "accounts" / account_folder.name
                arc_accounts.mkdir(parents=True, exist_ok=True)
                timestamp_str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                dest_name = f"{file_path.stem}-{timestamp_str}{file_path.suffix}.gz"
                dest_path = arc_accounts / dest_name

                with file_path.open("rb") as f_in, gzip.open(dest_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)

                file_path.unlink()
                result.archived_dossiers.append(dest_path)

            except OSError as err:
                result.errors.append((file_path, str(err)))

        if not dry_run and account_folder.exists() and not any(account_folder.iterdir()):
            try:
                account_folder.rmdir()
            except OSError as err:
                result.errors.append((account_folder, str(err)))


def _extract_record_date(item: dict[str, Any]) -> date | None:
    for field_name in ("last_touched", "source_run_date", "signal_observed", "added_at"):
        val = item.get(field_name)
        if val:
            m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", str(val))
            if m:
                try:
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    pass
    return None


def _sweep_latest_json(
    profile: str,
    content_root: Path | None,
    dossier_ttl_days: int,
    now: float,
    dry_run: bool,
    result: SweepResult,
) -> None:
    l_path = latest_json(profile, content_root)
    if not l_path.exists():
        return

    try:
        current_data = load_latest(profile, content_root)
        items = current_data.get("items", [])
        if not items:
            return

        today = datetime.now(UTC).date()
        file_age_days = int((now - l_path.stat().st_mtime) / 86400)

        kept_items: list[dict[str, Any]] = []
        purged_names: list[str] = []

        for item in items:
            item_date = _extract_record_date(item)
            if item_date:
                age_days = (today - item_date).days
            else:
                age_days = file_age_days

            status = str(item.get("status") or "").lower()
            is_disqualified = status in RETIRED_STATUSES
            is_stale_age = age_days >= dossier_ttl_days

            is_active_relationship = status in (
                "customer",
                "partner",
                "meeting",
                "engaged",
                "in-conversation",
                "replied",
            )
            should_purge = (is_disqualified and is_stale_age) or (
                not is_active_relationship and is_stale_age
            )

            if should_purge:
                name = str(item.get("company") or item.get("id") or "unknown")
                purged_names.append(name)
            else:
                kept_items.append(item)

        result.purged_accounts.extend(purged_names)

        if purged_names and not dry_run:
            snapshot(profile, content_root)
            current_data["items"] = kept_items
            _atomic_write(l_path, current_data)

    except (OSError, json.JSONDecodeError) as err:
        result.errors.append((l_path, str(err)))


def sweep_stale_pii(
    profile: str,
    ttl_days: int = 7,
    dossier_ttl_days: int = 60,
    dry_run: bool = False,
    force: bool = False,
    content_root: Path | None = None,
) -> SweepResult:
    """Archive and purge cleartext prospect CSVs, aged dossiers, and stale ledger records.

    Args:
        profile: Tenant profile name.
        ttl_days: Retention threshold in days for temporary CSVs (default 7).
        dossier_ttl_days: Retention threshold in days for account dossiers and
            retired ledger records (default 60).
        dry_run: If True, discover targets without modifying files.
        force: If True, also archive unenrolled ready-to-load files.
            Without this flag, ready-to-load*.csv files are guarded
            and reported separately.
        content_root: Optional content root override.

    Returns:
        SweepResult detailing archived, guarded, skipped, errors, dossiers, and purged accounts.
    """
    result = SweepResult()
    p_dir = prospects_dir(profile, content_root)
    arc_dir = archive_dir(profile, content_root)
    now = time.time()

    if p_dir.exists():
        ttl_seconds = ttl_days * 86400.0

        candidates: list[Path] = []
        for item in p_dir.glob("*.csv"):
            if item.is_file():
                candidates.append(item)

        seq_dir = sequences_dir(profile, content_root)
        if seq_dir.exists():
            for item in seq_dir.glob("*.csv"):
                if item.is_file():
                    candidates.append(item)

        for path in candidates:
            if not _is_target_pii_file(path):
                result.skipped.append((path, "protected or non-target"))
                continue

            try:
                mtime = path.stat().st_mtime
                age_s = now - mtime
                if age_s < ttl_seconds:
                    result.skipped.append((path, f"fresh (age={age_s / 86400:.1f}d < {ttl_days}d)"))
                    continue

                if _is_unenrolled_load_file(path) and not force:
                    age_d = age_s / 86400
                    result.guarded.append(
                        (
                            path,
                            f"unenrolled load file (age={age_d:.0f}d) — "
                            f"pass --force to archive, or load into the sequencer first",
                        )
                    )
                    continue

                if dry_run:
                    result.archived.append(path)
                    continue

                arc_dir.mkdir(parents=True, exist_ok=True)
                timestamp_str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
                dest_name = f"{path.stem}-{timestamp_str}.csv.gz"
                dest_path = arc_dir / dest_name

                with path.open("rb") as f_in:
                    with gzip.open(dest_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)

                path.unlink()
                result.archived.append(dest_path)

            except OSError as err:
                result.errors.append((path, str(err)))

    # 60-day account dossier sweep
    _sweep_account_dossiers(
        profile=profile,
        content_root=content_root,
        arc_dir=arc_dir,
        dossier_ttl_days=dossier_ttl_days,
        now=now,
        dry_run=dry_run,
        result=result,
    )

    # 60-day latest.json purge
    _sweep_latest_json(
        profile=profile,
        content_root=content_root,
        dossier_ttl_days=dossier_ttl_days,
        now=now,
        dry_run=dry_run,
        result=result,
    )

    return result


def main(argv: list[str] | None = None) -> int:
    """CLI for purging/archiving stale PII prospect CSVs, dossiers, and records."""
    parser = argparse.ArgumentParser(
        prog="gtm_core.retention_sweep",
        description="Archive and purge stale prospect CSVs, account dossiers, and ledger records past TTL.",
    )
    parser.add_argument("--profile", required=True, help="Tenant profile name")
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=7,
        help="Retention TTL in days for temporary CSVs (default: 7)",
    )
    parser.add_argument(
        "--dossier-ttl-days",
        type=int,
        default=60,
        help="Retention TTL for account dossiers and retired records in days (default: 60)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate sweep without archiving or deleting files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Also archive unenrolled ready-to-load files (default: guard them)",
    )
    args = parser.parse_args(argv)

    res = sweep_stale_pii(
        profile=args.profile,
        ttl_days=args.ttl_days,
        dossier_ttl_days=args.dossier_ttl_days,
        dry_run=args.dry_run,
        force=args.force,
    )

    mode_label = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode_label}Retention sweep for profile '{args.profile}':")
    print(f"  Archived / Purged CSVs: {len(res.archived)} file(s)")
    for p in res.archived:
        print(f"    - {p.name}")
    if res.archived_dossiers:
        print(f"  Archived Dossiers: {len(res.archived_dossiers)} file(s)")
        for p in res.archived_dossiers:
            print(f"    - {p.name}")
    if res.purged_accounts:
        print(f"  Purged Ledger Records: {len(res.purged_accounts)} account(s)")
        for a in res.purged_accounts:
            print(f"    - {a}")
    if res.guarded:
        print(f"  Guarded (unenrolled — use --force to override): {len(res.guarded)} file(s)")
        for p, reason in res.guarded:
            print(f"    - {p.name}: {reason}")
    print(f"  Skipped (fresh/protected): {len(res.skipped)} file(s)")
    if res.errors:
        print(f"  Errors: {len(res.errors)}")
        for p, err in res.errors:
            print(f"    - {p}: {err}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
