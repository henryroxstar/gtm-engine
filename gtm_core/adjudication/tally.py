from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from ..signal_record import Verdict
from .model import Adjudication
from .stability import _unit, unstable_row_id
from .verdicts import _VERDICT_SEVERITY


@dataclass(frozen=True)
class TallyRow:
    """One (email, touch) unit's verdict across every run it was judged in.

    ``unscored=True`` on a contested or no-verdict row is deliberate, not a leftover
    default: it means every existing consumer of :class:`Adjudication` already refuses
    this row with no new plumbing — :func:`repair_queue` treats it as unscored,
    ``write-verdicts`` skips it at the point it builds ``by_email`` — so a draw between
    runs cannot silently reach the sequencer as a settled verdict.
    """

    email: str
    touch: int = 1
    verdict: str = ""  # the settled verdict, or "" when contested / no-verdict
    unscored: bool = False
    contested: bool = False
    policy: str = ""  # unanimous | majority | contested | no-verdict
    runs: int = 0
    scored_runs: int = 0
    verdict_counts: dict[str, int] = field(default_factory=dict)
    score: int = 0
    score_range: tuple[int, int] = (0, 0)
    defect_class: str = ""
    evidence: str = ""
    note: str = ""
    evidence_from: str = ""
    body_hash: str = ""
    body_stable: bool = True
    row_ids: tuple[str, ...] = ()
    backends: tuple[str, ...] = ()
    calibrated: bool | None = None

    def to_dict(self) -> dict:
        return {
            "email": self.email,
            "touch": self.touch,
            "verdict": self.verdict,
            "unscored": self.unscored,
            "contested": self.contested,
            "policy": self.policy,
            "runs": self.runs,
            "scored_runs": self.scored_runs,
            "verdict_counts": dict(self.verdict_counts),
            "score": self.score,
            "score_range": list(self.score_range),
            "defect_class": self.defect_class,
            "evidence": self.evidence,
            "note": self.note,
            "evidence_from": self.evidence_from,
            "body_hash": self.body_hash,
            "body_stable": self.body_stable,
            "row_ids": list(self.row_ids),
            "backends": list(self.backends),
            "calibrated": self.calibrated,
        }


@dataclass
class Tally:
    """A tally over one or more independent judging runs of the same pool.

    Reports a RANGE, never a point estimate — the incident this whole module exists to
    prevent was a single number (``38``) standing in for what twelve runs actually
    supported (``12`` to ``69``, 26% of recipients contested outright).
    """

    records: int = 0
    runs: int = 0
    recipients: int = 0
    units: int = 0
    unanimous: int = 0
    split: int = 0  # >=1 run disagreed with another, whether or not it settled
    contested: int = 0  # no strict majority — settles nothing
    no_verdict: int = 0  # every run on this unit was unscored
    rows: list[TallyRow] = field(default_factory=list)
    send_single_pass: int = 0
    primary_source: str = ""
    per_run_send_rate: tuple[float, float] = (0.0, 0.0)
    max_contested: float = 0.10
    unstable_key: str = ""  # informational unless --group-by row_id was requested
    findings: list[str] = field(default_factory=list)

    @property
    def split_rate(self) -> float:
        """Fraction of units where re-judging changed the verdict. NOT
        :func:`gtm_core.eval_calibration.flip_rate` — that measures rubric-order
        sensitivity within one run against a <10% bar; this measures cross-run
        disagreement. Same-shaped number, different question — never compare the two."""
        return self.split / self.units if self.units else 0.0

    @property
    def contested_rate(self) -> float:
        return self.contested / self.units if self.units else 0.0

    @property
    def failed(self) -> bool:
        return self.contested_rate > self.max_contested


def collapse_touches(rows: Sequence[TallyRow]) -> list[TallyRow]:
    """One row per recipient, worst touch wins — the send-set's collapse, mirroring
    :func:`worst_verdict`'s policy that a defect on one touch is a fact about the row.
    A contested or no-verdict touch is treated as worse than a confirmed ``drop``: "we
    don't know what touch 2 says" cannot be overridden by a clean touch 1."""

    def severity(row: TallyRow) -> int:
        return _VERDICT_SEVERITY.get(row.verdict, 3) if row.verdict else 3

    by_email: dict[str, list[TallyRow]] = defaultdict(list)
    for row in rows:
        by_email[row.email].append(row)
    return [max(group, key=severity) for group in by_email.values()]


def send_set(t: Tally, policy: str = "majority") -> list[str]:
    """Recipients supporting ``policy``, aggregated across all of a recipient's touches
    and runs. ``"consensus"`` — every scored run said send. ``"majority"`` — a strict
    majority of scored runs said send. ``"any"`` — at least one run said send, ever.

    Touch is tracked at unit level for the split/contested accounting above; it is
    deliberately NOT tracked here — whether a person ever reached a send verdict is a
    recipient-level question, not a per-touch one.
    """
    by_email: dict[str, Counter] = defaultdict(Counter)
    for row in t.rows:
        by_email[row.email].update(row.verdict_counts)
    out = []
    for email, counts in by_email.items():
        total = sum(counts.values())
        if not total:
            continue
        sends = counts.get(Verdict.SEND, 0)
        if policy == "consensus":
            ok = sends == total
        elif policy == "any":
            ok = sends > 0
        else:
            ok = sends * 2 > total
        if ok:
            out.append(email)
    return sorted(out)


