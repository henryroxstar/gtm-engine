"""Pre-enrolment list gates: is this list worth sending to at all?

The repo already gates *rendering* (`tests/linter/merge_render_linter.py`), *row hygiene*
(:mod:`gtm_core.merge_hygiene`) and *compliance* (:mod:`gtm_core.email_compliance`). All three
answer "is this row well-formed and legal to mail?". None answers the prior question:
**is this person someone who could act on the offer, and is the research good enough to open on?**

That gap is not theoretical. On 2026-08-11 a 286-row list passed every existing gate and was
53% mis-aimed: the A/B/C tier column ranked seniority and firmographics rather than role
relevance, so Tier A was 26% on-target while the *untiered* rows were 100%. Separately, 78 rows
carried a `why_now`, but only 2 referenced the current year and 18 held nothing but a Bombora
intent score -- a prioritisation input, not a sentence. Twelve rows out of 286 could carry a
personalised opener.

Industry data agrees on the ordering: signal-triggered cold email replies at roughly 5-18% where
generic role-and-size targeting gets 1-3%, and the gap between average and elite senders is a
targeting problem before it is a copy problem. So this module runs FIRST, before research is
commissioned -- researching a mis-aimed list converts the most expensive input in the pipeline
into nothing.

Fail-closed, like the rest of the gate family: unknown means not-proven, never assumed-good.

Deliberately NOT here: whether the address is deliverable, whether the market is in scope, whether
the copy renders. Those have owners. This module owns *who is on the list*.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .finding_budget import WARN_BUDGET, BudgetVerdict, budget_verdict, render_budget
from .merge_hygiene import SIGNAL_MAX_AGE_DAYS, signal_is_fresh, signal_latest_date
from .prospect_paths import suppression_ledger
from .suppression import load_index as load_suppression_index

__all__ = [
    "RoleFit",
    "SignalGrade",
    "ListAudit",
    "role_fit",
    "signal_grade",
    "source_hit_rates",
    "audit_rows",
    "main",
]

# --- role fit ------------------------------------------------------------

# Seats that could plausibly own, block or champion an infrastructure purchase.
# Broad on purpose: this gate exists to catch the *clearly* wrong seat, not to score fit.
# Anything it cannot place lands in `unclear`, which is a prompt for a human read -- never a pass.
_ROLE_IN = re.compile(
    r"(chief\s+(technolog|technical|information|info|security|privacy|compliance|data|digital"
    r"|risk|architect|scientist|product|innovation|executive|operating)"
    r"|\bciso\b|\bcto\b|\bcio\b|\bcpo\b|\bcdo\b|\bcoo\b|\bceo\b"
    r"|head\s+of\s+(engineering|ai|ml|security|architecture|platform|data|technolog|infosec"
    r"|it\b|product|strategy|risk|compliance|innovation|digital|legal)"
    r"|engineer|architect|technolog|information\s+(services|systems?|security|technolog)"
    r"|infosec|cyber|security|privacy|complian|governance|risk\s+(manage|officer)"
    r"|founder|general\s+counsel|legal|data\s+scien|analytics|product\s+manage|technical)",
    re.IGNORECASE,
)

# Seats that cannot buy this no matter how good the email is. Checked FIRST so that
# "VP Human Resources Business Consulting" is `out` despite matching nothing in _ROLE_IN.
_ROLE_OUT = re.compile(
    r"(human\s+resources|talent\s+acquisition|recruit|\bhr\b|pharmacy|materials\s+management"
    r"|hospitality|patient\s+liaison|physician\s+relations|nursing|food\s+service|facilities"
    r"|environmental\s+services|janitor|marketing|social\s+media|accounts\s+payable|payroll"
    r"|benefits|sales\s+strategy|business\s+development|underwriting|ambulatory"
    r"|retail\s+operations|private\s+assets|commercial\s+(lending|banking)|physician\s+recruit)",
    re.IGNORECASE,
)


class RoleFit:
    IN = "in"
    UNCLEAR = "unclear"
    OUT = "out"


def role_fit(title: str) -> str:
    """Classify a job title as :data:`RoleFit.IN` / ``UNCLEAR`` / ``OUT``.

    ``OUT`` is checked before ``IN`` on purpose: a title can contain both a plausible token and a
    disqualifying one ("VP Human Resources **Business** Consulting", "Director of Physician
    **Recruit**ment"), and the disqualifier is the load-bearing half.

    ``UNCLEAR`` is not a soft pass. It means a human has not read it yet, and
    :func:`audit_rows` refuses to clear a list while unclear rows remain unreviewed.
    """
    t = (title or "").strip()
    if not t:
        return RoleFit.UNCLEAR
    if _ROLE_OUT.search(t):
        return RoleFit.OUT
    if _ROLE_IN.search(t):
        return RoleFit.IN
    return RoleFit.UNCLEAR


# --- signal grade --------------------------------------------------------

# "machine learning & artificial intelligence (intent score 93)" is a Bombora topic score.
# It tells you who to call first. It is not a fact about the company and cannot be merged
# into a sentence -- shipping it as an opener produces a non-sequitur.
_INTENT_ONLY = re.compile(r"^\s*[^()]{0,80}\(intent\s+score\s+\d+\)\s*$", re.IGNORECASE)

# Research notes that admit they found nothing datable. Honest, and still unusable as an opener.
_HEDGED = re.compile(
    r"(no dated|not verifiable|unverified|could not confirm|to confirm at outreach"
    r"|still appears|structural[:,]? )",
    re.IGNORECASE,
)


class SignalGrade:
    #: A dated event inside the freshness window: supports "saw the news".
    FRESH = "fresh"
    #: A real, durable fact about what the company does. Undated by nature, and does not decay.
    #: This is the preferred hook -- see the module docstring.
    STRUCTURAL = "structural"
    #: A dated event that has aged out. Opening on it advertises a stale list.
    STALE = "stale"
    #: A Bombora-style intent topic + score. Prioritisation input only.
    INTENT_ONLY = "intent-only"
    #: Nothing usable.
    ABSENT = "absent"

    #: Grades that can carry a personalised opening line.
    USABLE = frozenset({FRESH, STRUCTURAL})


def signal_grade(
    why_now: str,
    as_of: datetime.date | None = None,
    max_age_days: int = SIGNAL_MAX_AGE_DAYS,
) -> str:
    """Grade a `why_now` research note by what kind of opener it can support.

    The distinction that matters is **dated vs structural**. A dated event ("$243M Series C,
    2025-07-29") decays: past the window it becomes a liability, because "saw your news" about a
    13-month-old round tells the reader you are working from an old list. A structural fact
    ("their platform runs autonomous agents against regulated casefiles") does not decay, which
    makes it the better hook for any list that will take weeks to send.

    Undated is therefore *not* automatically a failure -- unlike
    :func:`~gtm_core.merge_hygiene.signal_is_fresh`, which fail-closes to ``False`` because it is
    answering the narrower question "may this row open on *news*?".
    """
    text = (why_now or "").strip()
    if not text:
        return SignalGrade.ABSENT
    if _INTENT_ONLY.match(text):
        return SignalGrade.INTENT_ONLY
    if signal_latest_date(text) is not None:
        return (
            SignalGrade.FRESH
            if signal_is_fresh(text, as_of=as_of, max_age_days=max_age_days)
            else SignalGrade.STALE
        )
    if _HEDGED.search(text):
        return SignalGrade.ABSENT
    return SignalGrade.STRUCTURAL


# --- source attribution --------------------------------------------------


def _source_key(src: str) -> str:
    """Collapse a per-run filename to the run *family* that produced it.

    `prospects-20260724-backlog-enrich-run5-hubspot.csv` and `...-run6-...` are the same method
    with the same hit rate; comparing them individually splits the evidence and hides the signal.
    """
    s = (src or "").strip() or "unknown"
    s = re.sub(r"\.csv$", "", s)
    s = re.sub(r"-run\d+", "", s)
    s = re.sub(r"-hubspot.*$", "", s)
    s = re.sub(r"^prospects-\d{8}-", "", s)
    return s or "unknown"


def source_hit_rates(rows: list[dict]) -> dict[str, dict]:
    """In-ICP hit rate per source run family.

    This is the cheapest lever in the whole pipeline and it needs no sends: if one sourcing method
    returns 100% usable seats and another returns 35%, that ratio decides where the next
    enrichment budget goes. On the 2026-08-11 list a single bulk run contributed 71% of the rows
    and nearly all of the waste.
    """
    buckets: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        buckets[_source_key(r.get("src", ""))][role_fit(r.get("title", ""))] += 1
    out = {}
    for src, c in buckets.items():
        n = sum(c.values())
        out[src] = {
            "rows": n,
            "in": c[RoleFit.IN],
            "unclear": c[RoleFit.UNCLEAR],
            "out": c[RoleFit.OUT],
            "hit_rate": round(c[RoleFit.IN] / n, 3) if n else 0.0,
        }
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["rows"]))


# --- the audit -----------------------------------------------------------


@dataclass
class ListAudit:
    rows: int = 0
    role: Counter = field(default_factory=Counter)
    signal: Counter = field(default_factory=Counter)
    sources: dict = field(default_factory=dict)
    tier_role: dict = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    acked: tuple[str, ...] = ()
    budget: int = WARN_BUDGET

    @property
    def signal_usable(self) -> int:
        return sum(self.signal[g] for g in SignalGrade.USABLE)

    @property
    def verdict(self) -> BudgetVerdict:
        """Findings grouped by rule, with rates — see :mod:`gtm_core.finding_budget`."""
        return budget_verdict(self.findings, self.rows, acked=self.acked, budget=self.budget)

    @property
    def failed(self) -> bool:
        return bool(self.findings)


def audit_rows(
    rows: list[dict],
    as_of: datetime.date | None = None,
    *,
    min_hit_rate: float = 0.60,
    acked: tuple[str, ...] = (),
    budget: int = WARN_BUDGET,
) -> ListAudit:
    """Audit a prospect list before any research or enrolment spend.

    Raises findings (does not mutate rows) when:

    * any row is a clearly-wrong seat -- flagged, never auto-dropped, because a regex must not
      unilaterally disqualify a real person;
    * unclear seats remain, which need a human read;
    * a tier column disagrees with role fit, i.e. the tier does not mean what it claims;
    * a source run family falls below ``min_hit_rate``;
    * rows carry an intent score in the `why_now` field, which cannot be merged into copy;
    * rows carry a stale dated signal, which is worse than no signal.
    """
    a = ListAudit(rows=len(rows), acked=acked, budget=budget)
    tiers: dict[str, Counter] = defaultdict(Counter)

    for r in rows:
        fit = role_fit(r.get("title", ""))
        a.role[fit] += 1
        a.signal[signal_grade(r.get("why_now", ""), as_of=as_of)] += 1
        tiers[(r.get("tier") or "untiered").strip() or "untiered"][fit] += 1

    a.sources = source_hit_rates(rows)
    a.tier_role = {
        t: {
            "rows": sum(c.values()),
            "in": c[RoleFit.IN],
            "hit_rate": round(c[RoleFit.IN] / sum(c.values()), 3) if sum(c.values()) else 0.0,
        }
        for t, c in sorted(tiers.items())
    }

    if a.role[RoleFit.OUT]:
        a.findings.append(
            f"role-fit: {a.role[RoleFit.OUT]} row(s) are a clearly wrong seat. Flag them for a "
            f"human decision -- do not auto-delete."
        )
    if a.role[RoleFit.UNCLEAR]:
        a.findings.append(
            f"role-fit: {a.role[RoleFit.UNCLEAR]} row(s) could not be classified and need a "
            f"human read before enrolment."
        )

    # A tier column is only useful if it predicts fit. When the best-labelled tier is no better
    # than the worst, the label is measuring something else and must not drive research spend.
    graded = {t: v for t, v in a.tier_role.items() if t != "untiered" and v["rows"] >= 10}
    if len(graded) >= 2:
        best = max(graded.values(), key=lambda v: v["hit_rate"])
        worst = min(graded.values(), key=lambda v: v["hit_rate"])
        if best["hit_rate"] - worst["hit_rate"] < 0.15:
            a.findings.append(
                f"tier-meaningless: tier hit rates span only "
                f"{worst['hit_rate']:.0%}-{best['hit_rate']:.0%}; the tier column does not predict "
                f"role fit, so it must not be used to allocate research effort."
            )
        untiered = a.tier_role.get("untiered")
        if untiered and untiered["rows"] >= 10 and untiered["hit_rate"] > best["hit_rate"]:
            a.findings.append(
                f"tier-inverted: untiered rows are {untiered['hit_rate']:.0%} on-target versus "
                f"{best['hit_rate']:.0%} for the best labelled tier. The tier ranking is backwards."
            )

    for src, s in a.sources.items():
        if s["rows"] >= 25 and s["hit_rate"] < min_hit_rate:
            a.findings.append(
                f"source-quality: '{src}' contributed {s['rows']} row(s) at "
                f"{s['hit_rate']:.0%} role fit (floor {min_hit_rate:.0%}). Do not source the next "
                f"batch this way."
            )

    if a.signal[SignalGrade.INTENT_ONLY]:
        a.findings.append(
            f"signal-intent-only: {a.signal[SignalGrade.INTENT_ONLY]} row(s) hold an intent score "
            f"in why_now. That ranks who to contact; it is not a fact and cannot be merged into copy."
        )
    if a.signal[SignalGrade.STALE]:
        a.findings.append(
            f"signal-stale: {a.signal[SignalGrade.STALE]} row(s) carry a dated signal older than "
            f"{SIGNAL_MAX_AGE_DAYS} days. Opening on it advertises a stale list -- re-research or "
            f"switch the row to a structural hook."
        )
    return a


def render(a: ListAudit) -> str:
    lines = [f"list-fit audit — {a.rows} row(s)", ""]
    lines.append("  role fit:")
    for k in (RoleFit.IN, RoleFit.UNCLEAR, RoleFit.OUT):
        pct = f"{a.role[k] / a.rows:.0%}" if a.rows else "-"
        lines.append(f"    {k:<8} {a.role[k]:>5}  {pct}")
    lines.append("  signal grade:")
    for k in (
        SignalGrade.FRESH,
        SignalGrade.STRUCTURAL,
        SignalGrade.STALE,
        SignalGrade.INTENT_ONLY,
        SignalGrade.ABSENT,
    ):
        lines.append(f"    {k:<12} {a.signal[k]:>5}")
    lines.append(f"    -> {a.signal_usable} row(s) can carry a personalised opener")
    if a.tier_role:
        lines.append("  tier vs role fit:")
        for t, v in a.tier_role.items():
            lines.append(f"    {t:<12} n={v['rows']:<5} in-ICP {v['hit_rate']:.0%}")
    lines.append("  source hit rate:")
    for s, v in a.sources.items():
        lines.append(f"    {s[:38]:<40} n={v['rows']:<5} in-ICP {v['hit_rate']:.0%}")
    lines.append("")
    if a.findings:
        lines.append(f"  FAIL — {len(a.findings)} finding(s):")
        v = a.verdict
        if v.enumerable:
            lines.extend(f"    - {f}" for f in a.findings)
        else:
            lines.append(render_budget(v, unit="row"))
    else:
        lines.append("  PASS — no list-fit findings.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="gtm_core.list_fit",
        description="Pre-enrolment list gate: role fit, signal grade, source hit rate.",
    )
    p.add_argument("--csv", required=True, type=Path, help="the prospect list about to be worked")
    p.add_argument(
        "--min-hit-rate",
        type=float,
        default=0.60,
        help="minimum in-ICP rate for a source run family (default 0.60)",
    )
    # Suppression semantics, shared with `account_integrity` and `merge_render_linter`:
    # consulting the ledger is the DEFAULT. The three gates previously disagreed — one
    # always skipped, two were opt-in, and this one could not reach the ledger at all
    # because it took no profile — so whether an excluded person was linted depended on
    # which gate you happened to run.
    p.add_argument(
        "--profile",
        default="",
        help="profile whose suppression ledger to consult; omitted falls back to the "
        "`suppression` column alone, which a rebuild can have dropped",
    )
    p.add_argument(
        "--include-suppressed",
        action="store_true",
        help="lint suppressed rows too (default: skip them — they will never be sent, and "
        "their findings crowd out the ones about rows that will)",
    )
    p.add_argument(
        "--skip-suppressed",
        action="store_true",
        help="deprecated, now the default; accepted so existing invocations keep working",
    )
    p.add_argument(
        "--ack",
        action="append",
        default=[],
        metavar="RULE",
        help="acknowledge a finding class by rule name; repeatable",
    )
    p.add_argument(
        "--budget",
        type=int,
        default=WARN_BUDGET,
        help=f"unacknowledged finding classes tolerated before enumeration is suppressed "
        f"and this blocks (default {WARN_BUDGET})",
    )
    p.add_argument("--warn-only", action="store_true", help="report findings but exit 0")
    args = p.parse_args(argv)

    with args.csv.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not args.include_suppressed:
        before = len(rows)
        index = load_suppression_index(suppression_ledger(args.profile)) if args.profile else None
        rows = [
            r
            for r in rows
            if not (r.get("suppression") or "").strip() and not (index.match(r) if index else None)
        ]
        if before != len(rows):
            source = "ledger + column" if index else "column only"
            print(f"suppressed: skipped {before - len(rows)} row(s) ({source})\n")

    a = audit_rows(
        rows,
        min_hit_rate=args.min_hit_rate,
        acked=tuple(args.ack),
        budget=args.budget,
    )
    print(render(a))
    return 0 if (args.warn_only or not a.failed) else 1


if __name__ == "__main__":
    sys.exit(main())
