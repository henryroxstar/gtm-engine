"""House notes — the operator's taste, accumulated one Gate 1 edit at a time.

An Edit at the plan gate is the clearest signal in the system about what this tenant actually wants,
and until now it was spent on one draft and forgotten. Each edit that changes a brief decision is
appended here, dated, so the next reader loads what the operator has already said rather than
learning it again.

Two properties, both deliberate:

**Capped and oldest-dropped.** Twenty lines. A note file that grows without bound stops being read,
and a taste signal nobody reads is the same as no signal. The cap forces the list to stay a summary
of recent corrections rather than an archive.

**Append-only through this module.** Never ``Edit``/``Write`` on the file: the append is dated and
capped in one place, so a caller cannot half-apply the rule.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .paths import _safe_segment, resolve_content_root

NOTES_FILENAME = "house-notes.md"

#: Oldest lines are dropped past this. A cap that is never hit is a cap nobody chose.
MAX_LINES = 20

_HEADER = (
    "# House notes\n\n"
    "What the operator changed at a gate, newest last. Appended by `gtm_core.house_notes`; the\n"
    f"oldest line is dropped past {MAX_LINES}. Read by the second reader so taste accumulates in\n"
    "one place instead of being re-learned every run.\n\n"
)


def notes_path(profile: str, *, content_root: Path | None = None) -> Path:
    root = resolve_content_root() if content_root is None else Path(content_root)
    return root / _safe_segment(profile, "profile") / "exemplars" / NOTES_FILENAME


def read_lines(profile: str, *, content_root: Path | None = None) -> list[str]:
    """The note lines, newest last. Missing file means no notes, never an error."""
    path = notes_path(profile, content_root=content_root)
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("- ")
    ]


def append(
    profile: str,
    line: str,
    *,
    content_root: Path | None = None,
    today: str | None = None,
) -> Path:
    """Append one dated note, dropping the oldest past ``MAX_LINES``. Returns the file written."""
    text = " ".join(str(line).split())
    if not text:
        raise ValueError("refusing to append an empty house note")

    path = notes_path(profile, content_root=content_root)
    date = today or datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [*read_lines(profile, content_root=content_root), f"- {date} — {text}"][-MAX_LINES:]

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uv run python -m gtm_core.house_notes",
        description="Append or read the operator's accumulated gate corrections.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_append = sub.add_parser("append", help="append one dated note")
    p_append.add_argument("--profile", required=True)
    p_append.add_argument("--line", required=True)

    p_list = sub.add_parser("list", help="print the notes, newest last")
    p_list.add_argument("--profile", required=True)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "append":
            print(json.dumps({"path": str(append(args.profile, args.line))}))
            return 0
        print(json.dumps({"notes": read_lines(args.profile)}, indent=2))
        return 0
    except ValueError as exc:
        print(f"[house-notes] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[house-notes] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
