"""Measured precision: what the graded members of a source say about the operator's stated prior.

The registry's ``precision`` is the operator's prior, a hand-read ``k/n``. The graded screen files
are the evidence: every member of a source that has been screened for agentic activity carries a
grade (A agents in operation, B AI but not agents, C neither, ``?`` could not be graded). A source
whose graded members fall below the bar goes inert whatever its prior says (``registry.inert_reason``).

Read-only. A grading file lives at ``<content>/<profile>/prospects/source-agentic-screen-*.csv``.
The identity of a graded row is ``(source, organisation)``: the same organisation graded twice for
one source with two different grades has no honest tally, so that source is left out and named.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import _safe_segment, resolve_content_root

GLOB = "source-agentic-screen-*.csv"
HIT = "A"
GRADES = frozenset({"A", "B", "C", "?"})
_MAX_ROWS = 20_000


@dataclass(frozen=True)
class Tally:
    hits: int
    graded: int

    @property
    def rate(self) -> float:
        return self.hits / self.graded

    def text(self) -> str:
        return f"{self.hits}/{self.graded}"


@dataclass
class Measured:
    by_source: dict[str, Tally] = field(default_factory=dict)
    ungraded: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def grading_files(profile: str, content_root: Path | None = None) -> list[Path]:
    root = content_root if content_root is not None else resolve_content_root()
    folder = root / _safe_segment(profile, "profile") / "prospects"
    return sorted(folder.glob(GLOB)) if folder.is_dir() else []


def _grade_column(header: list[str]) -> str | None:
    return next((h for h in header if h.strip().startswith("agentic_grade")), None)


def _read(path: Path, graded: dict, problems: list[str]) -> None:
    """Merge one file's rows into ``graded`` only if the whole file was read: a half-read file is a biased tally."""
    mine: dict = {}
    if _read_rows(path, mine, problems):
        for key, rows in mine.items():
            graded.setdefault(key, []).extend(rows)


def _read_rows(path: Path, graded: dict, problems: list[str]) -> bool:
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            col = _grade_column(reader.fieldnames or [])
            missing = [c for c in ("source", "organisation") if c not in (reader.fieldnames or [])]
            if col is None or missing:
                need = missing + ([] if col else ["agentic_grade"])
                problems.append(f"{path.name} has no {', '.join(need)} column, so it is not read.")
                return False
            for n, row in enumerate(reader, start=2):
                if n > _MAX_ROWS:
                    problems.append(
                        f"{path.name} has more than {_MAX_ROWS} rows, so it is not counted."
                    )
                    return False
                source = (row.get("source") or "").strip()
                org = " ".join((row.get("organisation") or "").split()).casefold()
                grade = (row.get(col) or "").strip().upper()
                if not source or not org or grade not in GRADES:
                    problems.append(
                        f"{path.name} line {n}: source, organisation and a grade of "
                        f"A, B, C or ? are all required, so the row is skipped."
                    )
                    continue
                graded.setdefault((source, org), []).append((grade, path.name, n))
        return True
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        problems.append(
            f"{path.name} could not be read ({type(exc).__name__}), so it is not counted."
        )
        return False


def measure(profile: str, content_root: Path | None = None) -> Measured:
    """Tally graded members per source. Never raises: a problem is named in ``problems``."""
    out = Measured()
    graded: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
    try:
        files = grading_files(profile, content_root)
    except (OSError, ValueError) as exc:
        out.problems.append(f"the grading folder could not be listed ({type(exc).__name__}).")
        return out
    for path in files:
        out.files.append(path.name)
        _read(path, graded, out.problems)
    conflicted: set[str] = set()
    per_source: dict[str, list[str]] = {}
    for (source, org), grades in sorted(graded.items()):
        if len({g for g, _, _ in grades} - {"?"}) > 1:
            conflicted.add(source)
            where = "; ".join(f"{g} ({f} line {n})" for g, f, n in grades)
            out.problems.append(
                f"source {source!r}: {org!r} is graded more than one way, {where}. "
                "No tally is made for this source until one grade is removed."
            )
            continue
        known = [g for g, _, _ in grades if g != "?"]
        if known:
            per_source.setdefault(source, []).append(known[0])
        else:
            out.ungraded[source] = out.ungraded.get(source, 0) + 1
    for source, grades in per_source.items():
        if source not in conflicted:
            out.by_source[source] = Tally(grades.count(HIT), len(grades))
    return out
