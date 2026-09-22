"""Mutation suite — one *smallest* crafted deviation per rule, from a known-clean baseline.

The tripwire corpus (``tests/tripwire/test_design_lint_tripwire.py``) proves every rule fires,
but it fires them all from one document that is wrong in sixteen ways at once. That cannot tell
a rule that fires *on its own cause* from a rule that only fires as a side effect of another —
and a rule that has never been shown to fire independently is, for review purposes, the same as
a rule nobody has seen fire.

So each case here starts from ``clean.md``, which lints to nothing, applies the smallest edit
that should trip exactly one rule, and asserts the **exact** finding set. Exact, not containment:
a mutation that happened to break five rules would satisfy a containment check for each of them
in turn, which is the failure this suite exists to rule out. Where a deviation genuinely cannot
be made smaller — removing the executive summary to unseat COV-01 also removes a tier — the
collateral finding is named in the expectation rather than tolerated by a loose assertion.

Modelled on ``tests/linter/test_merge_render_mutation_suite.py``, which exists because 25 of that
linter's 43 rules had no negative control at all: *a rule with no negative control is
indistinguishable from a dead rule* — both produce silence on clean input.

The two backstops at the bottom are the point of the file as much as the cases are. One fails
when a rule joins the inventory with no mutation; the other fails when a mutation emits a rule
the inventory does not know about. They earned their keep again when SD6-SD9 landed: all four
went into the inventory and the corpus, both backstops went red naming exactly the four with no
negative control, and that is the only signal that would have caught it — an ``advisory`` cannot
fail a build, so nothing else in CI has any opinion about whether one works.
"""

from __future__ import annotations

import pytest

from gtm_core import design_lint as dl
from tests.tripwire.test_design_lint_tripwire import CORPUS, INVENTORY, triples

Triple = tuple[str, str, str]

BASELINE = (CORPUS / "clean.md").read_text(encoding="utf-8")


def _rename(text: str, old: str, new: str) -> str:
    """Rewrite one heading. Asserts the heading was there — a no-op edit is a silent pass."""
    assert text.count(old) == 1, f"expected exactly one {old!r} in the baseline"
    return text.replace(old, new)


def _drop_exec(text: str) -> str:
    return _rename(text, "## Executive summary", "## Overview")


def _swap_tiers(text: str) -> str:
    out = _rename(text, "## Tier 1 — customer overview", "## TIER-B")
    out = _rename(out, "## Tier 2 — technical appendix", "## Tier 1 — customer overview")
    return _rename(out, "## TIER-B", "## Tier 2 — technical appendix")


