"""Adjudication — the reading pass, as a ranker rather than a gate.

Forty-three deterministic rules guard this pipeline. Every defect class they encode
was originally found the same way: a human read the emails and noticed something. The
rules then made that class **non-recurring**. None of them has ever found a *new* one,
and none of them can: a regex knows the shapes it was given.

So the arrangement in force through August 2026 had it backwards — generation by a
model that can read, verification by patterns that cannot. Three review rounds each
surfaced novel classes the gates had no opinion about (proof mapped to the wrong
mechanism, an offer whose tone read as cocky, a seat problem the recipient does not
actually have), and each round ended by encoding those classes as more regex, which
guaranteed a fourth round would find different ones. An aesthetic loop with no
measurement has no fixed point.

This module does not add a forty-fourth rule. It makes the reading pass a *first-class
step with the properties the gates already have* — deterministic sampling, a recorded
verdict per item, and an explicit statement of what was NOT read:

* :func:`stratify` / :func:`sample` — a covering sample across the axes that actually
  vary the copy (seat, signal-vs-generic, tier), chosen by a stable hash so the same
  list yields the same sample on every run and two reviewers can be compared. Reading
  the first fifty rows of a file sorted by company name is not a sample of anything.
* :func:`coverage` — which strata the read touched and which it did not. "I reviewed
  50 emails" and "the list is reviewed" are different claims; without this, the first
  gets reported as the second.
* :func:`rank` — send order. The reading produces a score, the score picks which 30 go
  out first, and shipping 30 with an outcome attached is worth more than a fourth
  review round.
* :func:`novel_classes` — defect classes the reader named that no existing rule covers.
  These, and only these, are the candidates for becoming rule forty-four. A class the
  rules already cover recurring in a read means a gate is inert, which is a different
  and more urgent bug.

**This is not a gate and must not become one.** Its verdicts rank and record; they
never block, because a blocking check whose author is a language model is a check that
can be argued with, and the whole value of the deterministic tier is that it cannot.
The two tiers do different jobs: rules prevent recurrence, reading finds what recurs.

Stdlib-only, no I/O beyond JSONL read/write on paths the caller supplies.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .signal_record import JUDGE_COLUMNS, Verdict

__all__ = [
    "DEFAULT_STRATA",
    "Adjudication",
    "stratum_of",
    "stratify",
    "collapsed_axes",
    "sample",
    "coverage",
    "rank",
    "novel_classes",
    "read_records",
    "write_records",
    "repair_queue",
    "REPAIR_ATTEMPT_CAP",
    "completeness",
    "worst_verdict",
    "Disposal",
    "disposal_audit",
    "TARGETING_DEFECTS",
    "main",
]

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


def stratum_of(row: dict, axes: Sequence[str] = DEFAULT_STRATA) -> str:
    """The stratum key for a row: one value per axis, joined.

    ``signal`` is derived rather than read from a column — a row either has a clause
    that can open a dated line or it does not, and that is the axis, not whatever the
    column happens to be named this month.
    """
    parts = []
    for axis in axes:
        if axis == "signal":
            parts.append("signal" if (row.get("signal_clause") or "").strip() else "generic")
        else:
            parts.append((row.get(axis) or "-").strip() or "-")
    return "|".join(parts)


def stratify(rows: Iterable[dict], axes: Sequence[str] = DEFAULT_STRATA) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[stratum_of(r, axes)].append(r)
    return dict(out)


def collapsed_axes(rows: Sequence[dict], axes: Sequence[str] = DEFAULT_STRATA) -> list[str]:
    """Axes that do not vary across the list — either absent from the schema, or
    constant.

    A collapsed axis is not an error (a per-seat list has one seat by construction),
    but it silently shrinks what the sample covers, and an unreported one turns "I
    sampled across seat, signal and tier" into a claim about one axis wearing three
    names.
    """
    if not rows:
        return list(axes)
    out = []
    for axis in axes:
        values = {stratum_of(r, [axis]) for r in rows}
        if len(values) <= 1:
            out.append(axis)
    return out


def _stable_key(row: dict, seed: str = "") -> str:
    """A deterministic per-row ordering key.

    Not ``random.shuffle(seed=...)``: that reorders under a Python version change, and
    a sample nobody can reproduce is a sample nobody can check. A hash of the row's own
    identity is stable across machines, runs, and years.

    ``seed`` varies WHICH exemplar a stratum contributes without making the draw
    unreproducible. Both properties are wanted and they pull against each other: a fixed
    key means consecutive evals re-read the same rows and never exercise the rest of the
    list, while true randomness means a sheet cannot be rebuilt or two labelers compared.
    Mixing a caller-chosen seed into the hash gives variety ACROSS evals and determinism
    WITHIN one — pass the campaign plus the sheet date and every sheet is reproducible
    forever while no two sheets read the same exemplars.
    """
    ident = (row.get("email") or row.get("company") or "").strip().lower()
    return hashlib.sha256(f"{seed}|{ident}".encode()).hexdigest()


def sample(
    rows: Sequence[dict], n: int, axes: Sequence[str] = DEFAULT_STRATA, seed: str = ""
) -> list[dict]:
    """A covering sample of ``n`` rows: every stratum gets one before any gets two.

    Round-robin across strata rather than proportional allocation, on purpose. A
    proportional sample of a list that is 80% one seat spends 80% of a scarce reading
    budget re-reading that seat's single template, and the rare stratum — where the
    copy is least exercised and most likely wrong — goes unread. Once every stratum has
    one, the round-robin naturally weights the large ones anyway.

    Because ``DEFAULT_STRATA`` now carries ``cell``, that same one-before-two rule spreads
    the draw across the hook-matrix coordinates present in ``rows`` — the sampler maximises
    argument variety for free, and needs no cell-specific logic to do it. What it cannot do
    is invent coverage: a draw can only span the cells the input actually contains, so
    breadth is bought upstream by drafting across more cells, not here.
    """
    if n <= 0 or not rows:
        return []
    buckets = stratify(rows, axes)
    for key in buckets:
        buckets[key].sort(key=lambda r: _stable_key(r, seed))
    order = sorted(buckets)
    picked: list[dict] = []
    depth = 0
    while len(picked) < n:
        added = False
        for key in order:
            if depth < len(buckets[key]):
                picked.append(buckets[key][depth])
                added = True
                if len(picked) == n:
                    return picked
        if not added:
            break  # every stratum exhausted: the list is smaller than n
        depth += 1
    return picked


@dataclass
class Coverage:
    strata: int = 0
    read: int = 0
    rows: int = 0
    adjudicated: int = 0
    unread_strata: list[str] = field(default_factory=list)

    @property
    def share(self) -> float:
        return (self.read / self.strata) if self.strata else 0.0


def coverage(
    rows: Sequence[dict],
    records: Sequence[Adjudication],
    axes: Sequence[str] = DEFAULT_STRATA,
) -> Coverage:
    """What the read actually covered — and, more usefully, what it did not."""
    buckets = stratify(rows, axes)
    by_email = {(r.get("email") or "").strip().lower(): stratum_of(r, axes) for r in rows}
    seen = {by_email.get((a.email or "").strip().lower(), "") for a in records}
    seen.discard("")
    unread = sorted(set(buckets) - seen)
    return Coverage(
        strata=len(buckets),
        read=len(buckets) - len(unread),
        rows=len(rows),
        adjudicated=len({a.email for a in records}),
        unread_strata=unread,
    )


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
    have = {k.strip().lower() for k in known if k.strip()}
    counts: Counter[str] = Counter()
    for a in records:
        cls = (a.defect_class or "").strip().lower()
        if cls and cls not in have:
            counts[cls] += 1
    return dict(counts.most_common())


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


#: Defect classes that are properties of TARGETING, not of copy. A verdict carrying one is a
#: statement about which argument was aimed at this account — not about whether the account is
#: contactable — so it can never be terminal on its own.
#:
#: `PENDING.md` §"Route fact_creates_problem / right_person findings to the prospect skill":
#: *"they are targeting properties, not copy defects. Fit moves persona scores ~3 points, copy
#: ~1. Without this the loop polishes emails while the lever sits upstream."*
#:
#: Named here because the judge does not reliably honour its own verdict vocabulary: on
#: 2026-08-27 all 9 rejections came back ``drop`` (defined in the judge's prompt as "should not
#: be contacted at all") while every one of their notes described a re-angle — "the email
#: assumes X has a problem but provides no evidence". The verdict word said terminal; the
#: defect class said mis-aimed. A caller reading only the word retires a healthy account.
TARGETING_DEFECTS = frozenset({"fact_creates_problem", "right_person"})


@dataclass
class Disposal:
    """Where a scored batch's rows actually went. See :func:`disposal_audit`."""

    total: int = 0
    send: int = 0
    re_angle: int = 0
    drop: int = 0
    unscored: int = 0
    #: ``re-angle`` rows still under the attempt cap — a live queue, by definition.
    repairable: int = 0
    #: ``re-angle`` rows the caller has not accounted for anywhere. The number this exists
    #: to surface.
    stranded: int = 0
    #: ``drop`` rows whose defect is a :data:`TARGETING_DEFECTS` class — a mis-aimed argument
    #: recorded with a terminal-sounding word. These are re-angles in all but name.
    misfiled_drop: int = 0
    #: The subset of :attr:`misfiled_drop` the caller has not filed anywhere. Only this
    #: raises a finding; a misfiled drop that reached a re-angle queue has been handled.
    misfiled_unfiled: int = 0
    uncalibrated: int = 0
    findings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.findings = self.findings or []

    @property
    def failed(self) -> bool:
        return bool(self.findings)


