"""Safe read/merge/write for ``content/<profile>/prospects/latest.json``.

`latest.json` is a **cumulative** dashboard-state file: it grows across every
prospect run and carries the operator's per-account ``status`` edits
(contacted/qualified/disqualified) made between runs. A blind full-file
overwrite (writing only the current run's items) silently destroys every prior
account and every status edit — a data-loss class this module exists to prevent.

Guarantees:
  * **Merge, never replace.** New items upsert by a precise identity key
    (:func:`_identity_key` — domain-first, then id, then a non-lossy company
    name); an account already present keeps its operator-edited ``status`` (and
    other sticky fields) unless the caller explicitly refreshes them.
  * **Never drop an existing account.** The merged result always starts from
    *every* existing item and only appends/updates — even a pre-existing
    duplicate key is retained, never silently collapsed.
  * **Snapshot before every write.** The current file is copied to
    ``prospects/.snapshots/latest-<UTC-timestamp>.json`` first, so any bad write
    is one ``restore`` away. Snapshots are pruned to the most recent N.
  * **Shrink tripwire.** A write that would drop the item count below what's on
    disk is refused unless ``allow_shrink=True``. By construction a merge can't
    shrink, so this is a regression tripwire that catches any future change which
    reintroduces dropping.
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


def _identity_key(item: dict) -> str:
    """The single source of truth for account identity across merge + dedup.

    Precise and non-lossy — unlike :func:`_norm`, it never strips corporate
    suffixes or non-ASCII characters, so genuinely-different companies can't
    collide and a non-Latin name can't collapse to an empty key.

    Precedence:
      * ``domain`` (the true unique business identifier) → ``"d:<domain>"``;
      * else ``id`` → ``"i:<id>"``;
      * else a whitespace-collapsed, lowercased company name (suffixes and
        non-ASCII preserved) → ``"c:<company>"``;
      * else ``""`` (caller must not drop an empty-key item — append it).

    Domain-first means two different companies can never merge (they can't share
    a domain); the only edge is an existing domainless entry re-emitted *with* a
    domain, which yields a visible duplicate — never a silent drop.
    """
    domain = str(item.get("domain") or "").strip().lower()
    if domain:
        return f"d:{domain}"
    cid = str(item.get("id") or "").strip().lower()
    if cid:
        return f"i:{cid}"
    company = " ".join(str(item.get("company") or "").split()).lower()
    if company:
        return f"c:{company}"
    return ""


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
        data = json.load(f)
    # Fail loud on a valid-JSON-but-wrong-shape file rather than letting a later
    # .get()/iteration throw an opaque error mid-merge (same intent as raising on
    # a corrupt file: a malformed cumulative file must never be treated as empty).
    if not isinstance(data, dict):
        raise ValueError(f"latest.json must be a JSON object, got {type(data).__name__}: {p}")
    if not isinstance(data.get("items", []), list):
        raise ValueError(f'latest.json "items" must be a JSON array: {p}')
    return data


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
    allow_shrink: bool = False,
    content_root: Path | None = None,
) -> dict:
    """Merge ``new_items`` into latest.json by :func:`_identity_key`.

    Merge-only by construction: the result starts from *every* existing account
    and only appends/updates — it can never drop a prior account, so a full-file
    overwrite is impossible through this path. Existing accounts keep their
    STICKY_FIELDS (operator status edits, etc.); all other fields are refreshed
    from the incoming item. An incoming item with no derivable key is appended
    (never dropped). Snapshots the current file first, writes atomically. Refuses
    to shrink the item count unless ``allow_shrink=True``. Returns a summary dict.
    """
    current = load_latest(profile, content_root)
    existing_items = current.get("items", [])

    # Start from ALL existing items — none is ever dropped, even a pre-existing
    # duplicate key (setdefault keeps the first occurrence as the merge target
    # while leaving the duplicate in the list).
    result_items = [dict(it) for it in existing_items]
    index: dict[str, int] = {}
    for pos, it in enumerate(result_items):
        k = _identity_key(it)
        if k:
            index.setdefault(k, pos)

    added, updated = 0, 0
    for item in new_items:
        key = _identity_key(item)
        if not key:
            # Un-keyable (e.g. a pure-non-ASCII name with no domain/id): append
            # rather than silently drop.
            result_items.append(dict(item))
            added += 1
            continue
        if key in index:
            prior = result_items[index[key]]
            merged = dict(item)
            for f in STICKY_FIELDS:
                if f in prior and prior[f] not in (None, "", "new"):
                    merged[f] = prior[f]
            result_items[index[key]] = merged
            updated += 1
        else:
            result_items.append(dict(item))
            index[key] = len(result_items) - 1
            added += 1

    if not allow_shrink and len(result_items) < len(existing_items):
        raise ValueError(
            f"refusing to shrink latest.json: {len(existing_items)} -> "
            f"{len(result_items)} items; pass allow_shrink=True to override"
        )

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
    m.add_argument(
        "--allow-shrink",
        action="store_true",
        help="override the shrink tripwire (only if the merge would legitimately reduce the item count)",
    )

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
            allow_shrink=args.allow_shrink,
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
