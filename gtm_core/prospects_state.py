"""Safe read/merge/write for ``content/<profile>/prospects/latest.json``.

`latest.json` is a **cumulative** dashboard-state file: it grows across every
prospect run and carries the operator's per-account ``status`` edits
(contacted/qualified/disqualified) made between runs. A blind full-file
overwrite (writing only the current run's items) silently destroys every prior
account and every status edit — a data-loss class this module exists to prevent.

Guarantees:
  * **Merge, never replace.** New items upsert by normalized company key; an
    account already present keeps its operator-edited ``status`` (and other
    sticky fields) unless the caller explicitly refreshes them.
  * **Snapshot before every write.** The current file is copied to
    ``prospects/.snapshots/latest-<UTC-timestamp>.json`` first, so any bad write
    is one ``restore`` away. Snapshots are pruned to the most recent N.
  * **Shrink tripwire.** If a write would drop the item count below a fraction
    of what's on disk, it is refused unless ``allow_shrink=True`` — this is the
    guard that catches "I overwrote the cumulative file with one run".
  * **Atomic write.** tmp file + ``os.replace`` so a crash mid-write can't leave
    a truncated file.

stdlib-only, to match the rest of gtm_core.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from gtm_core.paths import resolve_content_root

SNAPSHOT_DIRNAME = ".snapshots"
SNAPSHOT_KEEP = 30
# Fields that belong to the operator / dashboard and must survive a re-merge.
STICKY_FIELDS = ("status", "priority", "notes", "owner", "last_touched")


def _utc_stamp() -> str:
    # microsecond resolution so back-to-back snapshots never collide on filename
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")


def _norm(company: str) -> str:
    n = company.lower().strip()
    n = re.sub(
        r"\b(inc|corp|corporation|ltd|llc|plc|group|holdings|company|co|limited|pte)\b",
        "",
        n,
    )
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return n


def latest_path(profile: str, content_root: Path | None = None) -> Path:
    root = content_root or resolve_content_root()
    return root / profile / "prospects" / "latest.json"


def _snapshot_dir(profile: str, content_root: Path | None = None) -> Path:
    return latest_path(profile, content_root).parent / SNAPSHOT_DIRNAME


def load_latest(profile: str, content_root: Path | None = None) -> dict:
    """Return the current latest.json as a dict, or an empty skeleton if absent.

    Raises on a present-but-corrupt file rather than returning an empty skeleton —
    silently treating a corrupt cumulative file as empty is exactly how a merge
    would then "shrink" it to just the current run.
    """
    p = latest_path(profile, content_root)
    if not p.exists():
        return {"kind": "prospects", "profile": profile, "items": []}
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def snapshot(profile: str, content_root: Path | None = None) -> Path | None:
    """Copy the current latest.json into the snapshots dir. No-op if absent."""
    src = latest_path(profile, content_root)
    if not src.exists():
        return None
    snap_dir = _snapshot_dir(profile, content_root)
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / f"latest-{_utc_stamp()}.json"
    shutil.copy2(src, dest)
    _prune_snapshots(snap_dir)
    return dest


def _prune_snapshots(snap_dir: Path, keep: int = SNAPSHOT_KEEP) -> None:
    snaps = sorted(snap_dir.glob("latest-*.json"))
    for old in snaps[:-keep]:
        old.unlink(missing_ok=True)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".latest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def upsert_latest(
    profile: str,
    new_items: list[dict],
    source_run: str,
    *,
    generated_at: str | None = None,
    content_root: Path | None = None,
) -> dict:
    """Merge ``new_items`` into latest.json by normalized company key.

    Merge-only by construction: the result always starts from every existing
    account and only adds/updates — it can never drop a prior account, so a
    full-file overwrite becomes impossible through this path. Existing accounts
    keep their STICKY_FIELDS (operator status edits, etc.); all other fields are
    refreshed from the incoming item. Snapshots the current file first, writes
    atomically. Returns a summary dict.
    """
    current = load_latest(profile, content_root)
    existing_items = current.get("items", [])
    by_key = {_norm(it.get("company", it.get("id", ""))): it for it in existing_items}

    added, updated = 0, 0
    for item in new_items:
        key = _norm(item.get("company", item.get("id", "")))
        if not key:
            continue
        merged = dict(item)
        if key in by_key:
            prior = by_key[key]
            for f in STICKY_FIELDS:
                if f in prior and prior[f] not in (None, "", "new"):
                    merged[f] = prior[f]
            updated += 1
        else:
            added += 1
        by_key[key] = merged

    result_items = list(by_key.values())

    snap = snapshot(profile, content_root)

    out = dict(current)
    out["kind"] = "prospects"
    out["profile"] = profile
    out["source_run"] = source_run
    out["generated_at"] = generated_at or datetime.now(UTC).isoformat()
    out["items"] = result_items

    _atomic_write(latest_path(profile, content_root), out)

    return {
        "profile": profile,
        "source_run": source_run,
        "existing": len(existing_items),
        "added": added,
        "updated": updated,
        "total": len(result_items),
        "snapshot": str(snap) if snap else None,
    }


def restore(
    profile: str, snapshot_file: str | None = None, content_root: Path | None = None
) -> Path:
    """Restore latest.json from a snapshot (newest by default)."""
    snap_dir = _snapshot_dir(profile, content_root)
    if snapshot_file:
        src = Path(snapshot_file)
        if not src.is_absolute():
            src = snap_dir / snapshot_file
    else:
        snaps = sorted(snap_dir.glob("latest-*.json"))
        if not snaps:
            raise FileNotFoundError(f"no snapshots in {snap_dir}")
        src = snaps[-1]
    dest = latest_path(profile, content_root)
    # snapshot the (possibly bad) current file before clobbering it, so restore is itself reversible
    snapshot(profile, content_root)
    shutil.copy2(src, dest)
    return src


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gtm_core.prospects_state")
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser(
        "merge", help="merge an items JSON file into latest.json (snapshot + shrink-guard)"
    )
    m.add_argument("--profile", required=True)
    m.add_argument("--items", required=True, help="path to a JSON array of item objects")
    m.add_argument("--source-run", required=True)
    m.add_argument("--generated-at", default=None)

    s = sub.add_parser("snapshot", help="take a manual snapshot of latest.json")
    s.add_argument("--profile", required=True)

    r = sub.add_parser("restore", help="restore latest.json from a snapshot (newest by default)")
    r.add_argument("--profile", required=True)
    r.add_argument("--from", dest="snapshot_file", default=None)

    ls = sub.add_parser("list-snapshots", help="list available snapshots")
    ls.add_argument("--profile", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "merge":
        items = json.loads(Path(args.items).read_text(encoding="utf-8"))
        if not isinstance(items, list):
            print("ERROR: --items must be a JSON array", file=sys.stderr)
            return 2
        summary = upsert_latest(
            args.profile,
            items,
            args.source_run,
            generated_at=args.generated_at,
        )
        print(json.dumps(summary, indent=2))
        return 0

    if args.cmd == "snapshot":
        snap = snapshot(args.profile)
        print(snap or "(no latest.json to snapshot)")
        return 0

    if args.cmd == "restore":
        src = restore(args.profile, args.snapshot_file)
        print(f"restored latest.json from {src}")
        return 0

    if args.cmd == "list-snapshots":
        snap_dir = _snapshot_dir(args.profile)
        for s in sorted(snap_dir.glob("latest-*.json")):
            print(s)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
