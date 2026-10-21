from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .model import Adjudication

#: How many repair passes one row may have before the loop must stop. The cap is here,
#: in deterministic code, and NOT in the skill prompt — the skill runs the loop but this
#: module decides when it ends. A model asked to police its own retry budget is a model
#: that can be argued out of it.
REPAIR_ATTEMPT_CAP = 3


@dataclass
class RepairCandidate:
    """One row the repair loop may act on, or a refusal explaining why it may not."""

    record: Adjudication
    eligible: bool
    reason: str = ""


def repair_queue(
    records: Iterable[Adjudication], *, cap: int = REPAIR_ATTEMPT_CAP
) -> list[RepairCandidate]:
    """Rows needing another repair pass, and rows that must not get one.

    Three refusals, all deliberate:

    * **at the cap** — a row with ``repair_attempt >= cap`` is done. Three re-angles that
      still do not satisfy the rubric is not a copy problem the loop can fix; it is a row
      the loop should stop spending on. The verdict stands and the row drops.
    * **attempt count unknown** — ``repair_attempt is None`` means the record predates the
      repair loop and its true count is unrecoverable. Treating that as zero is how a row
      already at the cap silently restarts, so it is refused instead. The judge writes an
      explicit integer on every record, so this only ever bites a hand-written one.
    * **unscored** — no verdict means nothing to repair *toward*. Re-composing a row the
      judge could not read is guessing.

    A ``send`` verdict is simply not a candidate — there is nothing to fix.
    """
    out: list[RepairCandidate] = []
    for rec in records:
        if rec.unscored:
            out.append(RepairCandidate(rec, False, "unscored — no verdict to repair toward"))
            continue
        if rec.verdict == "send":
            continue
        if rec.repair_attempt is None:
            out.append(
                RepairCandidate(
                    rec,
                    False,
                    "repair_attempt not recorded — cannot prove this row is under the cap, "
                    "and assuming zero is how a row already at the cap restarts",
                )
            )
            continue
        if rec.repair_attempt >= cap:
            out.append(
                RepairCandidate(
                    rec,
                    False,
                    f"at the {cap}-attempt cap ({rec.repair_attempt}) — the verdict stands",
                )
            )
            continue
        out.append(RepairCandidate(rec, True, ""))
    return out
