"""What to load into the sending tool — ONE file per sending list, and nothing else.

The page used to list every ``ready-to-load*.csv`` under one "safe to download" heading:
the pooled ``ready-to-load.csv`` beside the two dated files the router splits it into. The
pooled file holds EVERY row, including contacts still waiting on the operator's decision, so
the most obviously-named file on the page was the one that must not be loaded.

The rule here, in the order it is applied:

* the router's dated per-list files hold only rows cleared for that list — they are the
  things to load, each with its own row count. An empty one is said to be empty, not linked;
* a per-list file whose date stamp is older than the last sort on record (the newest
  ``stamp`` in the routed state) is OUT OF DATE and is not offered;
* the pooled file is a working list. It is named as "do NOT load" while anybody is waiting
  on a decision, kept "for reference" once per-list files exist, and offered as the thing to
  load only when nobody is waiting AND the list was never split.

Reads file names, sizes and the routed state it is handed; no row content leaves this module.
"""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path

from ..prospects_consolidate import _prospects_dir

#: Files untouched for longer than this are not offered — the same masking the section has
#: always applied, so a forgotten export cannot be loaded by accident.
TTL_DAYS = 7

#: The router's loadable lists, in the order they print, with the operator's words for each.
#: Pinned equal to ``gtm_core.lanes.router._LOADABLE_LANES`` by the tests rather than imported:
#: importing that module runs the whole ``lanes`` package for two strings.
LIST_LABELS: dict[str, str] = {"personalised": "Personalised list", "generic": "Generic list"}

WORKING_NAME = "ready-to-load.csv"
_LIST_FILE_RE = re.compile(
    r"^ready-to-load-(" + "|".join(LIST_LABELS) + r")-(\d{4}-\d{2}-\d{2})\.csv$"
)


def _data_rows(path: Path) -> int | None:
    """Rows under the header, or ``None`` when the file cannot be read as a CSV."""
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            filled = sum(1 for row in csv.reader(fh) if any(cell.strip() for cell in row))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    return max(filled - 1, 0)


def _entry(path: Path, base: Path, now: float) -> dict | None:
    rows = _data_rows(path)
    if rows is None:
        return None
    return {
        "name": path.name,
        "path": str(path.relative_to(base)),
        "rows": rows,
        "age_days": round((now - path.stat().st_mtime) / 86400, 1),
    }


def _list_state(entry: dict, sorted_on: str | None) -> str:
    if sorted_on and entry["stamp"] < sorted_on:
        return "out_of_date"
    if entry["age_days"] >= TTL_DAYS:
        return "old"
    return "load" if entry["rows"] else "empty"


def load_files(
    profile: str, content_root: Path | None, records: list[dict], *, waiting: int
) -> dict:
    """``{"lists": [...], "working": {...} | None, "sorted_on": str | None}``.

    ``records`` is the routed state (one dict per contact, each carrying the ``stamp`` of the
    sort that placed it); ``waiting`` is the "Waiting on you" contact count the caller already
    derived from that same state — passed in, never re-derived here.
    """
    prospects = _prospects_dir(profile, content_root)
    seq_dir, base, now = prospects / "sequences", prospects.parent, time.time()
    sorted_on = max((str(r.get("stamp") or "") for r in records), default="") or None

    newest: dict[str, tuple[str, Path]] = {}
    if seq_dir.is_dir():
        for path in seq_dir.iterdir():
            hit = _LIST_FILE_RE.match(path.name)
            if hit and path.is_file() and hit.group(2) >= newest.get(hit.group(1), ("",))[0]:
                newest[hit.group(1)] = (hit.group(2), path)

    lists: list[dict] = []
    for key, label in LIST_LABELS.items():
        if key not in newest:
            continue
        stamp, path = newest[key]
        entry = _entry(path, base, now)
        if entry is None:
            continue
        entry.update(list=key, label=label, stamp=stamp)
        entry["state"] = _list_state(entry, sorted_on)
        lists.append(entry)

    working = _entry(seq_dir / WORKING_NAME, base, now) if seq_dir.is_dir() else None
    if working is not None:
        working["waiting"] = waiting
        if waiting:
            working["state"] = "do_not_load"
        elif lists:
            working["state"] = "reference"
        else:
            working["state"] = "load" if working["age_days"] < TTL_DAYS else "old"
    return {"lists": lists, "working": working, "sorted_on": sorted_on}
