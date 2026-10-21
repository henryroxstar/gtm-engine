"""The judge's calibration record — what "calibrated" has to mean before a verdict may remove a row.

Until 2026-09-03 :func:`gtm_core.eval_calibration.is_calibrated` was a bare file-existence
check: *any* ``*-holdout.json`` under the profile's evals directory made every subsequent
judge record carry ``calibrated: true``, and ``account_integrity.filter_by_verdict`` obeys a
calibrated judge's ``drop``. On 2026-09-01 a holdout was sealed and the judge was scored
against it — and **failed all four bars** — which under the old rule made it *more* trusted,
not less. The live send list was spared only because it had been stamped before the holdout
existed.

This module gives ``score`` a place to persist its verdict, next to the holdout it scored,
bound to that holdout's exact bytes. ``calibrated`` now means *"the newest sealed holdout has
a score record that says PASS, and the holdout has not been edited since"*. Anything else —
no record, a record for an older holdout, a failed run, a provisional pass with the flip bar
unmeasured, a re-edited holdout — reads as **not calibrated**, which is the fail-safe
direction: an uncalibrated judge ranks and never removes.

Stdlib only. Deliberately imports nothing from :mod:`gtm_core.eval_calibration` so that
module can import this one without a cycle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Suffix appended to the holdout stem. ``eval-2026-09-01-holdout.json`` →
#: ``eval-2026-09-01-holdout-score.json``. Ends in ``-score.json``, so the
#: ``*-holdout.json`` glob that discovers sealed holdouts can never mistake a score record
#: for a holdout.
SCORE_SUFFIX = "-score.json"


def score_record_path(holdout: Path) -> Path:
    """Where ``score`` records its verdict for ``holdout`` — beside it, never elsewhere."""
    holdout = Path(holdout)
    stem = holdout.name[: -len(".json")] if holdout.name.endswith(".json") else holdout.name
    return holdout.with_name(stem + SCORE_SUFFIX)


def holdout_sha256(holdout: Path) -> str:
    """The exact bytes a score record is bound to. Editing the holdout invalidates the record."""
    return hashlib.sha256(Path(holdout).read_bytes()).hexdigest()


def write_score_record(
    holdout: Path,
    verdict: Any,
    *,
    n: int,
    provisional: bool,
    predictions: Path | str | None,
    reversed_predictions: Path | str | None = None,
    baseline_predictions: Path | str | None = None,
) -> Path:
    """Persist one ``score`` run. ``verdict`` is a ``JudgeVerdict`` (``passed``/``tpr``/``tnr``/
    ``kappa``/``flip``/``reasons``); duck-typed so this module stays import-free of the
    calibration harness.

    ``provisional`` is the CLI's own "PASS with an unmeasured flip rate" case. It is recorded
    as **not passed**: three bars cleared with the fourth never tested is exactly the state
    the flip bar exists to refuse, and a record that said ``passed: true`` here would let a
    coin-flip judge start removing rows.
    """
    holdout = Path(holdout)
    record = {
        "holdout": holdout.name,
        "holdout_sha256": holdout_sha256(holdout),
        "passed": bool(verdict.passed) and not provisional,
        "bars_passed": bool(verdict.passed),
        "provisional": bool(provisional),
        "n": int(n),
        "tpr": verdict.tpr,
        "tnr": verdict.tnr,
        "kappa": verdict.kappa,
        "flip": verdict.flip,
        "reasons": list(verdict.reasons or []),
        "predictions": str(predictions) if predictions else "",
        "reversed_predictions": str(reversed_predictions) if reversed_predictions else "",
        "baseline_predictions": str(baseline_predictions) if baseline_predictions else "",
        "ran_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    out = score_record_path(holdout)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out


def read_score_record(holdout: Path) -> dict | None:
    """The record for ``holdout``, or ``None`` when ``score`` has never run against it (or the
    file is unreadable — treated identically, because a record nobody can parse is not
    evidence of a pass)."""
    path = score_record_path(holdout)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def holdout_passed(holdout: Path) -> bool:
    """True iff ``holdout`` has a score record that says PASS, was not provisional, and was
    written against the holdout's current bytes. Every other state is ``False``."""
    holdout = Path(holdout)
    if not holdout.is_file():
        return False
    record = read_score_record(holdout)
    if not record or record.get("passed") is not True or record.get("provisional"):
        return False
    return record.get("holdout_sha256") == holdout_sha256(holdout)


