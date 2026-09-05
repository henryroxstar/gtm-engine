"""Tripwire corpus for ``gtm_core.brief_lint`` — every rule family fires, at its severity, or CI is red.

Read ``tests/tripwire/README.md`` first. The goldens are the linter's behaviour BEFORE the Phase 3
Cluster A split (PRD 2026-09-01 §6.2 V2); a pure-motion refactor must reproduce them exactly —
same rules, same severities, same order, same count.
"""

from __future__ import annotations

import pytest

from gtm_core import brief_lint as bl
from tests.tripwire import support

CORPUS = support.HERE / "brief_lint"

Triple = tuple[str, str, str]

#: Every (tier, rule, severity) ``brief_lint`` can emit. T3's rule names are parameterised by the
#: counted noun (sources / lanes / speakers) — all six shapes are real rule ids and all six fire.
INVENTORY: frozenset[Triple] = frozenset(
    {
        ("T1", "source id", "error"),
        ("T1", "module path", "error"),
        ("T1", "shell command", "error"),
        ("T1", "repo filename", "error"),
        ("T1", "internal rule ref", "error"),
        ("T1", "schema field", "error"),
        ("T1", "code constant", "error"),
        ("T1", "status token", "error"),
        ("T1", "snake_case in <code>", "error"),
        ("T2", "house jargon", "error"),
        ("T2", "internal tool name", "error"),
        ("T3", "stale sources count", "error"),
        ("T3", "stale lanes count", "error"),
        ("T3", "stale speakers count", "error"),
        ("T3", "stale sources total", "error"),
        ("T3", "stale lanes total", "error"),
        ("T3", "stale speakers total", "error"),
        ("T4", "unnamed section ref", "error"),
        ("T5", "figure with no source", "warn"),
        ("T6", "script tag", "error"),
        ("T6", "external asset", "error"),
        ("T6", "duplicate id", "error"),
        ("T6", "dangling anchor", "error"),
        ("T6", "unbalanced <div>", "error"),
        ("T6", "section missing from contents", "error"),
        ("T7", "long paragraph", "warn"),
        ("T7", "long sentence", "warn"),
        ("T7", "bold overload", "warn"),
        ("T8", "section missing audience", "error"),
        ("T8", "unknown audience", "error"),
        ("T8", "operator content before reader content", "error"),
        ("T9", "headline-about-method", "error"),
        ("T9", "callout buries its takeaway", "error"),
        ("T9", "caveats before content", "error"),
        ("T9", "actions not split by team", "error"),
        ("T10", "class-not-styled", "error"),
        ("T10", "glued-inline", "error"),
        ("T11", "count-disagrees-across-tables", "error"),
        ("T11", "cross-section-assertion", "warn"),
        ("T12", "unreplaced-placeholder", "error"),
        ("T12", "template-scaffolding", "error"),
        ("T13", "styled-child-missing", "error"),
        ("T13", "list-container-not-a-list", "error"),
        ("T14", "uncited row", "error"),
        ("T15", "missing required section", "error"),
    }
)

# Order is the linter's: T3, T4 first (both surfaces), then the reader-register tiers.
JARGON: list[Triple] = [
    ("T3", "stale speakers count", "error"),
    ("T3", "stale sources count", "error"),
    ("T3", "stale lanes count", "error"),
    ("T3", "stale sources count", "error"),
    ("T3", "stale lanes count", "error"),
    ("T3", "stale speakers count", "error"),
    ("T3", "stale sources total", "error"),
    ("T3", "stale lanes total", "error"),
    ("T3", "stale speakers total", "error"),
    ("T4", "unnamed section ref", "error"),
    ("T4", "unnamed section ref", "error"),
    ("T4", "unnamed section ref", "error"),
    ("T1", "source id", "error"),
    ("T1", "source id", "error"),
    ("T1", "module path", "error"),
    ("T1", "module path", "error"),
    ("T1", "shell command", "error"),
    ("T1", "repo filename", "error"),
    ("T1", "repo filename", "error"),
    ("T1", "internal rule ref", "error"),
    ("T1", "schema field", "error"),
    ("T1", "schema field", "error"),
    ("T1", "code constant", "error"),
    ("T1", "status token", "error"),
    ("T1", "snake_case in <code>", "error"),
    ("T1", "snake_case in <code>", "error"),
    ("T1", "snake_case in <code>", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "house jargon", "error"),
    ("T2", "internal tool name", "error"),
    ("T5", "figure with no source", "warn"),
    ("T7", "long paragraph", "warn"),
    ("T7", "long sentence", "warn"),
    ("T7", "bold overload", "warn"),
]

