from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ..merge_hygiene import (
    clean_company,
    clean_first_name,
    clean_last_name,
    clean_segment,
    clean_title,
)
from .columns import MASTER_COLS
from .confidence import classify_confidence
from .paths import _pool_dir, _sequences_dir


def _existing_master_path(profile: str, content_root: Path | None) -> Path | None:
    """The canonical rolling file if it already exists; otherwise the newest
    dated ``master-list*.csv`` on disk (so a first run seeds from whatever the
    operator built by hand). Never assumes a fixed prior filename."""
    pool = _pool_dir(profile, content_root)
    canonical = pool / "master-list.csv"
    if canonical.exists():
        return canonical
    seq_dir = _sequences_dir(profile, content_root)
    # Migration: seed from the pre-.pool location (or any hand-built dated file)
    # the first time we run under the new layout.
    candidates = sorted(seq_dir.glob("master-list*.csv"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _load_master(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="ignore") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        email = (r.get("email") or "").strip().lower()
        if not email:
            continue
        rec = {c: (r.get(c) or "") for c in MASTER_COLS}
        rec["email"] = email
        # Repair merge fields on load as well as on ingestion, so the fix is retroactive:
        # rows folded into the master list before this gate existed are cleaned on the next
        # sweep rather than staying dirty forever. Idempotent, so re-running is a no-op.
        rec["first"] = clean_first_name(rec["first"], rec["last"], email)
        rec["last"] = clean_last_name(rec["last"])
        rec["company"] = clean_company(rec["company"])
        rec["title"] = clean_title(rec["title"])
        # Storage is lowercase (`merge_hygiene.SEGMENTS`); every reader that COMPARES a
        # segment lowercases first, but the pool is also read by people and by joins that
        # do not. Measured 2026-09-24: 46 of 622 pooled rows spelled `Startup`/`Enterprise`
        # beside 576 lowercase ones. Case-only: an unknown value passes through as written.
        rec["segment"] = clean_segment(rec["segment"])
        if not rec["conf_tier"]:
            rec["conf_tier"] = classify_confidence(
                rec["email_status"],
                rec["conf"],
                segment=rec["segment"],
                email=rec["email"],
                company_domain=rec["company_domain"],
            )
        out.append(rec)
    return out


def _atomic_write_csv(path: Path, rows: list[dict]) -> None:
    _atomic_write_csv_cols(path, rows, MASTER_COLS)


def _atomic_write_csv_cols(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _snapshot(path: Path, snap_dir: Path, keep: int = 30) -> Path | None:
    if not path.exists():
        return None
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")
    dest = snap_dir / f"master-list-{stamp}.csv"
    dest.write_bytes(path.read_bytes())
    snaps = sorted(snap_dir.glob("master-list-*.csv"))
    for old in snaps[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def _append_blocked_log(
    profile: str,
    content_root: Path | None,
    blocked: list[dict],
    reason_of=lambda r: r["email_status"] or r["conf"],
) -> None:
    if not blocked:
        return
    path = _pool_dir(profile, content_root) / ".blocked-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for r in blocked:
            f.write(
                json.dumps(
                    {
                        "logged_at": stamp,
                        "email": r["email"],
                        "company": r["company"],
                        "src": r["src"],
                        "reason": reason_of(r),
                    }
                )
                + "\n"
            )