def disposal_audit(
    records: Iterable[Adjudication],
    *,
    accounted_for: Collection[str] = (),
    cap: int = REPAIR_ATTEMPT_CAP,
) -> Disposal:
    """What happened to the rows this batch did NOT send — the count a summary omits.

    ``accounted_for`` is the set of emails the caller can name a destination for: queued for
    repair, enrolled, or deliberately retired to the suppression ledger. A ``re-angle`` row
    in none of those has been **stranded** — silently discarded by a caller that then
    reported the batch's ``send`` count as its result.

    Why this exists, stated plainly because the failure is cheap to repeat: on 2026-08-27 a
    run scored 963 researched accounts, produced 480 ``re-angle`` verdicts, dropped every one
    of them without queueing any, and reported "zero survived". :class:`Verdict` defines
    ``re-angle`` as *"the account is real and the seat is right, but this clause cannot carry
    the pitch — goes back to research, not to the sequencer"*, and `email-quality`'s SKILL.md
    warns that reading a bare rejection as a bad account "would disqualify most of a healthy
    list". Both were already written down. Neither was *counted*, so neither fired, and the
    run's own summary read as a clean negative result rather than as 480 unfiled rows.

    A summary that reports only what passed is indistinguishable from one where nothing was
    salvageable. This makes the difference a number.
    """
    seen = {e.strip().lower() for e in accounted_for if e and e.strip()}
    d = Disposal()
    stranded_examples: list[str] = []
    misfiled_examples: list[str] = []
    for rec in records:
        d.total += 1
        if rec.calibrated is False:
            d.uncalibrated += 1
        if rec.unscored:
            d.unscored += 1
            continue
        if rec.verdict == "send":
            d.send += 1
            continue
        if rec.verdict == "drop":
            d.drop += 1
            if (rec.defect_class or "").strip().lower() in TARGETING_DEFECTS:
                d.misfiled_drop += 1
                # Counted always (the miscategorisation is worth seeing even once handled),
                # but only a FINDING while the row is unfiled — the check is about accounting,
                # not volume, exactly as it is for a stranded re-angle.
                if rec.email.strip().lower() not in seen:
                    d.misfiled_unfiled += 1
                    if len(misfiled_examples) < 3:
                        misfiled_examples.append(f"{rec.email} ({rec.defect_class})")
            continue
        if rec.verdict != "re-angle":
            continue
        d.re_angle += 1
        if rec.repair_attempt is not None and rec.repair_attempt < cap:
            d.repairable += 1
        if rec.email.strip().lower() not in seen:
            d.stranded += 1
            if len(stranded_examples) < 3:
                stranded_examples.append(rec.email)

    if d.stranded:
        d.findings.append(
            f"{d.stranded}/{d.re_angle} re-angle row(s) are stranded — rejected by the judge "
            f"and then filed nowhere (not queued for repair, not enrolled, not suppressed). "
            f"re-angle means the ACCOUNT is right and the ARGUMENT is not; these are a work "
            f"queue, not a result. Route them (repair_queue, or re-aim the argument) or "
            f"retire them explicitly to the suppression ledger — do not report this batch's "
            f"send count as its outcome while they are unfiled. e.g. "
            f"{', '.join(stranded_examples)}"
        )
    if d.misfiled_unfiled:
        d.findings.append(
            f"{d.misfiled_unfiled}/{d.drop} 'drop' verdict(s) carry a TARGETING defect "
            f"({'/'.join(sorted(TARGETING_DEFECTS))}) — the argument was mis-aimed at this "
            f"account, which is a re-angle, not a do-not-contact. Per PENDING.md these route "
            f"to the `prospect` skill (re-target), never to the copy gate. Treating them as "
            f"terminal retires accounts whose only fault is the pitch you chose. e.g. "
            f"{', '.join(misfiled_examples)}"
        )
    if d.uncalibrated:
        d.findings.append(
            f"{d.uncalibrated}/{d.total} verdict(s) were written by an UNCALIBRATED judge "
            f"(no sealed holdout existed). These rank; they do not decide. Do not retire an "
            f"account on one."
        )
    return d


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
    have = {k.strip().lower() for k in known if k.strip()}
    counts: Counter[str] = Counter()
    for a in records:
        cls = (a.defect_class or "").strip().lower()
        if cls and cls in have:
            counts[cls] += 1
    return dict(counts.most_common())


