from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .model import Adjudication


def _unit(rec: Adjudication) -> tuple[str, int]:
    """The (recipient, touch) identity every keying in this module already trusts."""
    return ((rec.email or "").strip().lower(), int(rec.touch or 1))


def unstable_row_id(records: Sequence[Adjudication]) -> str:
    """ "" if ``row_id`` is a usable grouping key for these records, else the refusal.

    NOT a uniqueness test — on one legitimate judging run every ``row_id`` IS unique and
    that is correct; a uniqueness test would refuse the common case. The real test is
    whether ``row_id`` agrees with the (email, touch) identity :func:`write-verdicts`
    already uses to collapse a row. On a single run both partitions are identical, so
    this cannot false-positive there. On several independent runs over the same people,
    ``row_id`` — ``sha256(spec|csv|email|touch)[:16]``, an eval-SHEET row identity, see
    :func:`gtm_core.eval_calibration._row_id` — differs per run even for the same
    recipient, so it splits one (email, touch) across several ``row_id``s.

    This is the check that would have caught the 2026-08-30 pool-599 incident: grouping
    1298 records (12 runs over 599 recipients) by raw ``row_id`` produced 1298 singleton
    groups, each trivially unanimous with itself, printing "flip rate 0.0%, ties 0" over
    a pool where 36% of recipients had in fact changed verdict on an identical body.
    """
    ids_by_unit: dict[tuple[str, int], set[str]] = defaultdict(set)
    units_by_id: dict[str, set[tuple[str, int]]] = defaultdict(set)
    blank = populated = 0
    for rec in records:
        rid = (rec.row_id or "").strip()
        unit = _unit(rec)
        if rid:
            populated += 1
            ids_by_unit[unit].add(rid)
            units_by_id[rid].add(unit)
        else:
            blank += 1

    # One row_id spanning more than one recipient is impossible from the real hash —
    # email is inside sha256(spec|csv|email|touch) — so this means the input was edited.
    forged = sorted(rid for rid, units in units_by_id.items() if len(units) > 1)
    if forged:
        return (
            f"{len(forged)} row_id(s) span more than one (email, touch), e.g. {forged[0]!r} "
            f"— impossible from gtm_core.eval_calibration._row_id since email is part of "
            f"the hash. The input has been edited or corrupted."
        )

    split = sorted(unit for unit, ids in ids_by_unit.items() if len(ids) > 1)
    if split:
        email, touch = split[0]
        examples = ", ".join(sorted(ids_by_unit[split[0]]))
        return (
            f"row_id is NOT stable across these {len(records)} record(s) — {len(split)} of "
            f"{len(ids_by_unit)} (email, touch) unit(s) carry more than one row_id, e.g. "
            f"{email!r} touch {touch} -> {examples}. row_id is an eval-sheet row identity "
            f"(sha256(spec|csv|email|touch)[:16]), not a recipient identity: two independent "
            f"judging runs over the same person mint two different ids and never collide. "
            f"Group by (email, touch) instead."
        )

    if blank and populated:
        return (
            f"{blank} of {blank + populated} record(s) carry no row_id while the rest do — "
            f"a `row_id or email` fallback would key those two sets in different namespaces "
            f"and split a recipient present in both. Group by (email, touch) instead."
        )
    return ""


def unstable_bodies(records: Sequence[Adjudication]) -> list[tuple[str, int]]:
    """(email, touch) unit(s) whose records carry more than one ``body_hash``.

    Grouping by (email, touch) is only sound evidence of *judge* disagreement if every
    run judged the *same* body. A unit with several body_hash values is comparing
    verdicts on different emails, not measuring re-judging variance — this must be a
    hard refusal, not a reported "split", or the tool reports a wrong number instead of
    a wrong-but-visibly-flagged one.
    """
    by_unit: dict[tuple[str, int], set[str]] = defaultdict(set)
    for rec in records:
        h = (rec.body_hash or "").strip()
        if h:
            by_unit[_unit(rec)].add(h)
    return sorted(unit for unit, hashes in by_unit.items() if len(hashes) > 1)
