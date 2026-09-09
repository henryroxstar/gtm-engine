"""Regenerate the export column map from the schema the code actually reads.

The map is a *document about code*, and hand-maintained it had drifted into being
wrong in the most expensive direction: it omitted all nine record and verdict columns
the enrollment gate blocks on, so a run that followed it exactly produced a list the
gate rejected — while the document looked complete.

Generated, it cannot omit a column that exists. Same shape as the skill codegen: a
`generate` that writes and a `check` that fails on drift, so CI notices before a reader
does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .prospects_consolidate import csv_map_markdown

__all__ = ["MAP_PATH", "generate", "check", "main"]

#: Rendered into the `prospect` skill's references, where the skill points readers.
MAP_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugin"
    / "skills"
    / "prospect"
    / "references"
    / "hubspot-csv-map.md"
)


def generate(path: Path | None = None) -> Path:
    target = path or MAP_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(csv_map_markdown(), encoding="utf-8")
    return target


def check(path: Path | None = None) -> bool:
    """True when the committed map matches what the schema would render."""
    target = path or MAP_PATH
    if not target.exists():
        return False
    return target.read_text(encoding="utf-8") == csv_map_markdown()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.schema_doc",
        description="Regenerate (or verify) the export column map from the code's schema.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed map is stale, instead of rewriting it",
    )
    p.add_argument("--path", type=Path, default=None)
    args = p.parse_args(argv)

    if args.check:
        if check(args.path):
            print("csv-map: in sync with the schema")
            return 0
        print(
            "csv-map: STALE — regenerate with `uv run python -m gtm_core.prospects schema-doc`",
            file=sys.stderr,
        )
        return 1
    print(f"wrote {generate(args.path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
