from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .defects import normalize_defect_class
from .model import Adjudication


@dataclass
class Completeness:
    """Did the judge actually score every row it was handed?"""

    rows: int = 0
    records: int = 0
    scored: int = 0
    unscored: int = 0
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing and self.records == self.rows


def completeness(rows: Sequence[dict], records: Sequence[Adjudication]) -> Completeness:
    """Per-row coverage of a judging batch — the R11 guard.

    ``coverage`` above answers "which *strata* were read", which a partial batch can
    satisfy while missing a third of the list. This answers the different and blunter
    question: is there a record for every row. A batch that scored 380 of 400 prints
    exactly like one that scored 400 unless something counts, and nothing did.

    A row the judge could not parse must appear as an ``unscored`` record, not vanish —
    so an unscored record still counts toward ``records`` and is reported separately.
    """
    by_email: dict[str, Adjudication] = {}
    for rec in records:
        key = (rec.email or "").strip().lower()
        if key:
            by_email[key] = rec
    missing = [
        (r.get("email") or "").strip().lower()
        for r in rows
        if (r.get("email") or "").strip().lower() not in by_email
    ]
    return Completeness(
        rows=len(rows),
        records=len(records),
        scored=sum(1 for a in records if not a.unscored),
        unscored=sum(1 for a in records if a.unscored),
        missing=sorted(m for m in missing if m),
    )


def covered_classes(records: Iterable[Adjudication], known: Iterable[str]) -> dict[str, int]:
    """Named classes that a rule already covers — i.e. evidence a gate is inert."""
    have = {normalize_defect_class(k) for k in known if k.strip()}
    counts: Counter[str] = Counter()
    for a in records:
        cls = normalize_defect_class(a.defect_class)
        if cls and cls in have:
            counts[cls] += 1
    return dict(counts.most_common())