#: (id, mutate, expected findings). Every heading a mutation renames is replaced with one that
#: answers no coverage dimension at all, so the edit removes exactly the answer under test.
MUTATIONS: list[tuple[str, object, set[Triple]]] = [
    (
        "sd1-tier-missing",
        _drop_exec,
        {("SD1", "tier missing", "error")},
    ),
    (
        "sd1-tier-order",
        _swap_tiers,
        {("SD1", "tier order", "error")},
    ),
    (
        "sd2-cov-01",
        # COV-01 is answered twice — by the executive summary and by the "what we heard" recap —
        # so unseating it necessarily unseats a tier as well. That is declared, not tolerated.
        lambda t: _rename(_drop_exec(t), "### What we heard", "### Background"),
        {("SD2", "coverage gap COV-01", "error"), ("SD1", "tier missing", "error")},
    ),
    (
        "sd2-cov-02",
        lambda t: _rename(t, "### Constraints", "### Ground rules"),
        {("SD2", "coverage gap COV-02", "warn")},
    ),
    (
        "sd2-cov-03",
        lambda t: _rename(t, "### Context and current state", "### Where things stand"),
        {("SD2", "coverage gap COV-03", "error")},
    ),
    (
        "sd2-cov-04",
        lambda t: _rename(t, "### The solution we propose", "### The gateway"),
        {("SD2", "coverage gap COV-04", "error")},
    ),
    (
        "sd2-cov-05",
        lambda t: _rename(t, "### A3. Component inventory", "### A3. Moving parts"),
        {("SD2", "coverage gap COV-05", "error")},
    ),
    (
        "sd2-cov-06",
        # Two headings answer COV-06 — the how-it-works section and A4, whose title names the
        # data flow. Both go, and A4 keeps the identity/policy words so COV-08 still stands:
        # the deviation is minimal in what it REMOVES, not in how many lines it touches.
        lambda t: _rename(
            _rename(t, "### How it works", "### The booking path"),
            "### A4. Identity, policy and data flow",
            "### A4. Identity and policy",
        ),
        {("SD2", "coverage gap COV-06", "error")},
    ),
    (
        "sd2-cov-07",
        lambda t: _rename(t, "### Deployment topology", "### Where the gateway sits"),
        {("SD2", "coverage gap COV-07", "warn")},
    ),
    (
        "sd2-cov-08",
        lambda t: _rename(
            t, "### A4. Identity, policy and data flow", "### A4. What holds across the parts"
        ),
        {("SD2", "coverage gap COV-08", "warn")},
    ),
    (
        "sd2-cov-09",
        lambda t: _rename(t, "### A7. Decisions and trade-offs", "### A7. Why this shape"),
        {("SD2", "coverage gap COV-09", "warn")},
    ),
    (
        "sd2-cov-11",
        lambda t: _rename(t, "### Risks and open questions", "### Still unsettled"),
        {("SD2", "coverage gap COV-11", "error")},
    ),
    (
        "sd3-quality-requirements",
        lambda t: _rename(t, "### A9. Quality requirements", "### A9. Targets"),
        {("SD3", "quality requirements absent", "warn")},
    ),
    (
        "sd4-glossary",
        lambda t: _rename(t, "### A10. Glossary", "### A10. Terms"),
        {("SD4", "glossary absent", "warn")},
    ),
    (
        "sd6-untagged-capability",
        # One cell. The matrix declares a Status column and one row stops filling it in —
        # which is the whole rule: nothing about the claim changes, only whether the design
        # still says how ready it is.
        lambda t: _rename(
            t,
            "| Cross-region reconciliation | Design-target | one region at a time today |",
            "| Cross-region reconciliation |  | one region at a time today |",
        ),
        {("SD6", "untagged capability", "advisory")},
    ),
    (
        "sd7-design-target-as-live",
        # The matrix row is untouched and still honest; only the narrative changes mood. That
        # asymmetry IS the defect — the customer's architect reads the narrative.
        lambda t: _rename(
            t,
            "It would reconcile two regions against each other once the single-region path "
            "is bedded in.",
            "Cross-region reconciliation validates every leg of the booking, in all four regions.",
        ),
        {("SD7", "design-target as live", "advisory")},
    ),
    (
        "sd8-constraint-contradiction",
        # The Constraints section is left exactly as it was: the document now carries its own
        # refutation, which is worse than an unsourced claim and the reason this rule exists.
        lambda t: _rename(
            t,
            "The clinical record system sits outside our boundary and is\nnot modified.",
            "The gateway writes each reconciliation verdict into the clinical record system.",
        ),
        {("SD8", "constraint contradiction", "advisory")},
    ),
    (
        "sd9-actor-drift",
        # One verb, and the smallest possible deviation in the whole suite: `store` → `present`
        # makes the operator do what the principal already does two sections earlier. Neither
        # sentence is wrong on its own, which is why this survives review.
        lambda t: _rename(
            t,
            "We store the verdict against that reference",
            "We present the verdict against that reference",
        ),
        {("SD9", "actor drift", "advisory")},
    ),
    (
        "sd12-count-drift",
        # One word. The failure this rule exists for is exactly this size: an edit moves the
        # list and leaves the number reading as authoritative.
        lambda t: _rename(t, "Three problems came up", "Four problems came up"),
        {("SD12", "count drift", "error")},
    ),
    (
        "sd13-diagram-alone",
        lambda t: _rename(t, "How to read this: a booking leaves", "A booking leaves"),
        {("SD13", "diagram without walkthrough", "warn")},
    ),
]

#: SD11 runs over a skill body, not a design, so its mutation has its own baseline: the quiet
#: mirror in the corpus, edited into the contradiction it mirrors.
SKILL_BASELINE = (CORPUS / "skill-clean.md").read_text(encoding="utf-8")
SD11_EXPECTED: set[Triple] = {("SD11", "contradictory guardrail", "error")}


def _mutate(spec) -> set[Triple]:
    return set(triples(dl.lint(spec(BASELINE))))


def test_the_baseline_is_clean() -> None:
    """Without this, every case below could be passing for the wrong reason."""
    assert dl.lint(BASELINE) == []


@pytest.mark.parametrize(
    ("mutate", "expected"), [(m[1], m[2]) for m in MUTATIONS], ids=[m[0] for m in MUTATIONS]
)
def test_mutation_fires_exactly_its_rule(mutate, expected: set[Triple]) -> None:
    assert _mutate(mutate) == expected


def test_sd11_mutation_fires_from_its_own_clean_baseline() -> None:
    assert dl.lint_skill(SKILL_BASELINE) == []
    contradicted = SKILL_BASELINE.replace(
        "A revision carries no version log: the design is replaced in place and the date moves.",
        "A revision must append a version log as its final section.",
    )
    assert set(triples(dl.lint_skill(contradicted))) == SD11_EXPECTED


def test_every_inventory_rule_has_a_mutation() -> None:
    """Backstop one: a rule may not enter the inventory without a negative control here."""
    covered: set[Triple] = set(SD11_EXPECTED)
    for _, mutate, expected in MUTATIONS:
        covered |= expected
    assert covered == INVENTORY


def test_every_emitted_rule_is_in_the_inventory() -> None:
    """Backstop two, the reverse: a mutation may not emit a rule nothing has catalogued.

    This is what caught four uncatalogued rules in the linter this suite is modelled on.
    """
    emitted: set[Triple] = set(SD11_EXPECTED)
    for _, mutate, _expected in MUTATIONS:
        emitted |= _mutate(mutate)
    assert emitted <= INVENTORY
    assert emitted == INVENTORY
