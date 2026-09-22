"""Tripwire corpus for ``gtm_core.design_lint`` — every rule fires, at its severity, or CI is red.

Read ``tests/tripwire/README.md`` first. This is the half of the "two tests bracket every rule"
property that ``tests/lint/test_design_lint.py`` cannot supply: that file proves each rule is
**silent** on good input (its committed quiet-cases, and the real-artifact gate over
``content/``), and this one proves each rule still **fires** on a committed bad one. A rule that
silently stops working produces silence in both places otherwise, and silence is what a passing
lint looks like.

The corpus is four fixtures, all fictional (§R9):

* ``clean.md`` — a correct design. Fires nothing, answers all twelve coverage dimensions. It
  also carries the claim rules' POSITIVE controls, which the coverage rules never needed: a
  capability matrix whose Status column is filled on every row, a design-target described in the
  conditional, and one action attributed to one actor throughout. Each of those is a state SD6,
  SD7 and SD9 must stay silent on, and none of them existed in the fixture before the claim
  rules did — a rule whose quiet state is untested is a rule that might be quiet everywhere.
* ``dirty.md`` — every document rule, once.
* ``skill-contradiction.md`` / ``skill-clean.md`` — SD11's firing and quiet states. SD11 runs
  only in ``--skill`` mode, so it reaches the inventory through ``lint_skill`` and nowhere else.

``clean.md`` is not decoration. Writing it is what found the last calibration defect: the marker
``figure`` matched the word "figure" meaning *a number* — word-bounding it had fixed
"con**figure**d" and not this. See ``catalog.DIAGRAM_REFERENCE``.
"""

from __future__ import annotations

import pytest

from gtm_core import design_lint as dl
from tests.tripwire import support

CORPUS = support.HERE / "design_lint"

Triple = tuple[str, str, str]

#: Every (tier, rule, severity) ``design_lint`` can emit. SD2's rule name carries the coverage
#: dimension, so this literal also pins the taxonomy: adding a dimension to ``coverage.toml``, or
#: changing one's severity, fails here until the corpus covers it. That is the intent — a new
#: dimension that nothing exercises is a dimension nobody has seen fire.
#:
#: COV-10 and COV-12 are absent on purpose: they are reported by SD3 and SD4, their own rules, so
#: a design with no NFRs gets one loud finding rather than a warning lost inside SD2's list.
#:
#: SD6-SD9 are ``advisory`` — a severity that blocks nothing, not even under ``--strict``. They
#: still belong here, and that is the point: an advisory nobody has watched fire is exactly as
#: dead as a blocking rule nobody has watched fire, and the fact that it cannot fail CI makes
#: the tripwire the *only* thing standing between it and silently rotting.
INVENTORY: frozenset[Triple] = frozenset(
    {
        ("SD1", "tier missing", "error"),
        ("SD1", "tier order", "error"),
        ("SD2", "coverage gap COV-01", "error"),
        ("SD2", "coverage gap COV-02", "warn"),
        ("SD2", "coverage gap COV-03", "error"),
        ("SD2", "coverage gap COV-04", "error"),
        ("SD2", "coverage gap COV-05", "error"),
        ("SD2", "coverage gap COV-06", "error"),
        ("SD2", "coverage gap COV-07", "warn"),
        ("SD2", "coverage gap COV-08", "warn"),
        ("SD2", "coverage gap COV-09", "warn"),
        ("SD2", "coverage gap COV-11", "error"),
        ("SD3", "quality requirements absent", "warn"),
        ("SD4", "glossary absent", "warn"),
        ("SD6", "untagged capability", "advisory"),
        ("SD7", "design-target as live", "advisory"),
        ("SD8", "constraint contradiction", "advisory"),
        ("SD9", "actor drift", "advisory"),
        ("SD11", "contradictory guardrail", "error"),
        ("SD12", "count drift", "error"),
        ("SD13", "diagram without walkthrough", "warn"),
    }
)

#: ``dirty.md``, in emission order. Ordered with ``==``, not compared as a set: the order is what
#: an operator reads, and a reordering is a behaviour change a set comparison would not see.
DIRTY: list[Triple] = [
    ("SD1", "tier missing", "error"),
    ("SD1", "tier order", "error"),
    ("SD2", "coverage gap COV-01", "error"),
    ("SD2", "coverage gap COV-02", "warn"),
    ("SD2", "coverage gap COV-03", "error"),
    ("SD2", "coverage gap COV-04", "error"),
    ("SD2", "coverage gap COV-05", "error"),
    ("SD2", "coverage gap COV-06", "error"),
    ("SD2", "coverage gap COV-07", "warn"),
    ("SD2", "coverage gap COV-08", "warn"),
    ("SD2", "coverage gap COV-09", "warn"),
    ("SD2", "coverage gap COV-11", "error"),
    ("SD3", "quality requirements absent", "warn"),
    ("SD4", "glossary absent", "warn"),
    ("SD6", "untagged capability", "advisory"),
    ("SD7", "design-target as live", "advisory"),
    ("SD8", "constraint contradiction", "advisory"),
    ("SD9", "actor drift", "advisory"),
    ("SD12", "count drift", "error"),
    ("SD13", "diagram without walkthrough", "warn"),
]

