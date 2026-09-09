"""Tripwire corpus for ``gtm_core.deck_lint`` — every rule family fires, at its severity, or CI is red.

Read ``tests/tripwire/README.md`` first. The goldens below are the linter's behaviour BEFORE the
Phase 3 Cluster A split (PRD 2026-09-01 §6.2 V2); a pure-motion refactor must reproduce them
exactly — same rules, same severities, same order, same count.
"""

from __future__ import annotations

import pytest

from gtm_core import deck_lint as dl
from tests.tripwire import support

CORPUS = support.HERE / "deck_lint"

Triple = tuple[str, str, str]

#: Every (tier, rule, severity) ``deck_lint`` can emit. D7's severity is template-driven, so both
#: appear. D9 is the theme-level audit (``lint_theme``); it is listed with the per-deck tiers
#: because a split that forgets to wire it is exactly what this literal exists to catch.
INVENTORY: frozenset[Triple] = frozenset(
    {
        ("D1", "banner count", "error"),
        ("D1", "banner length", "warn"),
        ("D1", "over budget", "warn"),
        ("D2", "stacked questions", "error"),
        ("D2", "questions on the CTA", "error"),
        ("D2", "unsourced question", "error"),
        ("D2", "too few questions", "error"),
        ("D2", "no question bank", "warn"),
        ("D3", "guardrail", "error"),
        ("D4", "outcome claim", "error"),
        ("D4", "overstated outcome", "error"),
        ("D4", "regulatory status", "error"),
        ("D4", "unattributed term", "error"),
        ("D4", "unverified quote", "warn"),
        ("D4", "quote not in sources", "error"),
        ("D5", "no presenter note", "error"),
        ("D6", "over slide budget", "error"),
        ("D6", "appendix", "error"),
        ("D7", "wall of prose", "warn"),
        ("D7", "wall of prose", "error"),
        ("D7", "image is doing the explaining", "warn"),
        ("D7", "image is doing the explaining", "error"),
        ("D7", "deck is text", "warn"),
        ("D7", "deck is text", "error"),
        ("D8", "class with no rule", "warn"),
        ("D8", "unused rule", "warn"),
        ("D8", "unknown component", "error"),
        ("D8", "unknown layout", "error"),
        ("D9", "print guard missing", "error"),
        ("D9", "keyframes ends non-none filter", "error"),
        ("D9", "hardcoded backdrop-filter", "error"),
        ("D9", "unguarded filter", "error"),
        ("D9", "unguarded gradient text", "warn"),
        ("D10", "wall of words", "error"),
        ("D10", "over word budget", "warn"),
        ("D10", "under-explained", "error"),
        ("D10", "deck is dense", "warn"),
    }
)

# Findings for dirty.slides.md with no dossier and a 20-minute meeting. Slide 3 carries two
# banners under `<!-- lint-ok D1: … -->` and contributes NOTHING here — the suppression path is
# part of the golden.
DIRTY_BARE: list[Triple] = [
    ("D1", "banner count", "error"),
    ("D1", "banner length", "warn"),
    ("D1", "over budget", "warn"),
    ("D2", "stacked questions", "error"),
    ("D2", "questions on the CTA", "error"),
    ("D2", "too few questions", "error"),
    ("D2", "no question bank", "warn"),
    ("D5", "no presenter note", "error"),
    ("D6", "over slide budget", "error"),
    ("D6", "appendix", "error"),
    ("D7", "wall of prose", "warn"),
    ("D7", "image is doing the explaining", "warn"),
    ("D7", "deck is text", "warn"),
    ("D8", "class with no rule", "warn"),
    ("D8", "unused rule", "warn"),
    ("D8", "unknown component", "error"),
    ("D8", "unknown layout", "error"),
    ("D10", "under-explained", "error"),
    ("D10", "wall of words", "error"),
    ("D10", "over word budget", "warn"),
    ("D10", "deck is dense", "warn"),
]