# --- I/O -----------------------------------------------------------------


def read_records(path: Path) -> list[Adjudication]:
    out: list[Adjudication] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        # `repair_attempt` is read with a sentinel, never `int(... or 0)`: a record that
        # never recorded the field is genuinely unknown, and defaulting it to 0 would
        # restart a row already at the cap — the unbounded-loop bug in disguise.
        raw_attempt = d.get("repair_attempt")
        # Same sentinel discipline as `repair_attempt`, for the same reason: a record written
        # before this field existed is UNKNOWN, not "calibrated=False". Coercing unknown to
        # False would invent a measurement nobody made — the mirror of the bug the field is
        # here to stop, which was reading an unmeasured verdict as a measured one.
        raw_calibrated = d.get("calibrated")
        out.append(
            Adjudication(
                email=d.get("email", ""),
                verdict=(d.get("verdict") or "").strip().lower(),
                score=int(d.get("score") or 0),
                stratum=d.get("stratum", ""),
                touch=int(d.get("touch") or 1),
                defect_class=d.get("defect_class", ""),
                evidence=d.get("evidence", ""),
                note=d.get("note", ""),
                row_id=d.get("row_id", ""),
                body_hash=d.get("body_hash", ""),
                repair_attempt=(None if raw_attempt is None else int(raw_attempt)),
                repaired=bool(d.get("repaired", False)),
                unscored=bool(d.get("unscored", False)),
                backend=d.get("backend", ""),
                judge_batch=int(d.get("judge_batch") or 1),
                calibrated=(None if raw_calibrated is None else bool(raw_calibrated)),
            )
        )
    return out


