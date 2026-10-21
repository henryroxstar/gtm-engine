"""Reading the capture index tolerantly, and ordering it by time (signal-first R0.2).

A git union merge of two writers' ``sources/index.jsonl`` can interleave lines, leave conflict
markers, or tear a line. Lookups need the entries in time order and must survive a bad line;
``signal_sources.prune`` (which deletes files) keeps its own strict reader on purpose, because
silently skipping a line there could delete a page the index still references.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

_CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})(\s|$)")


def index_path(dir_path: Path) -> Path:
    idx = dir_path / "index.jsonl"
    return idx if idx.is_file() else dir_path / "sources.jsonl"


def read_tolerant(dir_path: Path) -> tuple[list[dict], list[dict]]:
    """(entries, problems). Never raises on a bad line; ``problems`` is ``{line, kind, text}``."""
    p = index_path(dir_path)
    if not p.is_file():
        return [], []
    try:
        content = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Unreadable capture index {p}: {exc}") from exc
    entries: list[dict] = []
    problems: list[dict] = []
    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if _CONFLICT_MARKER.match(line):
            problems.append({"line": lineno, "kind": "conflict-marker", "text": line[:40]})
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            entries.append(obj)
        else:
            problems.append({"line": lineno, "kind": "corrupt", "text": line[:40]})
    return entries, problems


def fetched_at_key(entry: dict) -> tuple[int, float, str]:
    """Sort key: (dated?, UTC epoch seconds, sha). An undated row sorts before every dated one."""
    sha = str(entry.get("sha256", ""))
    raw = str(entry.get("fetched_at", "") or "").strip()
    if raw:
        try:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return (0, 0.0, sha)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        return (1, dt.timestamp(), sha)
    return (0, 0.0, sha)


def filed_just_now(index: Path, norm: str, sha: str, ts: str, window_s: int = 120) -> bool:
    """True when the index's tail already holds this page, filed within ``window_s`` of ``ts``."""
    try:
        now = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        with index.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            tail = fh.read().decode("utf-8", errors="replace")
    except (ValueError, OSError):
        return False
    for line in reversed(tail.splitlines()):
        try:
            row = json.loads(line)
            then = datetime.datetime.fromisoformat(str(row["fetched_at"]).replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError):
            continue
        if row.get("url_norm") == norm and row.get("sha256") == sha:
            if (now.tzinfo is None) != (then.tzinfo is None):
                return False
            return abs((now - then).total_seconds()) <= window_s
    return False