def write_tally(rows: Iterable[TallyRow], path: Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(r.to_dict()) + "\n" for r in rows),
        encoding="utf-8",
    )
    return out


def tally(
    sources: Mapping[str, Sequence[Adjudication]],
    *,
    max_contested: float = 0.10,
    group_by: str = "email",
) -> Tally:
    """Tally independent judging runs of the same pool by (email, touch), never by
    ``row_id`` — see :func:`unstable_row_id`. ``sources`` is a mapping (run label ->
    its records) rather than a flat list because per-run send rate and the single-pass
    figure are undefined without knowing which record came from which run.
    """
    all_records = [rec for recs in sources.values() for rec in recs]
    key_fn = (lambda r: (r.row_id, 0)) if group_by == "row_id" else _unit

    by_unit: dict[tuple, list[tuple[str, Adjudication]]] = defaultdict(list)
    for label, recs in sources.items():
        for rec in recs:
            by_unit[key_fn(rec)].append((label, rec))

    rows: list[TallyRow] = []
    unanimous = split = contested = no_verdict = 0
    for _key, group in by_unit.items():
        email, touch = _unit(group[0][1])
        runs = len({label for label, _ in group})
        scored = [(label, rec) for label, rec in group if not rec.unscored and rec.verdict]
        verdict_counts = Counter(rec.verdict for _, rec in scored)

        hashes = {rec.body_hash for _, rec in group if rec.body_hash}
        body_hash = next(iter(hashes)) if len(hashes) == 1 else ""
        row_ids = tuple(sorted({rec.row_id for _, rec in group if rec.row_id}))
        backends = tuple(sorted({rec.backend for _, rec in group if rec.backend}))

        cal_values = [rec.calibrated for _, rec in scored]
        if any(v is False for v in cal_values):
            calibrated = False
        elif any(v is None for v in cal_values):
            calibrated = None
        elif cal_values:
            calibrated = True
        else:
            calibrated = None

        is_split = len(verdict_counts) > 1
        if is_split:
            split += 1

        if not scored:
            policy, verdict, unscored_flag, contested_flag = "no-verdict", "", True, False
            no_verdict += 1
            agreeing = []
        else:
            most_common = verdict_counts.most_common()
            tied = len(most_common) > 1 and most_common[0][1] == most_common[1][1]
            if tied:
                policy, verdict, unscored_flag, contested_flag = "contested", "", True, True
                contested += 1
                agreeing = scored
            else:
                verdict = most_common[0][0]
                policy = "unanimous" if len(verdict_counts) == 1 else "majority"
                unscored_flag = contested_flag = False
                if policy == "unanimous":
                    unanimous += 1
                agreeing = [(label, rec) for label, rec in scored if rec.verdict == verdict]

        scores = [rec.score for _, rec in scored]
        score_range = (min(scores), max(scores)) if scores else (0, 0)
        agree_scores = [rec.score for _, rec in agreeing] or scores
        score = round(median(agree_scores)) if agree_scores else 0

        if agreeing:
            label, chosen = min(
                agreeing,
                key=lambda t: (
                    t[1].row_id or "",
                    hashlib.sha256((t[1].note or "").encode()).hexdigest(),
                ),
            )
            evidence, note, defect_class, evidence_from = (
                chosen.evidence,
                chosen.note,
                chosen.defect_class,
                label,
            )
        else:
            evidence = note = defect_class = evidence_from = ""

        rows.append(
            TallyRow(
                email=email,
                touch=touch,
                verdict=verdict,
                unscored=unscored_flag,
                contested=contested_flag,
                policy=policy,
                runs=runs,
                scored_runs=len(scored),
                verdict_counts=dict(verdict_counts),
                score=score,
                score_range=score_range,
                defect_class=defect_class,
                evidence=evidence,
                note=note,
                evidence_from=evidence_from,
                body_hash=body_hash,
                body_stable=len(hashes) <= 1,
                row_ids=row_ids,
                backends=backends,
                calibrated=calibrated,
            )
        )

    t = Tally(
        records=len(all_records),
        runs=len(sources),
        recipients=len({r.email for r in rows}),
        units=len(rows),
        unanimous=unanimous,
        split=split,
        contested=contested,
        no_verdict=no_verdict,
        rows=rows,
        max_contested=max_contested,
        unstable_key=unstable_row_id(all_records),
    )

    if sources:
        t.primary_source = max(
            sources,
            key=lambda label: sum(1 for r in sources[label] if not r.unscored and r.verdict),
        )
        primary_sends = {
            (r.email or "").strip().lower()
            for r in sources.get(t.primary_source, [])
            if r.verdict == Verdict.SEND and not r.unscored
        }
        t.send_single_pass = len(primary_sends)
        rates = []
        for recs in sources.values():
            scored_recs = [r for r in recs if not r.unscored and r.verdict]
            if scored_recs:
                rates.append(
                    sum(1 for r in scored_recs if r.verdict == Verdict.SEND) / len(scored_recs)
                )
        if rates:
            t.per_run_send_rate = (min(rates), max(rates))

    if t.unstable_key and group_by == "row_id":
        t.findings.append(f"--group-by row_id refused: {t.unstable_key}")
    if t.contested_rate > max_contested:
        t.findings.append(
            f"{t.contested} of {t.units} unit(s) contested ({t.contested_rate:.1%}), over "
            f"--max-contested {max_contested:.0%} — the same 10% bar "
            f"gtm_core.eval_calibration.judge_meets_bar uses for flip_rate, applied to a "
            f"different metric."
        )
    return t