def newest_holdout_passed(holdouts: list[Path]) -> bool:
    """``is_calibrated``'s rule: the NEWEST sealed holdout decides. ``holdouts`` is the list
    :func:`gtm_core.eval_calibration.sealed_holdouts` returns — newest first — so a pass
    recorded against an older holdout is not carried forward once a newer one is sealed."""
    return bool(holdouts) and holdout_passed(holdouts[0])


# --------------------------------------------------------------------- self-consistency


def kappa_from_pairs(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's κ over (a, b) boolean pairs — the same arithmetic ``eval_calibration.cohens_kappa``
    applies to labels × predictions, for any two boolean series."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def self_agreement(a: list, b: list) -> dict:
    """The judge against ITSELF: two adjudication runs joined on ``row_id``.

    Needs no oracle. ``row_id`` (not ``(email, touch)``) is the key because one person is
    legitimately judged in several cells with different bodies — nine sweep files carried 43
    such duplicate units — while ``row_id`` is unique within a run. A joined id whose two
    records carry different non-empty ``body_hash`` values is refused: that is not the same
    email judged twice. Unscored, verdict-less and repaired records are skipped and counted,
    exactly as the holdout scorer skips them.

    Returns ``agreement_3way``, ``agreement_binary`` (``send`` vs not — the pre-registered
    operational collapse), ``kappa_binary``, the 3×3 ``transitions`` and the disagreeing ids.
    """

    def index(records: list, side: str) -> tuple[dict, int]:
        out: dict[str, object] = {}
        skipped = 0
        for rec in records:
            if (
                rec.unscored
                or rec.verdict not in ("send", "re-angle", "drop")
                or rec.repaired
                or not rec.row_id
            ):
                skipped += 1
                continue
            if rec.row_id in out:
                raise ValueError(
                    f"row_id {rec.row_id} appears twice on side {side} — a run judges a row once; "
                    "merge the files by run, not by cell, or drop the duplicate"
                )
            out[rec.row_id] = rec
        return out, skipped

    ia, skipped_a = index(a, "a")
    ib, skipped_b = index(b, "b")
    joined = sorted(set(ia) & set(ib))
    changed = [
        rid
        for rid in joined
        if ia[rid].body_hash and ib[rid].body_hash and ia[rid].body_hash != ib[rid].body_hash
    ]
    if changed:
        raise ValueError(
            f"{len(changed)} row(s) carry different bodies on the two sides (e.g. {changed[0]}) — "
            "that is two emails, not one email judged twice; re-render before comparing"
        )
    transitions: dict[str, int] = {}
    pairs: list[tuple[bool, bool]] = []
    same3 = 0
    disagreed = []
    for rid in joined:
        va, vb = ia[rid].verdict, ib[rid].verdict
        transitions[f"{va}->{vb}"] = transitions.get(f"{va}->{vb}", 0) + 1
        pairs.append((va == "send", vb == "send"))
        if va == vb:
            same3 += 1
        else:
            disagreed.append(rid)
    n = len(joined)
    return {
        "n_a": len(a),
        "n_b": len(b),
        "joined": n,
        "skipped": skipped_a + skipped_b,
        "agreement_3way": (same3 / n) if n else None,
        "agreement_binary": (sum(1 for x, y in pairs if x == y) / n) if n else None,
        "kappa_binary": kappa_from_pairs(pairs),
        "transitions": dict(sorted(transitions.items())),
        "disagreed_row_ids": disagreed,
    }
