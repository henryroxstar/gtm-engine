"""Crash-safe file writes and snapshot rotation, in one place.

Four modules had each hand-rolled the same tmp-file-then-``os.replace`` dance and
the same keep-N snapshot prune, and a fifth — the suppression ledger's ``apply``,
the one that rewrites a *send list* — had not: it opened the target for ``"w"`` in
place, so a crash between the truncate and the last row left a half-written file
where a fully-written one used to be. On that path the truncated artifact is the
list of people about to be emailed.

Nothing here is clever; it exists so the guarantee is stated once and a new call
site cannot get it subtly wrong. Tenant-agnostic and stdlib-only, same shape as
:mod:`gtm_core.slugify` and :mod:`gtm_core.finding_budget`, so any module can
import it without dragging in a profile.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "atomic_write_text",
    "atomic_write_json",
    "atomic_write_csv",
    "snapshot_file",
    "utc_stamp",
]


def utc_stamp() -> str:
    """Microsecond-resolution UTC stamp: back-to-back snapshots never collide."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")


def atomic_write_text(path: Path, text: str, *, prefix: str = ".tmp-") -> None:
    """Write ``text`` to ``path`` so readers see either the old file or the new one.

    ``os.replace`` is atomic within a filesystem, and the ``fsync`` before it means
    the replacement is durable rather than merely queued.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path: Path, data: object, *, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False))


def atomic_write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    """Write a CSV atomically, blanking missing keys rather than raising.

    Mirrors the ``extrasaction="ignore"`` + blank-fill convention the pool writers
    already use, so a row carrying an extra column from an older build does not
    abort a rewrite half-way.
    """
    import io

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(fieldnames), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: (row.get(c) or "") for c in fieldnames})
    atomic_write_text(path, buf.getvalue())


def snapshot_file(
    path: Path,
    snap_dir: Path,
    *,
    prefix: str,
    keep: int = 30,
    suffix: str | None = None,
) -> Path | None:
    """Copy ``path`` into ``snap_dir`` as ``<prefix>-<stamp><suffix>``; prune to ``keep``.

    Returns ``None`` if the source does not exist — a first run has nothing to
    protect, which is not an error.
    """
    if not path.exists():
        return None
    ext = suffix if suffix is not None else path.suffix
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / f"{prefix}-{utc_stamp()}{ext}"
    dest.write_bytes(path.read_bytes())
    for old in sorted(snap_dir.glob(f"{prefix}-*{ext}"))[:-keep]:
        old.unlink(missing_ok=True)
    return dest