# The same deck with the question bank supplied and template A4: the bank turns both CTA
# questions into "unsourced", drops the bank warning, and A4 promotes every D7 rule to ERROR.
DIRTY_A4_BANK: list[Triple] = [
    ("D1", "banner count", "error"),
    ("D1", "banner length", "warn"),
    ("D1", "over budget", "warn"),
    ("D2", "stacked questions", "error"),
    ("D2", "questions on the CTA", "error"),
    ("D2", "unsourced question", "error"),
    ("D2", "unsourced question", "error"),
    ("D2", "too few questions", "error"),
    ("D5", "no presenter note", "error"),
    ("D6", "over slide budget", "error"),
    ("D6", "appendix", "error"),
    ("D7", "wall of prose", "error"),
    ("D7", "image is doing the explaining", "error"),
    ("D7", "deck is text", "error"),
    ("D8", "class with no rule", "warn"),
    ("D8", "unused rule", "warn"),
    ("D8", "unknown component", "error"),
    ("D8", "unknown layout", "error"),
    ("D10", "under-explained", "error"),
    ("D10", "wall of words", "error"),
    ("D10", "over word budget", "warn"),
    ("D10", "deck is dense", "warn"),
]

# claims.slides.md against the dossier (bank + DON'T guardrails) and the sourced factoids: the
# first quote is verbatim in the corpus and passes; the second is a paraphrase in quotes.
CLAIMS_SOURCED: list[Triple] = [
    ("D3", "guardrail", "error"),  # universal ban: SOC 2
    ("D3", "guardrail", "error"),  # dossier DON'T: congratulate … Zorvane
    ("D4", "outcome claim", "error"),
    ("D4", "overstated outcome", "error"),
    ("D4", "regulatory status", "error"),
    ("D4", "unattributed term", "error"),
    ("D4", "quote not in sources", "error"),
]

# The same deck with no dossier and no inputs: no dossier DON'T, both quotes unverifiable.
CLAIMS_BARE: list[Triple] = [
    ("D2", "no question bank", "warn"),
    ("D3", "guardrail", "error"),
    ("D4", "outcome claim", "error"),
    ("D4", "overstated outcome", "error"),
    ("D4", "regulatory status", "error"),
    ("D4", "unattributed term", "error"),
    ("D4", "unverified quote", "warn"),
    ("D4", "unverified quote", "warn"),
]

THEME_BAD: list[Triple] = [
    ("D9", "print guard missing", "error"),
    ("D9", "keyframes ends non-none filter", "error"),
    ("D9", "hardcoded backdrop-filter", "error"),
    ("D9", "unguarded filter", "error"),
    ("D9", "unguarded gradient text", "warn"),
]

#: (run id, fixture, kwargs spec, expected ordered findings). A kwargs spec is either a dict or
#: a name resolved by ``_kwargs`` when it needs the dossier/inputs loaded from the corpus.
RUNS: list[tuple[str, str, str | dict, list[Triple]]] = [
    ("clean-sourced", "clean.slides.md", "sourced", []),
    ("dirty-bare", "dirty.slides.md", {"minutes": 20}, DIRTY_BARE),
    ("dirty-a4-bank", "dirty.slides.md", "a4-bank", DIRTY_A4_BANK),
    ("claims-sourced", "claims.slides.md", "sourced", CLAIMS_SOURCED),
    ("claims-bare", "claims.slides.md", {}, CLAIMS_BARE),
]


def _dossier_inputs() -> tuple[list[str], list[tuple[str, str]], str]:
    """Bank (+ inputs extension), guardrails and quote corpus, composed as ``main()`` composes them."""
    dossier = CORPUS / "dossier.json"
    corpus = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((CORPUS / "inputs").rglob("*.md"))
    )
    bank = dl.question_bank(dossier) + [
        ln.strip("-*# ").strip() for ln in corpus.split("\n") if ln.strip().endswith("?")
    ]
    return bank, dl.guardrails_from(dossier), corpus


def _kwargs(spec: str | dict) -> dict:
    if isinstance(spec, dict):
        return spec
    bank, rails, corpus = _dossier_inputs()
    if spec == "sourced":
        return {"bank": bank, "guardrails": rails, "corpus": corpus}
    if spec == "a4-bank":
        return {"minutes": 20, "template": "A4", "bank": bank}
    raise ValueError(spec)


def triples(findings: list[dl.Finding]) -> list[Triple]:
    return [(f.tier, f.rule, f.severity) for f in findings]


def _run(fixture: str, spec: str | dict) -> list[Triple]:
    return triples(dl.lint((CORPUS / fixture).read_text(encoding="utf-8"), **_kwargs(spec)))


@pytest.mark.parametrize(
    ("fixture", "spec", "expected"), [r[1:] for r in RUNS], ids=[r[0] for r in RUNS]
)
def test_fixture_fires_exactly(fixture: str, spec: str | dict, expected: list[Triple]) -> None:
    assert _run(fixture, spec) == expected


