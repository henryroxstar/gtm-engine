"""Severity budget — the fix for a gate nobody can read.

Every gate in this family ends the same way: a wall of findings and an instruction
that WARNs "need one explicit operator acknowledgment". That instruction is honest
at ten findings and a fiction at four hundred.

Consumers, as of 2026-08-27: ``account_integrity``, ``list_fit``, and
``tests/linter/outreach_pack_linter``. (This list previously named
``merge_render_linter`` and ``list_fit`` as consumers when neither imported this
module — the docstring described the intent rather than the wiring, which is the same
class of claim the rest of this module exists to stop. ``merge_render_linter`` still
hand-rolls its own grouping in ``_report``; it is the remaining adoption.)

Measured on 2026-08-19 against the four staged run-500 lists: ``account_integrity``
emitted **388 warnings**, of which **11 (2.8%) were competitor flags** — including
several direct competitors named outright, plus two hard ERRORs on lists that were
loaded into the sequencer anyway. The gate had already found, by name, most of what
three rounds of human email review later "discovered". Nothing was wrong with the
checks. The output was unreadable, so it was acknowledged wholesale, which is the
same thing as not running it.

The rule this module enforces:

    A gate may enumerate its findings only while a human could plausibly read them.
    Past the budget it reports **rates per class** with a few exemplars, and blocks.

Blocking is the point. An over-budget gate is not reporting "many small problems" —
it is reporting that its own signal-to-noise has collapsed, and the only useful
operator actions are to *fix a class* or to *retire a class*. Both are per-class
decisions, so acknowledgment is per-class too (``--ack competitor-flag``), auditable
in the shell history, and never a blanket "proceed".

The budget is not a severity ranking and does not reorder anything: an ERROR blocks
at any count. It governs whether the WARN tier is legible.

Stdlib-only, no I/O, tenant-agnostic — same shape as ``slugify`` and ``merge_hygiene``
so any gate can import it without dragging in a profile.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = [
    "WARN_BUDGET",
    "EXEMPLARS",
    "SATURATION",
    "ClassRate",
    "BudgetVerdict",
    "split_rule",
    "group_by_rule",
    "budget_verdict",
    "render_budget",
]

#: How many findings a human will actually read before acknowledging blindly. Fifteen
#: is a screen. There is no science behind the exact number; there is evidence behind
#: the claim that 388 is past it.
WARN_BUDGET = 15

#: Exemplars printed per class once enumeration is suppressed. A bare rate is not
#: actionable — you cannot fix "3.8% competitor-flag" without seeing three of them.
EXEMPLARS = 3

#: Above this share of the examined population, a class has stopped discriminating.
#: `leadership-freshness` firing on 93% of accounts is not eighty-two findings about
#: eighty-two accounts; it is one fact about the list, restated eighty-two times. The
#: rate is what makes that visible — the enumerated form never did, which is how a
#: saturated class survives for months as ambient noise that hides the 1% class.
SATURATION = 0.5


def split_rule(finding: str) -> tuple[str, str]:
    """Split a gate's ``"rule-name: detail"`` finding string into its two parts.

    The gates in this family already format findings this way, so the budget reads
    what they emit rather than requiring every one of them to be rewritten around a
    new record type first. A finding with no ``": "`` is its own class — better a
    one-member class than a silent drop.
    """
    rule, sep, detail = finding.partition(": ")
    return (rule, detail) if sep else (finding, "")


def group_by_rule(findings: Iterable[str]) -> dict[str, list[str]]:
    """Group findings by their rule name, preserving first-seen order of both."""
    out: dict[str, list[str]] = {}
    for f in findings:
        rule, detail = split_rule(f)
        out.setdefault(rule, []).append(detail or f)
    return out


@dataclass(frozen=True)
class ClassRate:
    """One finding class, as a rate over the population the gate examined."""

    rule: str
    count: int
    denominator: int
    acked: bool
    exemplars: tuple[str, ...] = ()

    @property
    def rate(self) -> float:
        """Share of the examined population carrying this finding, 0.0 when unknown."""
        return (self.count / self.denominator) if self.denominator else 0.0

    @property
    def saturated(self) -> bool:
        """Whether this class fires so widely it describes the list, not its members."""
        return bool(self.denominator) and self.rate >= SATURATION


@dataclass
class BudgetVerdict:
    budget: int
    denominator: int
    classes: list[ClassRate] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(c.count for c in self.classes)

    @property
    def unacked(self) -> int:
        return sum(c.count for c in self.classes if not c.acked)

    @property
    def over_budget(self) -> bool:
        return self.unacked > self.budget

    @property
    def enumerable(self) -> bool:
        """Whether the full list is short enough to print. The inverse of blocking."""
        return not self.over_budget

    @property
    def blocked(self) -> bool:
        """An over-budget WARN tier blocks. See the module docstring for why."""
        return self.over_budget

    @property
    def unacked_classes(self) -> list[ClassRate]:
        return [c for c in self.classes if not c.acked]


def budget_verdict(
    findings: Iterable[str],
    denominator: int,
    *,
    denominators: dict[str, int] | None = None,
    acked: Iterable[str] = (),
    budget: int = WARN_BUDGET,
    exemplars: int = EXEMPLARS,
) -> BudgetVerdict:
    """Grade a gate's WARN findings against the readability budget.

    ``denominator`` is the population the gate examined (rows, accounts, renders) so
    each class reports as a rate rather than a raw count — "11 competitor flags" and
    "11 competitor flags out of 292 accounts" support different decisions. One gate
    often examines two populations at once (a per-row domain check and a per-account
    competitor check), so ``denominators`` overrides it per rule; a rate against the
    wrong population is worse than no rate, because it reads as precise.

    ``acked`` names classes the operator has explicitly accepted for this run. An
    acknowledged class still prints (acknowledgment is not suppression) but stops
    counting against the budget, so accepting the one class you understand does not
    silently buy headroom for the four you have not read.
    """
    ack = {a.strip() for a in acked if a.strip()}
    per_rule = denominators or {}
    grouped = group_by_rule(findings)
    classes = [
        ClassRate(
            rule=rule,
            count=len(details),
            denominator=per_rule.get(rule, denominator),
            acked=rule in ack,
            exemplars=tuple(details[:exemplars]),
        )
        for rule, details in grouped.items()
    ]
    classes.sort(key=lambda c: (-c.count, c.rule))
    return BudgetVerdict(budget=budget, denominator=denominator, classes=classes)


def render_budget(
    v: BudgetVerdict, *, unit: str = "row", units: dict[str, str] | None = None
) -> str:
    """Render the WARN tier: full enumeration under budget, rates + exemplars over it.

    ``units`` names the population each rule was counted against, matching the
    ``denominators`` passed to :func:`budget_verdict`, so the printed rate says what
    it is a rate *of*.
    """
    if not v.classes:
        return ""
    per_rule_unit = units or {}
    lines: list[str] = []
    if v.over_budget:
        lines.append(
            f"  WARNINGS — {v.unacked} unacknowledged finding(s) across "
            f"{len(v.unacked_classes)} class(es), over the readability budget of "
            f"{v.budget}. Enumeration suppressed; rates and exemplars only."
        )
        lines.append(
            "  This blocks. A gate this loud cannot be acknowledged by reading it. "
            "Fix a class, retire a class, or accept one explicitly with "
            "--ack <rule> (per class, never a blanket pass)."
        )
    else:
        lines.append(
            f"  WARNINGS — {v.total} finding(s) across {len(v.classes)} class(es), "
            f"within the readability budget of {v.budget}. Needs one explicit "
            f"operator acknowledgment."
        )
    lines.append("")
    for c in v.classes:
        mark = "ACKED  " if c.acked else "UNACKED"
        u = per_rule_unit.get(c.rule, unit)
        plural = u if u.endswith("s") else u + "s"
        denom = f"/{c.denominator} {plural}" if c.denominator else ""
        flag = "  <- SATURATED: a property of this list, not a finding about its members"
        lines.append(
            f"  [{mark}] {c.rule}: {c.count}{denom} ({c.rate:.1%})" + (flag if c.saturated else "")
        )
        shown = c.exemplars if v.over_budget else ()
        for ex in shown:
            lines.append(f"      e.g. {ex}")
        if v.over_budget and c.count > len(c.exemplars):
            lines.append(f"      ... +{c.count - len(c.exemplars)} more in this class")
    return "\n".join(lines).rstrip()