STRUCTURE: list[Triple] = [
    ("T7", "long sentence", "warn"),
    ("T6", "script tag", "error"),
    ("T6", "external asset", "error"),
    ("T6", "external asset", "error"),
    ("T6", "duplicate id", "error"),
    ("T6", "dangling anchor", "error"),
    ("T6", "unbalanced <div>", "error"),
    ("T6", "section missing from contents", "error"),
    ("T8", "unknown audience", "error"),
    ("T8", "section missing audience", "error"),
    ("T8", "operator content before reader content", "error"),
    ("T9", "callout buries its takeaway", "error"),
    ("T9", "caveats before content", "error"),
    ("T9", "actions not split by team", "error"),
    ("T9", "actions not split by team", "error"),
    ("T10", "class-not-styled", "error"),
    ("T10", "glued-inline", "error"),
    ("T12", "unreplaced-placeholder", "error"),
    ("T12", "unreplaced-placeholder", "error"),
    ("T12", "template-scaffolding", "error"),
    ("T12", "template-scaffolding", "error"),
    ("T13", "styled-child-missing", "error"),
    ("T13", "list-container-not-a-list", "error"),
    ("T11", "count-disagrees-across-tables", "error"),
    ("T11", "cross-section-assertion", "warn"),
    ("T14", "uncited row", "error"),
]

# A masthead (<h1>) switches T15 on; one section present, sixteen required ones missing.
SECTIONS: list[Triple] = [("T9", "headline-about-method", "error")] + [
    ("T15", "missing required section", "error")
] * 16

# The markdown record surface: only T3/T4 apply — its `gtm_core.voc.delta` is NOT a finding.
RECORD: list[Triple] = [
    ("T3", "stale lanes count", "error"),
    ("T3", "stale sources count", "error"),
    ("T3", "stale sources count", "error"),
    ("T3", "stale sources total", "error"),
    ("T4", "unnamed section ref", "error"),
]

#: (fixture, surface, expected ordered findings)
RUNS: list[tuple[str, str, list[Triple]]] = [
    ("clean.html", bl.READER, []),
    ("jargon.html", bl.READER, JARGON),
    ("structure.html", bl.READER, STRUCTURE),
    ("sections.html", bl.READER, SECTIONS),
    ("record.md", bl.RECORD, RECORD),
]


def triples(findings: list[bl.Finding]) -> list[Triple]:
    return [(f.tier, f.rule, f.severity) for f in findings]


def _run(fixture: str, surface: str) -> list[Triple]:
    return triples(bl.lint((CORPUS / fixture).read_text(encoding="utf-8"), surface))


@pytest.mark.parametrize(("fixture", "surface", "expected"), RUNS, ids=[r[0] for r in RUNS])
def test_fixture_fires_exactly(fixture: str, surface: str, expected: list[Triple]) -> None:
    assert _run(fixture, surface) == expected


def test_clean_fixture_fires_nothing() -> None:
    """All 17 required sections, every row cited, and jargon ONLY inside the operator appendix —
    the operator-range exemption is part of what "clean" proves."""
    assert _run("clean.html", bl.READER) == []
    assert "provenance" in (CORPUS / "clean.html").read_text(encoding="utf-8")


def test_every_rule_family_is_tripped_by_the_corpus() -> None:
    """Set EQUALITY: a family that stops firing is named, and a new family must join the corpus."""
    fired: set[Triple] = set()
    for fixture, surface, _ in RUNS:
        fired.update(_run(fixture, surface))
    assert fired == INVENTORY


def test_rule_tables_are_intact() -> None:
    """brief_lint has no registry; its tiers read these tables. Pin what a split could under-populate."""
    assert [rule for _, rule, _ in bl.IDENTIFIER_RULES] == [
        "module path",
        "shell command",
        "repo filename",
        "internal rule ref",
        "schema field",
        "schema field",
        "code constant",
        "status token",
    ]
    assert len(bl.VOCABULARY) == 22
    assert bl.VENDOR_TOOLS == ("Syften", "Firecrawl", "Slidev", "Higgsfield", "WebFetch")
    assert bl.AUDIENCES == frozenset({"all", "marketing", "sales", "product", "operator"})
    assert [label for label, _ in bl.REQUIRED_READER_SECTIONS] == [
        "00 Since last issue",
        "01 Who we heard from",
        "02 What buyers say",
        "03 What people say about us",
        "04b Competitor moves",
        "04c Regulatory & enforcement clock",
        "04d Proof points",
        "04e Account moves",
        "05 Are we aligned?",
        "06 What to validate",
        "07 Standards watch",
        "08 What this means for each team",
    ]
    assert [label for label, _ in bl.REQUIRED_READER_APPENDICES] == [
        "App. A Sources & how to read them",
        "App. B Evidence",
        "App. C SEC search",
        "App. D Glossary",
        "App. E Operator notes",
    ]
    assert [label for label, _ in bl._T14_CITED_SECTIONS] == [
        "02 What buyers say",
        "04b Competitor moves",
        "04c Regulatory & enforcement clock",
        "04d Proof points",
    ]


def test_file_relative_root_still_points_at_the_repo() -> None:
    """PRD §6.1 row 5 names brief_lint.py:128 — ``parents[1]`` resolves one level deeper once the
    module becomes ``brief_lint/__init__.py``."""
    assert bl.ROOT == support.REPO_ROOT


def test_help_golden() -> None:
    support.check_help("brief_lint")


@pytest.mark.parametrize("case", support.CASES["brief_lint"], ids=lambda c: c.name)
def test_cli_golden(case: support.CliCase, tmp_path) -> None:
    support.check_case(case, tmp_path)
