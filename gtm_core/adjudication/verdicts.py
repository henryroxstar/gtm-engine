from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from ..signal_record import Verdict
from .defects import normalize_defect_class
from .model import Adjudication

#: Ranking order for verdicts. A drop never outranks a re-angle however high its score,
#: because the score answers "would they reply" and the verdict answers "should this be
#: sent at all" — the second question dominates.
_VERDICT_RANK = {"send": 0, "re-angle": 1, "drop": 2}


def rank(records: Iterable[Adjudication]) -> list[Adjudication]:
    """Send order: verdict first, then score, then a stable tiebreak on address."""
    return sorted(
        records,
        key=lambda a: (_VERDICT_RANK.get(a.verdict, 3), -int(a.score or 0), a.email),
    )


def novel_classes(records: Iterable[Adjudication], known: Iterable[str]) -> dict[str, int]:
    """Defect classes the reader named that no deterministic rule covers.

    The output is the candidate list for a new rule, and nothing else is. A class that
    IS in ``known`` recurring in a read is a separate and worse finding — it means a
    gate that should have caught it is inert, which happened on 2026-08-19 when a
    ``--signoff`` default silently disabled four CTA rules at once.
    """
    # Both sides through the one normaliser: rule ids are kebab-case, judge classes are
    # whatever the model typed, and they must compare equal or every rule looks inert.
    have = {normalize_defect_class(k) for k in known if k.strip()}
    counts: Counter[str] = Counter()
    for a in records:
        cls = normalize_defect_class(a.defect_class)
        if cls and cls not in have:
            counts[cls] += 1
    return dict(counts.most_common())


#: Severity order for collapsing several touches' verdicts into one row-level answer.
_VERDICT_SEVERITY = {Verdict.SEND: 0, Verdict.REANGLE: 1, Verdict.DROP: 2}


def worst_verdict(records: Sequence[Adjudication]) -> Adjudication:
    """The most severe record of several for one row — ``drop`` > ``re-angle`` > ``send``.

    A row is judged once per touch, so several records legitimately describe it. Keeping
    whichever arrived last makes the row-level verdict depend on iteration order, and the
    direction of that error matters: a defect found on one touch is a fact about the row,
    and a later clean touch does not retract it. Ties keep the FIRST of the tied records
    (``max`` only replaces on strictly-greater) — callers that need a specific touch to
    win a tie must order ``records`` themselves; this function does not look at ``touch``.
    """
    return max(records, key=lambda a: _VERDICT_SEVERITY.get(a.verdict, 0))