def test_theme_fixtures_fire_exactly() -> None:
    assert triples(dl.lint_theme(CORPUS / "theme-bad")) == THEME_BAD
    assert triples(dl.lint_theme(CORPUS / "theme-clean")) == []


def test_clean_fixture_fires_nothing() -> None:
    assert _run("clean.slides.md", "sourced") == []
    assert dl.question_slides(dl.parse_slides((CORPUS / "clean.slides.md").read_text())) == [
        2,
        3,
        4,
    ]


def test_every_rule_family_is_tripped_by_the_corpus() -> None:
    """Set EQUALITY: a family that stops firing is named, and a new family must join the corpus."""
    fired: set[Triple] = set()
    for _, fixture, spec, _ in RUNS:
        fired.update(_run(fixture, spec))
    fired.update(triples(dl.lint_theme(CORPUS / "theme-bad")))
    assert fired == INVENTORY


def test_catalog_tables_are_intact() -> None:
    """deck_lint has no rule registry; its rules read these tables. An under-populated table after
    a split weakens D7/D8/D9 without any rule going missing, so their contents are pinned."""
    assert dl.LAYOUTS == frozenset(
        {"chapter", "cover", "default", "end", "handoff", "pillars", "statement", "two-pane"}
    )
    assert dl.DIAGRAM_COMPONENTS == frozenset(
        {
            "EntityCrossing",
            "FlowSequence",
            "FlowTrack",
            "GateFunnel",
            "ProofContrast",
            "ReqAnchors",
            "ReuseTrack",
            "ScopeMap",
            "StackDiagram",
        }
    )
    assert dl.VISUAL_COMPONENTS == dl.DIAGRAM_COMPONENTS | frozenset(
        {
            "BreakTests",
            "CalloutRow",
            "ChatWindow",
            "ChipRow",
            "DuoGrid",
            "GlassCard",
            "ImpactRow",
            "PillarCard",
            "StatBlock",
            "StatsRow",
            "SurfaceGrid",
            "TerminalWindow",
        }
    )
    assert dl.COMPONENTS == dl.VISUAL_COMPONENTS | frozenset(
        {
            "AskBox",
            "Banner",
            "BrandMark",
            "BrandTag",
            "CanvasAmbience",
            "Eyebrow",
            "GradientText",
            "HeroTitle",
            "MeshAurora",
            "ParticleField",
            "PulseHalo",
            "SpeakerCard",
        }
    )
    assert sorted(dl.THEME_CLASSES) == [
        "anim-fade-up", "anim-scale-in", "anim-soft-fade", "askbox", "banner", "bk", "bl",
        "brk-head", "brks", "caps", "caps-wide", "caps-wider", "clean-list", "close-for",
        "close-gain", "display-callout", "divider", "font-display", "font-mono", "font-sans",
        "gradient-text", "gradient-text-product", "highlight", "lede", "mini-kicker", "ql",
        "scrim-bottom", "scrim-left", "scrim-radial", "slidev-code", "slidev-layout", "stagger",
    ]  # fmt: skip
    assert sorted(dl.GUARDED_AMBIENT_CLASSES) == [
        "canvas-ambience", "flow-beam", "grid-plane", "mesh-aurora", "orb", "pulse-halo",
        "pulse-ring", "scan-line", "slidev-vclick-hidden",
    ]  # fmt: skip
    assert dl.PHOTO_BOUND_CLASSES == frozenset({"bg-image"})
    assert sorted(dl.GUARDED_GRADIENT_TEXT_CLASSES) == [
        "cc-icon", "flow-label", "gradient-text", "gradient-text-product", "gt", "gt-animated",
        "gt-product", "impact-value", "number-stripe", "numeral", "phrase", "stat-number",
        "surface-index", "trigger-word",
    ]  # fmt: skip
    assert len(dl.UNIVERSAL_BANS) == 5
    assert [t[0] for t in dl.BORROWED_TERMS] == ["lethal trifecta"]


def test_help_golden() -> None:
    support.check_help("deck_lint")


@pytest.mark.parametrize("case", support.CASES["deck_lint"], ids=lambda c: c.name)
def test_cli_golden(case: support.CliCase, tmp_path) -> None:
    support.check_case(case, tmp_path)
