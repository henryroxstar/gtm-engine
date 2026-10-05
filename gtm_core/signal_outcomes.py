"""R6.3: replies joined to what each person was enrolled under. Read-only.

``python -m gtm_core.signal_outcomes --profile P`` joins ``outcomes.jsonl`` to ``enrolments.jsonl`` by
(lowercased email, sequence id) and reports, per kind of evidence, how many people were enrolled and
how many replied, replied well or opted out. A reply it cannot join is counted, never dropped:

* ``before_tracking``: the reply is in a sequence ``enrolments.jsonl`` never saw (it predates the file);
* ``unattributed``: the sequence is tracked, but nobody enrolled in it has that address.

Small numbers are called small. Below :data:`MIN_N` enrolled in either group the verdict is "too few to
read"; above it, two groups are "better"/"worse" only when their 95% intervals do not overlap.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .enrolments import CLASS_LABELS, GENERAL, label_for, path_for
from .outcomes import read_outcomes
from .paths import resolve_content_root

MIN_N = 100
#: A class value no enrolment writer produces; it shares one row and one fixed label.
UNRECOGNISED = "unrecognised"
_UNRECOGNISED_LABEL = "an unrecognised kind of evidence"
_REPLY = "reply"
_POSITIVE = ("positive_reply", "meeting")
_OPT_OUT = "opt_out"


def beta_cdf(x: float, a: float, b: float, steps: int = 4000) -> float:
    """CDF of Beta(a, b) at ``x`` by Simpson's rule (a, b >= 1, so the density is bounded)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_norm = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)

    def pdf(t: float) -> float:
        return (
            math.exp(log_norm + (a - 1) * math.log(t) + (b - 1) * math.log1p(-t))
            if 0 < t < 1
            else 0.0
        )

    n = steps + steps % 2
    h = x / n
    total = pdf(x * 1e-12) + pdf(x)
    total += sum((4 if i % 2 else 2) * pdf(i * h) for i in range(1, n))
    return min(1.0, total * h / 3)


def beta_interval(successes: int, trials: int, level: float = 0.95) -> tuple[float, float]:
    """The central ``level`` interval of the Beta(1 + successes, 1 + failures) posterior."""
    a, b = 1 + successes, 1 + max(trials - successes, 0)
    tail = (1 - level) / 2

    def quantile(p: float) -> float:
        lo, hi = 0.0, 1.0
        for _ in range(50):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if beta_cdf(mid, a, b) < p else (lo, mid)
        return (lo + hi) / 2

    return quantile(tail), quantile(1 - tail)


@dataclass
class ClassRow:
    key: str
    label: str
    enrolled: int = 0
    replies: int = 0
    positive: int = 0
    opt_outs: int = 0
    verdict: str = ""
    interval: tuple[float, float] = (0.0, 1.0)


@dataclass
class Report:
    classes: list[ClassRow] = field(default_factory=list)
    before_tracking: int = 0
    unattributed: int = 0
    #: Lines of ``enrolments.jsonl`` that could not be read as an enrolment. A person on one of them
    #: is missing from the denominator, so the count is said, never dropped.
    unreadable_enrolments: int = 0


def _read_enrolments(content_root: Path, profile: str) -> tuple[list[dict], int]:
    """``(enrolments, unreadable)``: the readable rows, and how many non-blank lines were not."""
    path = path_for(content_root, profile)
    if not path.is_file():
        return [], 0
    rows: list[dict] = []
    unreadable = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if isinstance(rec, dict) and rec.get("email") and rec.get("sequence_id"):
            rows.append(rec)
        else:
            unreadable += 1
    return rows, unreadable


def _class_key(rec: dict) -> str:
    """The signal class a row was enrolled under: one of the closed set, else ``UNRECOGNISED``.

    A hand-edited line must not be able to make a row of its own, or put its text in a report.
    """
    key = str(rec.get("signal_class") or GENERAL)
    return key if key in CLASS_LABELS else UNRECOGNISED


def _seq_of(row: dict) -> str:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    tag = next(
        (t[4:] for t in row.get("tags") or [] if isinstance(t, str) and t.startswith("seq:")), ""
    )
    return str(tag or meta.get("sequence_id") or "")


def _label(key: str) -> str:
    return _UNRECOGNISED_LABEL if key == UNRECOGNISED else label_for(key)


