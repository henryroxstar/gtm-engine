from __future__ import annotations

from pathlib import Path

from gtm_core.finding_budget import budget_verdict, render_budget

from .catalog import dimensions
from .model import ADVISORY, ERROR, Finding, Section
from .parse import _section_suppressions, parse_sections
from .rules_claims import _sd6, _sd7, _sd8, _sd9
from .rules_coverage import _sd1, _sd2, _sd3, _sd4, satisfied_by
from .rules_integrity import _sd12, _sd13, lint_skill

__all__ = ["coverage_map", "lint", "lint_skill", "report"]


def lint(text: str) -> list[Finding]:
    """Lint one solution-design document.

    Raises :class:`~gtm_core.design_lint.parse.UnparseableDesign` when the text is not a
    design document — never returns a clean result for something it could not read.
    """
    sections = parse_sections(text)
    findings: list[Finding] = []
    findings += _sd1(sections)
    findings += _sd2(sections)
    findings += _sd3(sections)
    findings += _sd4(sections)
    findings += _sd6(sections)
    findings += _sd7(sections)
    findings += _sd8(sections)
    findings += _sd9(sections)
    findings += _sd12(sections)
    findings += _sd13(sections)
    # An ADVISORY is not filtered, because there is no `lint-ok SD6` to write. It blocks
    # nothing, so it demands no escape hatch — and the hatch it would otherwise need is a
    # linter comment inside a document we hand to a customer, recording that our tooling's
    # vocabulary is older than the product. Disagreement with an advisory belongs in the
    # report, never in the deliverable.
    return [
        f
        for f in findings
        if f.severity == ADVISORY or f.tier not in _section_suppressions(sections, f)
    ]


def coverage_map(sections: list[Section]) -> list[tuple[str, str, str]]:
    """(dimension id, name, status) for every coverage dimension, in taxonomy order.

    `status` is drawn from the closed vocabulary coverage.toml declares. A dimension this
    function cannot evaluate reports `not_verified`, never `present` — an absent answer and
    a satisfied one must never look alike.
    """
    out: list[tuple[str, str, str]] = []
    for dimension in dimensions():
        try:
            hit = satisfied_by(sections, dimension)
        except Exception:  # pragma: no cover - a rule that cannot decide must not pass
            out.append((dimension.id, dimension.name, "not_verified"))
            continue
        out.append((dimension.id, dimension.name, "present" if hit else "absent"))
    return out


def report(
    path: Path,
    findings: list[Finding],
    coverage: list[tuple[str, str, str]],
    examined: int = 0,
) -> None:
    """Print one document's verdict.

    `examined` is the number of sections the linter read — the population a WARN rate is a
    rate *of*. It is passed in rather than derived from the findings, because deriving it
    from the findings counts only the sections that already failed: every class then reads
    as 50–100% and trips the saturation flag, and "a rate against the wrong population is
    worse than no rate, because it reads as precise".
    """
    advisories = [f for f in findings if f.severity == ADVISORY]
    graded = [f for f in findings if f.severity != ADVISORY]
    if not graded:
        # "clean" means nothing blocks and nothing is graded — but printing a bare tick
        # directly above a finding reads as a contradiction, which is the thing the three
        # tiers exist to avoid. Name the count so the tick and the block agree.
        tail = (
            f", with {len(advisories)} judgement call(s) below — none of them blocking"
            if advisories
            else ""
        )
        print(f"✓ {path} — clean{tail}")
    else:
        print(f"\n{path}")
        errors = [f for f in graded if f.severity == ERROR]
        warns = [f for f in graded if f.severity != ERROR]
        # Errors always enumerate — there are few and each blocks. Warnings go through the
        # readability budget: past WARN_BUDGET a gate that lists everything is one nobody
        # reads, so it reports rates per class with a few exemplars instead.
        for f in sorted(errors, key=lambda f: (f.section, f.tier)):
            where = f"section {f.section}" if f.section else "document"
            print(f"  ✗ [{f.tier} {f.rule}] {where}: {f.excerpt}")
            print(f"      → {f.fix}")
        if warns:
            verdict = budget_verdict(
                [f"{f.tier} {f.rule}: {f.excerpt}" for f in warns],
                denominator=max(examined, len({f.section for f in findings})),
            )
            print(render_budget(verdict, unit="section"))
    _advisory_block(advisories)
    # Not a finding — nothing here is necessarily wrong. It is printed every run because
    # coverage is the one thing a reader cannot see by scrolling: a design reads complete
    # right up until the architect asks the question it never answered.
    if coverage:
        absent = [f"{i} {n}" for i, n, s in coverage if s != "present"]
        print(f"\n  Coverage: {len(coverage) - len(absent)}/{len(coverage)} dimensions answered")
        if absent:
            print("    not answered: " + " · ".join(absent))


#: Why each claim rule might be wrong, said next to its findings rather than left implicit.
#: An advisory the reader cannot weigh is an advisory they will learn to scroll past.
_WHY_IT_MIGHT_BE_WRONG = {
    "SD6": "reads tag position, not the product — a claim may be tagged elsewhere",
    "SD7": "if the capability shipped since, the label is the stale half, not the sentence",
    "SD8": "a lifted beta constraint looks exactly like an overclaim from here",
    "SD9": "two correct sentences about two different operations read alike to this rule",
}


def _advisory_block(advisories: list[Finding]) -> None:
    """Print the judgement calls — their own block, one line each, and never a verdict.

    Deliberately NOT routed through `finding_budget` the way warnings are. The budget's job
    is to keep a long list of gradeable findings readable by collapsing it to rates; these
    are not gradeable and there is no action attached to a rate of them. What a reader needs
    is the specific sentence and the reason the linter may be out of date about it, so each
    one prints whole and the rules print separately.
    """
    if not advisories:
        return
    print(
        "\n  Judgement calls — these block nothing, and there is no lint-ok to write for"
        "\n  them: disagreeing with one costs you nothing and leaves no linter comment in"
        "\n  a document the customer reads. The linter reads structure; you know what"
        "\n  shipped this week. Where it is wrong it is usually because a tag or a limit"
        "\n  moved and it has not learnt yet — so read each one, then fix it or drop it."
    )
    for tier in sorted({f.tier for f in advisories}):
        rows = [f for f in advisories if f.tier == tier]
        name = rows[0].rule
        print(f"\n  ~ {tier} {name} — {_WHY_IT_MIGHT_BE_WRONG.get(tier, '')}")
        for f in sorted(rows, key=lambda f: (f.section, f.excerpt)):
            where = f"section {f.section}" if f.section else "document"
            print(f"    · {where}: {f.excerpt}")
            print(f"        → {f.fix}")
