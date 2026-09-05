from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from .defects import normalize_defect_class
from .model import Adjudication
from .repair import REPAIR_ATTEMPT_CAP

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
#:
#: ``fact_creates_problem`` was merged into ``fact_earns_its_place`` on the label side on
#: 2026-09-02 (gtm_core.eval_calibration.LABEL_FIELDS) but is kept here as a HISTORICAL
#: ALIAS, not a live vocabulary item: every ``defect_class`` written by the judge before
#: that date still carries the old string, and dropping it would silently reclassify
#: already-recorded misfiled drops as ordinary (terminal) drops — a behaviour change
#: disguised as a rename. ``fact_supports_pitch`` / ``bridge_depends_on_fact`` are
#: deliberately NOT here: they were never targeting defects, and adding them now would
#: widen what counts as "mis-aimed" under cover of the merge. This module cannot import
#: the merge map from ``eval_calibration`` (that module already imports ``sample`` from
#: here — the reverse import would cycle), so the names are spelled out independently.
TARGETING_DEFECTS = frozenset({"fact_earns_its_place", "fact_creates_problem", "right_person"})


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
            # Normalised first: the judge spells one class several ways (kebab, negated),
            # and a raw-string comparison counted 53 of ~65 misfiled drops on 2026-09-01.
            if normalize_defect_class(rec.defect_class) in TARGETING_DEFECTS:
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