def _verdict(row: ClassRow, general: ClassRow | None) -> str:
    if row.enrolled < MIN_N or general is None or general.enrolled < MIN_N:
        return f"too few to read (n < {MIN_N})"
    if row.key == GENERAL:
        return "the comparison group"
    if row.interval[0] > general.interval[1]:
        return "better"
    if row.interval[1] < general.interval[0]:
        return "worse"
    return "no clear difference yet"


def report(content_root: Path, profile: str) -> Report:
    """Per class: people enrolled, people who replied, replied well, or opted out.

    Every count is a PERSON in a sequence (lowercased email, sequence id), never a message row: one
    person who answers three times is one reply, a positive reply is also a reply, and a meeting is a
    positive one. The rate is replies over enrolled, so a count of messages in the numerator would
    put "49 of 1" in the report the go/no-go decision reads.
    """
    parsed, unreadable = _read_enrolments(content_root, profile)
    enrolled = {(e["email"].strip().lower(), e["sequence_id"]): e for e in parsed}
    tracked = {seq for _, seq in enrolled}
    rows: dict[str, ClassRow] = {}
    for e in enrolled.values():
        key = _class_key(e)
        rows.setdefault(key, ClassRow(key, _label(key))).enrolled += 1
    out = Report(unreadable_enrolments=unreadable)
    replied: set[tuple[str, str]] = set()
    well: set[tuple[str, str]] = set()
    left: set[tuple[str, str]] = set()
    stray: dict[bool, set[tuple[str, str]]] = {True: set(), False: set()}
    for rec in read_outcomes(content_root, profile):
        kind = rec.get("outcome")
        meta = rec.get("meta") if isinstance(rec.get("meta"), dict) else {}
        if rec.get("channel") != "email" or kind not in (_REPLY, _OPT_OUT, *_POSITIVE):
            continue
        email, seq = str(meta.get("prospect_email") or "").strip().lower(), _seq_of(rec)
        person = (email, seq)
        if person not in enrolled:
            if kind != _OPT_OUT:  # a stranger's opt-out was never a reply either
                stray[seq in tracked].add(person)
            continue
        if kind == _OPT_OUT:
            left.add(person)
        else:
            replied.add(person)
            if kind in _POSITIVE:
                well.add(person)
    out.unattributed, out.before_tracking = len(stray[True]), len(stray[False])
    for person in replied | left:
        row = rows[_class_key(enrolled[person])]
        row.replies += person in replied
        row.positive += person in well
        row.opt_outs += person in left
    out.classes = sorted(rows.values(), key=lambda r: (r.key == GENERAL, r.key))
    for row in out.classes:
        row.interval = beta_interval(row.replies, row.enrolled)
    general = rows.get(GENERAL)
    for row in out.classes:
        row.verdict = _verdict(row, general)
    return out


def plain_lines(rep: Report) -> list[str]:
    """The plain-words table: who was enrolled on what, and how many replied. No intervals."""
    lines = ["Replies by what each person was enrolled on:"]
    for c in rep.classes:
        lines.append(
            f"- {c.label}: {c.enrolled} enrolled, {c.replies} replied, {c.positive} replied well, "
            f"{c.opt_outs} opted out. {c.verdict}."
        )
    if not rep.classes:
        lines.append("- Nobody is tracked yet.")
    lines.append(f"People who replied before tracking began: {rep.before_tracking}.")
    lines.append(
        f"People who replied but could not be matched to anyone enrolled: {rep.unattributed}."
    )
    if rep.unreadable_enrolments:
        n = rep.unreadable_enrolments
        lines.append(
            f"{n} enrolment line{'s' if n != 1 else ''} could not be read, so those people are "
            "missing from the counts above."
        )
    return lines


def record_lines(rep: Report) -> list[str]:
    """The interval per class, for the record section only (the reader of the plain table never needs it)."""
    out = []
    for c in rep.classes:
        lo, hi = c.interval
        out.append(
            f"- {c.label}: reply rate between {lo:.1%} and {hi:.1%} "
            f"(95% Beta(1,1) interval, n = {c.enrolled})."
        )
    return out


def render(rep: Report) -> str:
    return "\n".join(
        [
            *plain_lines(rep),
            "",
            "Record (for the status page's record section):",
            *record_lines(rep),
        ]
    )


def main(argv: list[str] | None = None, *, content_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gtm_core.signal_outcomes", description=__doc__)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    print(render(report(content_root or resolve_content_root(), args.profile)))
    return 0


__all__ = [
    "CLASS_LABELS",
    "Report",
    "beta_cdf",
    "beta_interval",
    "main",
    "plain_lines",
    "record_lines",
    "render",
    "report",
]

if __name__ == "__main__":
    sys.exit(main())
