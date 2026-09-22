"""Manual, report-first PII retention sweep for prospecting state.

Archives aged cleartext prospect exports and account files, and — only when asked — purges
aged rows from the account ledger. **Nothing calls this module.** It is an operator's command:

    python -m gtm_core.retention_sweep --profile P              # a PLAN: prints, writes nothing
    python -m gtm_core.retention_sweep --profile P --apply      # acts, if every campaign is over

The policy is the product owner's decision (2026-09-21); each rule is the shape of a way the
first version of this sweep would have destroyed something:

* **Never automatic.** It was wired into the tail of the routine ``consolidate`` — unconditional,
  silent, result discarded. A build step must not delete, so the call is gone and the default
  here is a dry run: reaching a deletion takes an explicit ``--apply``.
* **Never while a campaign is unfinished.** ``--apply`` is refused unless every campaign
  manifest says, in so many words, that it is finished — ``draft``, paused, blank and unknown
  all block, as does campaign work with no manifest (:mod:`gtm_core.retention_campaign_gate`).
  There is deliberately no override flag (docs/RULES.md §R13) — closing the campaign IS it.
* **An exclusion row is never purged.** A ledger row whose status blocks enrollment (retired,
  replied, engaged, opted out) is the only home of that account-level exclusion. Deleting it
  does not forget the account: it re-admits it, next run, as a brand-new prospect.
* **A row's age is the date the code maintains** (``added_at``) — never ``signal_observed``,
  which dates the NEWS (the prospect skill admits a 210-day-old signal the day it is found),
  and never ``latest.json``'s mtime, which dates the last write to any row at all.
* **A campaign's record is kept.** A roster export named by ANY manifest, finished or not, the
  files of an account in a live relationship, and (without ``--force``) the operator's
  unenrolled ``ready-to-load*.csv`` staging files are guarded rather than archived.
* **No symlink is followed**, every target is confined under the profile's own content tree,
  and the apply path holds the profile lock.

"Archived" is not "erased": the gzip copies under ``prospects/.archive/`` are cleartext, and a
purged ledger row survives in ``prospects/.snapshots/``. The report says so.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from gtm_core.confine import ConfinementError, confined_dir, confined_source_file
from gtm_core.enrollment_gate import BLOCKED_ACCOUNT_STATUSES, DEFAULT_ENGAGED_STATUSES
from gtm_core.locks import LockBusy, profile_lock
from gtm_core.paths import resolve_content_root
from gtm_core.prospect_paths import (
    accounts_dir,
    archive_dir,
    latest_json,
    prospects_dir,
    sequences_dir,
)
from gtm_core.prospects_lock import ledger_lock
from gtm_core.prospects_state import _atomic_write, load_latest, snapshot
from gtm_core.retention_campaign_gate import CampaignGate, UnfinishedCampaignRefusal, campaign_gate
from gtm_core.slugify import slug

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
    """A sweep's outcome. With ``applied`` False every list is what WOULD happen (source
    paths); with it True, ``archived``/``archived_dossiers`` are the gzip copies written."""

    applied: bool = False
    campaigns: CampaignGate = field(default_factory=CampaignGate)
    archived: list[Path] = field(default_factory=list)
    guarded: list[tuple[Path, str]] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    archived_dossiers: list[Path] = field(default_factory=list)
    ledger_examined: bool = False
    purged_accounts: list[str] = field(default_factory=list)
    protected_accounts: list[str] = field(default_factory=list)
    undatable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Sweep:
    """What every pass needs — one value instead of seven positional arguments."""

    profile: str
    content_root: Path
    tree: Path  # content_root/<profile>, unresolved: the confinement root and the symlink-walk stop
    now: datetime
    apply: bool
    force: bool
    rosters: dict[Path, str]  # resolved roster export -> the campaign that names it
    result: SweepResult


def _roster_files(profile: str, content_root: Path) -> dict[Path, str]:
    """Every roster export a manifest names (finished OR not), resolved -> its campaign.

    Globbed exactly as ``email_campaign_dashboard.roster`` globs them — relative to
    ``prospects/`` — so "the files this campaign's page is built from" has one answer.
    """
    from gtm_core.campaigns_dashboard import _load_manifests

    base = prospects_dir(profile, content_root)
    out: dict[Path, str] = {}
    for manifest in _load_manifests(profile, content_root):
        for pattern in manifest.get("roster_globs") or []:
            for path in base.glob(pattern):
                out.setdefault(path.resolve(), str(manifest["slug"]))
    return out


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
    """A ready-to-load file that may not be enrolled yet — the operator's staging area. Archive
    one early and ``email-sequence`` must re-route from a ledger that may have been edited since."""
    return path.name.startswith("ready-to-load")


def _refusal(path: Path, tree: Path) -> str | None:
    """Why ``path`` must not be touched at all, or None.

    Walks the UNRESOLVED path up to the profile tree and refuses on the first symlink — a
    linked file, a linked account folder, a linked ``accounts/`` — before anything is resolved:
    the first version iterated ``is_dir()`` (which follows links) and would have gzipped and
    unlinked whatever a linked account folder pointed at, inside the content root or not.
    ``tree`` itself is exempt: ``content/<profile>`` is legitimately a link on some hosts, and
    the confinement below resolves both sides.
    """
    for part in (path, *path.parents):
        if part == tree:
            break
        if part.is_symlink():
            return f"symlink ({part.name}) — never followed"
    try:
        confined_source_file(path, content_root=tree, action="archive a file")
    except ConfinementError as exc:
        return str(exc)
    return None


def _triage(path: Path, sweep: _Sweep, ttl_days: int) -> tuple[str, str]:
    """``(archive | guard | skip, why)`` for one candidate — every keep-rule in one place."""
    why = _refusal(path, sweep.tree)
    if why:
        return "skip", why
    age_days = (sweep.now.timestamp() - path.lstat().st_mtime) / 86400
    if age_days < ttl_days:
        return "skip", f"fresh (age={age_days:.1f}d < {ttl_days}d)"
    campaign = sweep.rosters.get(path.resolve())
    if campaign:
        # Not overridable by --force: that flag is a statement about staging files, and a
        # roster export is the record a campaign's status page is rebuilt from.
        return "guard", f"roster export of campaign '{campaign}' — a campaign's record is kept"
    if _is_unenrolled_load_file(path) and not sweep.force:
        return "guard", (
            f"unenrolled load file (age={age_days:.0f}d) — "
            "pass --force to archive, or load into the sequencer first"
        )
    return "archive", ""


def _archive(path: Path, dest_dir: Path, sweep: _Sweep) -> Path:
    """gzip ``path`` into ``dest_dir``, then remove the original. The copy is CLEARTEXT."""
    dest_dir = confined_dir(dest_dir, content_root=sweep.tree)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = sweep.now.strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"{path.stem}-{stamp}{path.suffix}.gz"
    # "x": two same-named exports (prospects/ and sequences/) archived in the same second
    # must not overwrite each other — the second fails, is reported, and keeps its original.
    with path.open("rb") as f_in, gzip.open(dest, "xb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    path.unlink()
    return dest


def _process(path: Path, dest_dir: Path, bucket: list[Path], sweep: _Sweep, ttl: int) -> None:
    result = sweep.result
    try:
        verdict, why = _triage(path, sweep, ttl)
        if verdict == "skip":
            result.skipped.append((path, why))
        elif verdict == "guard":
            result.guarded.append((path, why))
        else:
            bucket.append(_archive(path, dest_dir, sweep) if sweep.apply else path)
    except (OSError, ConfinementError) as err:
        result.errors.append((path, str(err)))


def _sweep_exports(sweep: _Sweep, ttl_days: int) -> None:
    arc_dir = archive_dir(sweep.profile, sweep.content_root)
    for folder in (
        prospects_dir(sweep.profile, sweep.content_root),
        sequences_dir(sweep.profile, sweep.content_root),
    ):
        for path in sorted(folder.glob("*.csv")):
            if _is_target_pii_file(path):
                _process(path, arc_dir, sweep.result.archived, sweep, ttl_days)
            else:
                sweep.result.skipped.append((path, "protected or non-target"))


def _engaged_folders(sweep: _Sweep) -> dict[str, str]:
    """Account-folder slug -> ledger status, for accounts in a live relationship.

    A folder is named ``slug(company)`` and the ledger stamps the same value as ``id``; both are
    offered so a row missing one still protects its folder. Raises on an unreadable ledger.
    """
    out: dict[str, str] = {}
    for item in load_latest(sweep.profile, sweep.content_root).get("items", []):
        record = item if isinstance(item, dict) else {}
        status = str(record.get("status") or "").strip().lower().replace("_", "-")
        if status in DEFAULT_ENGAGED_STATUSES:
            out[str(record.get("id") or "").strip().lower()] = status
            out[slug(str(record.get("company") or ""))] = status
    out.pop("", None)
    return out


def _sweep_account_files(sweep: _Sweep, ttl_days: int) -> None:
    acc_dir = accounts_dir(sweep.profile, sweep.content_root)
    if not acc_dir.is_dir():
        return
    result = sweep.result
    try:
        engaged = _engaged_folders(sweep)
    except (OSError, ValueError) as err:
        # "Which accounts are in a live relationship" has no answer, so nothing here moves.
        result.errors.append((latest_json(sweep.profile, sweep.content_root), str(err)))
        return
    arc_dir = archive_dir(sweep.profile, sweep.content_root) / "accounts"
    for folder in sorted(acc_dir.iterdir()):
        if folder.is_symlink():
            result.skipped.append((folder, f"symlink ({folder.name}) — never followed"))
            continue
        if not folder.is_dir():
            continue
        if folder.name in engaged:
            why = f"account status '{engaged[folder.name]}' — a live relationship's files are kept"
            result.guarded.append((folder, why))
            continue
        for path in sorted(folder.iterdir()):
            if path.is_symlink() or path.is_file():
                _process(path, arc_dir / folder.name, result.archived_dossiers, sweep, ttl_days)
        try:
            if sweep.apply and not any(folder.iterdir()):
                folder.rmdir()
        except OSError as err:
            result.errors.append((folder, str(err)))


def _added_on(item: dict[str, Any]) -> date | None:
    """The day the code first recorded this row (``upsert_latest`` stamps ``added_at`` once and
    never moves it), or None — and a row with no admissible date is kept, never guessed at."""
    try:
        return datetime.fromisoformat(str(item.get("added_at") or "").strip()).date()
    except ValueError:
        return None


def _sweep_ledger(sweep: _Sweep, ttl_days: int) -> None:
    result = sweep.result
    path = latest_json(sweep.profile, sweep.content_root)
    if not path.is_file():
        return
    why = _refusal(path, sweep.tree)
    if why:
        result.skipped.append((path, why))
        return
    try:
        # The ledger's own write lock, nested inside profile_lock: every other ledger writer
        # takes this one, so a do-not-contact landing mid-sweep is ordered, never lost. A plan
        # takes no lock at all: it must not create even a lock file.
        with ledger_lock(path) if sweep.apply else contextlib.nullcontext():
            data = load_latest(sweep.profile, sweep.content_root)
            kept: list[Any] = []
            purged: list[str] = []
            for item in data.get("items", []):
                record = item if isinstance(item, dict) else {}
                name = str(record.get("company") or record.get("id") or "unknown")
                status = str(record.get("status") or "").strip().lower().replace("_", "-")
                added = _added_on(record)
                if status in BLOCKED_ACCOUNT_STATUSES:
                    # Every status the enrollment gate refuses on. This row IS the exclusion.
                    result.protected_accounts.append(name)
                elif added is None:
                    result.undatable.append(name)
                elif (sweep.now.date() - added).days >= ttl_days:
                    purged.append(name)
                    continue
                kept.append(item)
            result.purged_accounts.extend(purged)
            if purged and sweep.apply:
                snapshot(sweep.profile, sweep.content_root)
                data["items"] = kept
                _atomic_write(path, data)
    except (OSError, ValueError) as err:
        result.errors.append((path, str(err)))


def sweep_stale_pii(
    profile: str,
    *,
    ttl_days: int = 7,
    dossier_ttl_days: int = 60,
    force: bool = False,
    content_root: Path | None = None,
    apply: bool = False,
    include_ledger: bool = False,
    now: datetime | None = None,
) -> SweepResult:
    """Plan — or, with ``apply=True``, perform — a retention sweep of one profile.

    ``ttl_days`` ages export CSVs; ``dossier_ttl_days`` ages account files and ledger rows.
    ``force`` also archives unenrolled ready-to-load files and overrides nothing else. Without
    ``apply`` nothing on disk changes — not even a lock file. ``include_ledger`` is opt-in
    because ``latest.json`` is the record of record. ``now`` is the clock (tests).

    Raises :class:`UnfinishedCampaignRefusal` on ``apply`` while the campaign gate blocks (nothing
    has been written), and ``LockBusy`` on ``apply`` while another process holds the profile lock.
    """
    root = content_root if content_root is not None else resolve_content_root()
    tree = prospects_dir(profile, root).parent
    result = SweepResult(applied=apply, campaigns=campaign_gate(profile, root))
    if apply and result.campaigns.blocks_apply:
        raise UnfinishedCampaignRefusal(profile, result.campaigns)
    if not tree.is_dir():
        return result  # nothing to sweep — and taking the lock would invent the tenant folder
    sweep = _Sweep(
        profile=profile,
        content_root=root,
        tree=tree,
        now=now or datetime.now(UTC),
        apply=apply,
        force=force,
        rosters=_roster_files(profile, root),
        result=result,
    )
    # Non-blocking: an operator's command that hangs behind a pipeline run explains nothing.
    lock = profile_lock(root, profile, blocking=False) if apply else contextlib.nullcontext()
    with lock:
        _sweep_exports(sweep, ttl_days)
        _sweep_account_files(sweep, dossier_ttl_days)
        if include_ledger:
            result.ledger_examined = True
            _sweep_ledger(sweep, dossier_ttl_days)
    return result


def _print_section(title: str, entries: list[Any]) -> None:
    print(f"  {title}: {len(entries)}")
    for entry in entries:
        print(f"    - {entry[0]}: {entry[1]}" if isinstance(entry, tuple) else f"    - {entry}")


def _print_report(res: SweepResult, profile: str) -> None:
    arc = archive_dir(profile)
    note = "(gzip, still cleartext) — not erased"
    if res.applied:
        print(f"Retention sweep APPLIED for profile '{profile}':")
        files, accts = f"archived to {arc} {note}", f"archived to {arc / 'accounts'} {note}"
        rows = "purged (still in prospects/.snapshots — not erased)"
    else:
        print(f"[DRY RUN — nothing was changed; pass --apply to act] Plan for '{profile}':")
        files = accts = "that would be archived"
        rows = "that would be purged"
    # The campaign verdict comes FIRST: whether anything may be purged at all outranks what would be.
    verdict = res.campaigns.report_lines()
    print("\n".join(verdict[:-1] if res.applied else verdict))
    _print_section(f"Export CSVs {files}", res.archived)
    _print_section(f"Account files {accts}", res.archived_dossiers)
    if res.ledger_examined:
        _print_section(f"Ledger rows {rows}", res.purged_accounts)
        _print_section("Ledger rows kept — exclusion or live relationship", res.protected_accounts)
        _print_section("Ledger rows undatable — kept", res.undatable)
    else:
        print("  Ledger: not examined (pass --include-ledger)")
    _print_section("Guarded — kept", res.guarded)
    print(f"  Skipped (fresh / protected / symlink): {len(res.skipped)}")
    total = len(res.archived) + len(res.archived_dossiers)
    print(f"  Total: {total} file(s), {len(res.purged_accounts)} ledger row(s)")


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    """CLI: print a retention plan; with ``--apply``, carry it out. 2 = refused, 1 = errors."""
    parser = argparse.ArgumentParser(
        prog="gtm_core.retention_sweep",
        description="Plan (default) or apply the archiving of stale prospect CSVs and account "
        "files, and optionally the purge of aged ledger rows. Refused until every campaign is over.",
    )
    add = parser.add_argument
    add("--profile", required=True, help="Tenant profile name")
    add("--ttl-days", type=int, default=7, help="Age in days to archive an export CSV (7)")
    add("--dossier-ttl-days", type=int, default=60, help="Age for account files, ledger rows (60)")
    mode = parser.add_mutually_exclusive_group()
    # No flag overrides an unfinished campaign, on purpose (§R13): closing it is the override.
    mode.add_argument("--apply", action="store_true", help="Act. Exit 2 until campaigns are closed")
    mode.add_argument("--dry-run", action="store_true", help="The default; kept as a no-op alias")
    add("--force", action="store_true", help="Also archive unenrolled ready-to-load files")
    add("--include-ledger", action="store_true", help="Also purge aged, non-exclusion ledger rows")
    args = parser.parse_args(argv)

    try:
        res = sweep_stale_pii(
            args.profile,
            ttl_days=args.ttl_days,
            dossier_ttl_days=args.dossier_ttl_days,
            force=args.force,
            apply=args.apply,
            include_ledger=args.include_ledger,
            now=now,
        )
    except UnfinishedCampaignRefusal as refusal:
        print(str(refusal), file=sys.stderr)
        return 2
    except LockBusy as busy:
        print(f"{busy} — nothing was changed; try again when the run finishes", file=sys.stderr)
        return 1

    _print_report(res, args.profile)
    for path, err in res.errors:
        print(f"  ERROR {path}: {err}", file=sys.stderr)
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
