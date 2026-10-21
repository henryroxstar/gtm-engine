"""Manage and prune knowledge topic snapshots (R-14, PRD §9-18).

Snapshots live under content/<profile>/.snapshots/knowledge/<topic>.<ISO-stamp>.
Pruning keeps the newest 20 snapshots per topic.

Deletion is operator-owned and explicit: this module is registered in
tests/lint/destructive_reachability.py and must never be called by build steps or skills.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from .paths import _safe_segment, resolve_content_root

SNAPSHOT_KEEP_DEFAULT = 20


def snapshot_dir(content_root: Path, profile: str) -> Path:
    return content_root / _safe_segment(profile, "profile") / ".snapshots" / "knowledge"


def group_snapshots_by_topic(snap_dir: Path) -> dict[str, list[Path]]:
    """Group snapshot files in snap_dir by topic name."""
    if not snap_dir.is_dir():
        return {}
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in sorted(snap_dir.rglob("*.*")):
        if not p.is_file():
            continue
        # Format is <topic>.<timestamp>, possibly nested in subdirectories
        rel = p.relative_to(snap_dir)
        topic_stem = rel.name.rsplit(".", 1)[0]
        topic = (rel.parent / topic_stem).as_posix()
        groups[topic].append(p)
    return dict(groups)


def prune_snapshots(
    content_root: Path,
    profile: str,
    keep: int = SNAPSHOT_KEEP_DEFAULT,
    dry_run: bool = False,
) -> dict[str, list[Path]]:
    """Prune snapshots older than the newest `keep` per topic.

    Returns dict with 'to_delete' (or 'deleted') paths.
    """
    snap_dir = snapshot_dir(content_root, profile)
    groups = group_snapshots_by_topic(snap_dir)

    to_prune: list[Path] = []
    for _topic, snaps in sorted(groups.items()):
        # Sort chronologically (filename has ISO stamp)
        sorted_snaps = sorted(snaps)
        if len(sorted_snaps) > keep:
            excess = sorted_snaps[:-keep]
            to_prune.extend(excess)

    if dry_run:
        return {"to_delete": to_prune, "kept": keep}

    deleted: list[Path] = []
    for p in to_prune:
        p.unlink(missing_ok=True)
        deleted.append(p)

    return {"deleted": deleted, "kept": keep}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gtm_core.snapshots",
        description="Prune old knowledge topic snapshots (operator-owned).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    prune_parser = sub.add_parser("prune", help="Prune old snapshots beyond retention ceiling")
    prune_parser.add_argument("--profile", required=True)
    prune_parser.add_argument("--keep", type=int, default=SNAPSHOT_KEEP_DEFAULT)
    prune_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned deletions without deleting any files",
    )
    prune_parser.add_argument("--content-root", default=None)

    args = parser.parse_args(argv)
    content_root = (
        Path(args.content_root).expanduser().resolve()
        if args.content_root
        else resolve_content_root()
    )

    if args.cmd == "prune":
        res = prune_snapshots(content_root, args.profile, keep=args.keep, dry_run=args.dry_run)
        if args.dry_run:
            print(
                f"Dry run: {len(res['to_delete'])} snapshot(s) would be pruned (keeping {args.keep} newest per topic):"
            )
            for p in res["to_delete"]:
                print(f"  would delete: {p.name}")
        else:
            print(
                f"Pruned {len(res['deleted'])} snapshot(s) (keeping {args.keep} newest per topic):"
            )
            for p in res["deleted"]:
                print(f"  deleted: {p.name}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