SKILL_CONTRADICTION: list[Triple] = [("SD11", "contradictory guardrail", "error")]


def triples(findings: list[dl.Finding]) -> list[Triple]:
    return [(f.tier, f.rule, f.severity) for f in findings]


def _lint(fixture: str) -> list[Triple]:
    return triples(dl.lint((CORPUS / fixture).read_text(encoding="utf-8")))


def _lint_skill(fixture: str) -> list[Triple]:
    return triples(dl.lint_skill((CORPUS / fixture).read_text(encoding="utf-8")))


def test_dirty_fires_exactly() -> None:
    assert _lint("dirty.md") == DIRTY


def test_clean_fires_nothing() -> None:
    assert _lint("clean.md") == []


def test_clean_answers_every_coverage_dimension() -> None:
    """The positive control for SD2/SD3/SD4.

    ``test_clean_fires_nothing`` would also pass if every dimension were somehow skipped, so the
    coverage map is asserted directly: twelve dimensions, all ``present``, none ``not_verified``.
    """
    sections = dl.parse_sections((CORPUS / "clean.md").read_text(encoding="utf-8"))
    statuses = {status for _, _, status in dl.coverage_map(sections)}
    assert statuses == {"present"}
    assert len(dl.coverage_map(sections)) == len(dl.dimensions())


def test_skill_contradiction_fires_and_its_mirror_does_not() -> None:
    assert _lint_skill("skill-contradiction.md") == SKILL_CONTRADICTION
    assert _lint_skill("skill-clean.md") == []


def test_every_rule_is_tripped_by_the_corpus() -> None:
    """Set EQUALITY: a rule that stops firing is named, and a new rule must join the corpus."""
    fired = set(_lint("dirty.md")) | set(_lint_skill("skill-contradiction.md"))
    assert fired == INVENTORY


def test_the_inventory_names_every_coverage_dimension_sd2_can_report() -> None:
    """The instrument check on the literal above.

    ``INVENTORY`` is hand-written, so it can drift from ``coverage.toml`` in the one direction
    set-equality cannot see: if a dimension were dropped from the taxonomy AND from the literal
    in the same edit, both sides would agree and nothing would notice. This re-derives the SD2
    half from the taxonomy itself.
    """
    own_rule = {"COV-10", "COV-12"}  # reported by SD3 / SD4
    expected = {
        ("SD2", f"coverage gap {d.id}", d.severity) for d in dl.dimensions() if d.id not in own_rule
    }
    assert {t for t in INVENTORY if t[0] == "SD2"} == expected
    assert own_rule < {d.id for d in dl.dimensions()}


def test_suppression_is_part_of_the_corpus() -> None:
    """``lint-ok SDn`` must suppress the named rule and nothing else.

    Built from the committed dirty fixture rather than an inline string, so the escape hatch is
    exercised against the same document the goldens pin — and ``SD12``, a two-digit tier, is the
    one used, because a single-digit capture reads it as ``SD1`` and silences the wrong rule.
    """
    text = (CORPUS / "dirty.md").read_text(encoding="utf-8")
    suppressed = text.replace(
        "Three limits worth stating:",
        "<!-- lint-ok SD12: the third limit lives in the appendix -->\nThree limits worth stating:",
    )
    fired = triples(dl.lint(suppressed))
    assert ("SD12", "count drift", "error") not in fired
    assert ("SD1", "tier order", "error") in fired
    assert len(fired) == len(DIRTY) - 1


def test_the_dimensions_listing_names_every_dimension() -> None:
    """``--dimensions`` is what ``solution-discovery`` checks its question bank against.

    A listing that quietly dropped a dimension would take the question that sources it with it,
    so the printed text is matched against the taxonomy rather than pinned only as a golden.
    """
    printed = (support.CLI_DIR / "design-dimensions.stdout.txt").read_text(encoding="utf-8")
    for dimension in dl.dimensions():
        assert dimension.id in printed
        assert dimension.question in printed


def test_help_golden() -> None:
    support.check_help("design_lint")


@pytest.mark.parametrize("case", support.CASES["design_lint"], ids=lambda c: c.name)
def test_cli_golden(case: support.CliCase, tmp_path) -> None:
    support.check_case(case, tmp_path)