def write_records(records: Iterable[Adjudication], path: Path) -> Path:
    """Write adjudication records as JSONL. Round-trips through :func:`read_records`."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(a.to_dict()) + "\n" for a in records),
        encoding="utf-8",
    )
    return out


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_fieldnames(path: Path) -> list[str]:
    """The CSV's declared header, which is not the same as ``rows[0]``'s keys."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh).fieldnames or [])


#: Severity order for collapsing several touches' verdicts into one row-level answer.
_VERDICT_SEVERITY = {Verdict.SEND: 0, Verdict.REANGLE: 1, Verdict.DROP: 2}


def worst_verdict(records: Sequence[Adjudication]) -> Adjudication:
    """The most severe record of several for one row — ``drop`` > ``re-angle`` > ``send``.

    A row is judged once per touch, so several records legitimately describe it. Keeping
    whichever arrived last makes the row-level verdict depend on iteration order, and the
    direction of that error matters: a defect found on one touch is a fact about the row,
    and a later clean touch does not retract it. Ties keep the last, which is the freshest
    reading of the same severity.
    """
    return max(records, key=lambda a: _VERDICT_SEVERITY.get(a.verdict, 0))


def _known_from(args) -> list[str]:
    known = list(args.known or [])
    if args.known_file:
        known += [
            ln.strip() for ln in Path(args.known_file).read_text(encoding="utf-8").splitlines()
        ]
    return known


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.adjudication",
        description=(
            "The reading pass as a ranker: sample a list so the read covers it, record "
            "a verdict per email, rank the send order, and separate defect classes that "
            "need a NEW rule from ones an existing rule should already have caught. "
            "Never a gate — nothing here blocks a send."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sample", help="pick a covering sample to read")
    sp.add_argument("--csv", required=True, type=Path)
    sp.add_argument("-n", type=int, default=30)
    sp.add_argument("--axes", default=",".join(DEFAULT_STRATA))
    sp.add_argument("--out", type=Path, help="write the sample as CSV (default: stdout summary)")

    cp = sub.add_parser("coverage", help="what the read covered, and what it did not")
    cp.add_argument("--csv", required=True, type=Path)
    cp.add_argument("--records", required=True, type=Path)
    cp.add_argument("--axes", default=",".join(DEFAULT_STRATA))

    rp = sub.add_parser("rank", help="send order from the recorded verdicts")
    rp.add_argument("--records", required=True, type=Path)
    rp.add_argument("--top", type=int, default=0, help="print only the first N")

    np_ = sub.add_parser("novel", help="classes that need a new rule vs. an inert one")
    np_.add_argument("--records", required=True, type=Path)
    np_.add_argument("--known", action="append", default=[], metavar="RULE")
    np_.add_argument("--known-file", type=Path, help="one rule name per line")

    qp = sub.add_parser(
        "repair-queue",
        help="rows needing another repair pass — and the ones that must NOT get one",
    )
    qp.add_argument("--records", required=True, type=Path)
    qp.add_argument("--cap", type=int, default=REPAIR_ATTEMPT_CAP)
    qp.add_argument("--out", type=Path, help="write the eligible rows as JSONL")

    dp = sub.add_parser(
        "disposal",
        help="where the NON-send rows went — fails on stranded re-angles and misfiled drops",
    )
    dp.add_argument("--records", required=True, type=Path, nargs="+")
    dp.add_argument(
        "--accounted-for",
        type=Path,
        help="file of emails (one per line) that were queued, enrolled, or explicitly retired",
    )
    dp.add_argument("--cap", type=int, default=REPAIR_ATTEMPT_CAP)

    cop = sub.add_parser(
        "check-complete",
        help="fail unless there is a record for EVERY row — the partial-batch guard",
    )
    cop.add_argument("--csv", required=True, type=Path)
    cop.add_argument("--records", required=True, type=Path)

    wv = sub.add_parser(
        "write-verdicts",
        help="write recorded verdicts into a CSV's judge_verdict columns (never `verdict`, "
        "which is the researcher's)",
    )
    wv.add_argument("--csv", required=True, type=Path)
    wv.add_argument("--records", required=True, type=Path)
    wv.add_argument("--out", required=True, type=Path)
    wv.add_argument(
        "--profile",
        default="",
        help="stamp judge_calibrated from this profile's sealed holdouts; omitted leaves it "
        "blank, which reads as 'never checked' rather than 'checked and failed'",
    )

    args = p.parse_args(argv)
    axes = (
        tuple(a.strip() for a in getattr(args, "axes", "").split(",") if a.strip())
        or DEFAULT_STRATA
    )

    if args.cmd == "sample":
        rows = _read_rows(args.csv)
        picked = sample(rows, args.n, axes)
        buckets = stratify(rows, axes)
        print(f"{len(rows)} row(s) in {len(buckets)} stratum/strata; sampled {len(picked)}")
        flat = collapsed_axes(rows, axes)
        if flat:
            print(
                "  note: "
                + ", ".join(flat)
                + " does not vary on this list (absent or constant) — the sample covers "
                "fewer dimensions than the axes suggest"
            )
        for key in sorted(buckets):
            got = sum(1 for r in picked if stratum_of(r, axes) == key)
            print(f"  {key:40} {got:3}/{len(buckets[key])}")
        if args.out and picked:
            with args.out.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(picked)
            print(f"\nwrote {args.out}")
        return 0

    if args.cmd == "coverage":
        c = coverage(_read_rows(args.csv), read_records(args.records), axes)
        print(
            f"coverage — {c.adjudicated} email(s) read across {c.read}/{c.strata} strata "
            f"({c.share:.0%}), {c.rows} row(s) in the list"
        )
        for key in c.unread_strata:
            print(f"  UNREAD  {key}")
        if not c.unread_strata:
            print("  every stratum read")
        return 0

    if args.cmd == "rank":
        ordered = rank(read_records(args.records))
        shown = ordered[: args.top] if args.top else ordered
        by_verdict = Counter(a.verdict for a in ordered)
        print(
            f"{len(ordered)} adjudicated — " + ", ".join(f"{k}={v}" for k, v in by_verdict.items())
        )
        for i, a in enumerate(shown, 1):
            cls = f"  [{a.defect_class}]" if a.defect_class else ""
            print(f"  {i:3}. {a.score} {a.verdict:9} {a.email}{cls}")
        return 0

    if args.cmd == "disposal":
        recs: list[Adjudication] = []
        for p in args.records:
            recs.extend(read_records(p))
        accounted: list[str] = []
        if args.accounted_for:
            accounted = [
                ln.strip()
                for ln in args.accounted_for.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")
            ]
        d = disposal_audit(recs, accounted_for=accounted, cap=args.cap)
        print(
            f"{d.total} record(s): send={d.send} re-angle={d.re_angle} drop={d.drop} "
            f"unscored={d.unscored}"
        )
        print(
            f"  repairable={d.repairable} stranded={d.stranded} "
            f"misfiled_drop={d.misfiled_drop} (unfiled {d.misfiled_unfiled}) "
            f"uncalibrated={d.uncalibrated}"
        )
        for f in d.findings:
            print(f"\nFINDING: {f}")
        if d.failed:
            print("\nFAIL — the non-send rows are not accounted for")
            return 1
        print("\nPASS")
        return 0

    if args.cmd == "repair-queue":
        queue = repair_queue(read_records(args.records), cap=args.cap)
        eligible = [c for c in queue if c.eligible]
        refused = [c for c in queue if not c.eligible]
        print(f"{len(eligible)} row(s) eligible for repair, {len(refused)} refused")
        for c in eligible:
            print(f"  REPAIR   attempt {c.record.repair_attempt} {c.record.email}")
        for c in refused:
            print(f"  REFUSED  {c.record.email}: {c.reason}")
        if args.out:
            write_records([c.record for c in eligible], args.out)
            print(f"\nwrote {args.out}")
        return 0

    if args.cmd == "check-complete":
        rows = _read_rows(args.csv)
        c = completeness(rows, read_records(args.records))
        print(
            f"{c.records} record(s) for {c.rows} row(s) — {c.scored} scored, {c.unscored} unscored"
        )
        if c.missing:
            print(f"\nFAIL — {len(c.missing)} row(s) have NO record at all:")
            for email in c.missing[:20]:
                print(f"  MISSING  {email}")
            if len(c.missing) > 20:
                print(f"  ... and {len(c.missing) - 20} more")
            print(
                "\nA batch that scored some of the list prints like one that scored all of "
                "it. That is why this check exists — do not proceed on a partial batch."
            )
            return 1
        print("PASS — every row has a record")
        return 0

    if args.cmd == "write-verdicts":
        rows = _read_rows(args.csv)
        records = read_records(args.records)
        c = completeness(rows, records)
        if c.missing:
            print(
                f"refusing to write verdicts: {len(c.missing)} of {c.rows} row(s) have no "
                f"record. Run `check-complete` — a partially-judged list must not be "
                f"written as a judged one.",
                file=sys.stderr,
            )
            return 1
        # Accumulate, never a dict comprehension. One address legitimately carries several
        # records — one per touch — and keying the other way silently kept whichever
        # happened to be last, so the verdict the enrollment gate read was the last
        # touch's rather than the list's. Worst wins: a drop found on touch 3 is a fact
        # about the row, and a send on touch 4 does not retract it.
        by_email: dict[str, list[Adjudication]] = {}
        for a in records:
            if a.unscored:
                continue
            by_email.setdefault((a.email or "").strip().lower(), []).append(a)

        # Imported here, not at module scope: `eval_calibration` imports this module, so a
        # top-level import would be a cycle. (`agent/mcp/judge/server.py` has no such
        # constraint and imports it normally.)
        from .eval_calibration import is_calibrated

        calibrated = is_calibrated(args.profile) if args.profile else None
        written = 0
        for row in rows:
            recs = by_email.get((row.get("email") or "").strip().lower())
            if not recs:
                continue
            rec = worst_verdict(recs)
            # The judge's own columns. `verdict` is the researcher's and is never touched
            # here — one column with two writers is how "the judge ranks and never blocks"
            # became false of the artifact while staying true in three documents.
            row["judge_verdict"] = rec.verdict
            # A reason nobody can act on is the same as none — prefer the evidence phrase.
            row["judge_verdict_reason"] = rec.evidence or rec.note or rec.defect_class
            row["judge_calibrated"] = "" if calibrated is None else str(bool(calibrated)).lower()
            written += 1

        # From the reader, not from `rows[0]`: a first row that happens to be missing an
        # optional key would silently truncate every other row's columns.
        fieldnames = list(_read_fieldnames(args.csv))
        for col in JUDGE_COLUMNS:
            if col not in fieldnames:
                fieldnames.append(col)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({c: (row.get(c) or "") for c in fieldnames})
        print(f"wrote {written} judge verdict(s) into {len(rows)} row(s) -> {args.out}")
        if calibrated is None:
            print(
                "  judge_calibrated is blank: no --profile, so nobody asked whether this "
                "judge has ever been measured against a human."
            )
        elif not calibrated:
            print(
                "  judge_calibrated=false: this judge has NO sealed holdout. Its verdicts "
                "rank rows; they do not remove them."
            )
        return 0

    if args.cmd == "novel":
        records = read_records(args.records)
        known = _known_from(args)
        new = novel_classes(records, known)
        old = covered_classes(records, known)
        print(f"{len(records)} adjudication(s) against {len(set(known))} known rule(s)\n")
        print("CANDIDATE RULES — named by the read, covered by nothing:")
        for cls, n in new.items():
            print(f"  {n:4}  {cls}")
        if not new:
            print("  (none)")
        if old:
            print("\nINERT GATES — a rule covers these, and the read found them anyway:")
            for cls, n in old.items():
                print(f"  {n:4}  {cls}")
            print(
                "  A rule that exists and did not fire is a worse finding than a missing "
                "rule. Check the gate actually runs before writing another one."
            )
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
