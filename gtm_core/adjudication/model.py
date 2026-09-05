from __future__ import annotations

from dataclasses import dataclass

#: The axes that actually change what the copy says. Seat decides what the body may
#: lead on; signal decides whether the opener can be dated at all; tier decides how
#: much research sits behind it; ``cell`` is the hook-matrix coordinate the copy was
#: WRITTEN against, and therefore the one axis that varies the argument itself rather
#: than who receives it.
#:
#: ``cell`` was added 2026-08-23 and the omission was costly. Copy is scoped per LIST,
#: so every recipient on one list gets the same body no matter their seat: without a
#: messaging axis the sampler was free to call a draw "stratified across seat, signal
#: and tier" while every row carried one of two arguments. The 2026-08-21 sheet did
#: exactly that — 30 rows, 4 seats, 2 segments, and **2 of the matrix's 74 cells**. Every
#: conclusion drawn from it about "the copy" or "the judge" was a conclusion about two
#: arguments, and nothing in the sample said so. ``collapsed_axes`` below reports an axis
#: that does not vary, which is the guard that makes a thin draw visible rather than
#: silent — it could not fire on an axis that was not being sampled at all.
DEFAULT_STRATA = ("seat", "signal", "tier", "cell")


@dataclass
class Adjudication:
    """One read email, recorded.

    ``score`` is 1-5 on the only question that matters before there is outcome data:
    would this recipient, in this seat, at this company, reply positively. It is a
    guess. It is recorded as a guess, with the reasoning attached, so that when replies
    do arrive the guesses can be scored against them — which is the point at which this
    stops being aesthetics.
    """

    email: str
    verdict: str  # send | re-angle | drop
    score: int  # 1-5
    stratum: str = ""
    touch: int = 1
    defect_class: str = ""
    evidence: str = ""
    note: str = ""

    #: The eval-sheet row identity — ``sha256(spec|csv|email|touch)[:16]``, the same key
    #: :func:`gtm_core.eval_calibration._row_id` computes. Carried here so a judge's
    #: prediction can be joined to a sealed holdout label without re-deriving it from
    #: four fields that must all match exactly. Empty on a hand-written record.
    row_id: str = ""
    #: sha256 of the exact body this verdict is about. A repair rewrites the body; without
    #: this, a stale verdict and a fresh one are indistinguishable on disk.
    body_hash: str = ""
    #: How many repair passes this row has already had. **``None`` means "not recorded",
    #: which is NOT the same as zero** — see :func:`repair_queue`. Every record the judge
    #: writes carries an explicit integer; only records predating the repair loop are None.
    repair_attempt: int | None = None
    #: True if this body was re-composed by the repair loop rather than authored cold.
    #: Repaired rows are excluded from the judge-validation holdout (labelling copy the
    #: judge shaped, then validating the judge on it, is circular) and reported as their
    #: own stratum so "is judge-shaped copy better or worse" stays a measurable number.
    repaired: bool = False
    #: True when the judge could not produce a verdict for this row. Recorded rather than
    #: dropped: a row that vanishes between input and output makes a partial batch look
    #: like a complete one, which is the whole R11 failure class.
    unscored: bool = False
    #: Which transport produced this verdict — ``"api"`` (one Anthropic request per row) or
    #: ``"sdk"`` (batched through the Agent SDK on the host's own auth). Empty on a
    #: hand-written record. Recorded because the two are NOT interchangeable, and a holdout
    #: scored across both would otherwise be a silent confound in every statistic.
    backend: str = ""
    #: How many emails shared this row's prompt. ``1`` means it was scored alone. The API
    #: path is always 1; the SDK path batches to amortise subprocess cost, which trades
    #: away some of the per-row independence the confusion matrix and Cohen's kappa assume.
    #: Recording the width is what keeps that trade measurable instead of assumed away.
    judge_batch: int = 1
    #: The DETERMINISTIC grounding pre-pass verdict for this body
    #: (:mod:`gtm_core.groundedness`), computed before the model saw the row and recorded
    #: alongside its guess. ``"clean"``, or a compact flag string such as
    #: ``"untraceable=97%;unhedged=1"``. ``"tier1=no-corpus"`` means the profile has no
    #: ``case-studies.md``, so the fabricated-number tier could not run — deliberately NOT
    #: the same as "every number is fabricated", which is what an empty corpus would
    #: otherwise report for every row carrying a figure. Empty on a record written before
    #: the cascade was wired, or by a hand-written row.
    #:
    #: This RANKS, it does not gate: `account_integrity --require-verdict send` remains
    #: what refuses a row. Recorded so that, once labels exist, the cheap tiers can be
    #: scored against outcomes with the same machinery as the judge itself.
    grounding: str = ""
    #: Whether a **sealed holdout existed for this profile** when the verdict was written —
    #: i.e. whether anything has ever measured this judge against human labels.
    #:
    #: ``None`` means "not recorded" (a record predating this field); ``False`` means the
    #: judge was demonstrably unvalidated at write time. **The two are not the same and
    #: neither is "fine".**
    #:
    #: Why this field exists: on 2026-08-27 a run scored 963 researched accounts, read the
    #: resulting ``drop`` verdicts as terminal, and reported "zero survived" — while no
    #: sealed holdout existed for ANY profile, so the judge had never once been checked
    #: against a human. Nothing in the payload distinguished those verdicts from validated
    #: ones, because nothing recorded the difference. `email-quality`'s SKILL.md already
    #: warned in prose that a bare rejection "usually means the copy is wrong... and reading
    #: it as 'bad account' would disqualify most of a healthy list" — prose the caller had
    #: not read. A property that only lives in a document a caller may skip is not a guard.
    calibrated: bool | None = None

    def to_dict(self) -> dict:
        """JSONL projection. ``repair_attempt`` is emitted even when None so a reader can
        tell "recorded as unknown" from "key absent because the writer predates the field"."""
        return {
            "email": self.email,
            "verdict": self.verdict,
            "score": self.score,
            "stratum": self.stratum,
            "touch": self.touch,
            "defect_class": self.defect_class,
            "evidence": self.evidence,
            "note": self.note,
            "row_id": self.row_id,
            "body_hash": self.body_hash,
            "repair_attempt": self.repair_attempt,
            "repaired": self.repaired,
            "unscored": self.unscored,
            "backend": self.backend,
            "judge_batch": self.judge_batch,
            "grounding": self.grounding,
            # Emitted even when None, for the same reason `repair_attempt` is: a reader must
            # be able to tell "recorded as unvalidated" from "key absent, writer predates it".
            "calibrated": self.calibrated,
        }
