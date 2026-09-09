"""Deck-LEVEL consistency gate — the axis every existing deck check is blind to.

WHY THIS EXISTS. Three gates already guard a deck: ``deck_lint`` (per-slide correctness),
``scripts/deck_fit_probe.mjs`` (per-slide overflow) and ``scripts/deck_export_verify.py``
(per-file export integrity). Every one of them asks a question about ONE slide or ONE file.
Nothing asked a question about the DECK — and that is where the defect was hiding.

Measured 2026-08-29 across all 10 decks in the workspace, 377 component instances:

    GlassCard 97 · Eyebrow 87 · Banner 49 · HeroTitle 46 · GradientText 42  →  321 (85.1%)
    the other 29 components                                                 →   56
    11 components                                                           →    0 uses

Five components of 34 are 85% of every deck. ``GlassCard`` at 97 means most content is
*cards*, the lowest-information visual form available. Decks look alike because they are
alike, and no per-slide rule can see it: each of those slides is individually fine.

THE CAUSE WAS NOT TASTE. The composer's catalog documented 16 of the 34 shipped components.
Five more were filed under "Phase 3 components (not yet available)" while actually shipping,
12 were absent from the catalog entirely, and one (``HUDBar``) was documented but did not
exist. The composer could not reach what it was not told about. Fixing the catalog is the
unlock; this module is what stops it silting up again.

Rules are deliberately few and mechanical. A rule that needs the rendered CSS (accent count
per chapter, type-scale steps actually used) is NOT here: it cannot be answered from
``slides.md`` and a rule that guesses is worse than no rule.

    uv run python -m gtm_core.deck_consistency <slides.md> [--theme <dir>] [--strict] [--json]

Exit 1 on any error (or on any finding with ``--strict``). A rule that is genuinely wrong
about a deck can be suppressed with ``<!-- lint-ok C3: reason -->`` in the offending slide,
matching ``deck_lint``'s convention — naming the tier and the reason is the price.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from gtm_core.deck_lint import ERROR, WARN, Finding, Slide, parse_slides

__all__ = [
    "DEFAULT_FIVE",
    "component_census",
    "check",
    "main",
]

# ══════════════════════════════════════════════════════════════════════════════════════
# What counts as a component
# ══════════════════════════════════════════════════════════════════════════════════════

#: A PascalCase opening tag. Lowercase HTML elements can never match, which is the point.
_TAG = re.compile(r"<([A-Z][A-Za-z0-9]*)\b")

#: Slidev builtins + Vue intrinsics. Present in every deck without living in the theme's
#: components/ dir, so C6 must not report them as unknown. Sourced from
#: https://sli.dev/builtin/components (fetched 2026-08-29) plus Vue's own special elements.
_BUILTINS = frozenset(
    {
        # Slidev builtin components
        "Arrow",
        "AutoFitText",
        "BlueSky",
        "CodeGroup",
        "LightOrDark",
        "Link",
        "Mermaid",
        "Monaco",
        "PlantUml",
        "PoweredBySlidev",
        "RenderWhen",
        "SlideCurrentNo",
        "SlidesTotal",
        "SlidevVideo",
        "TitleRenderer",
        "Toc",
        "TocList",
        "Transform",
        "Tweet",
        "VClick",
        "VClicks",
        "VAfter",
        "VClickGap",
        "VDrag",
        "VDragArrow",
        "VSwitch",
        "Youtube",
        # Vue intrinsics
        "Transition",
        "TransitionGroup",
        "KeepAlive",
        "Suspense",
        "Teleport",
        "Component",
    }
)

#: The five components that were 85.1% of the corpus on 2026-08-29. Hardcoded deliberately:
#: C5 asks "did this deck reach past the defaults?", and that question needs a fixed
#: reference point, not one that drifts with whatever the last deck happened to use.
DEFAULT_FIVE = frozenset({"GlassCard", "Eyebrow", "Banner", "HeroTitle", "GradientText"})

#: CHROME — components the composer mandates or places structurally, not to carry an idea.
#: They are excluded from the C1/C2/C5 ratios, and the reason is a conflict this gate would
#: otherwise create with the composer's own rules: `Eyebrow` is required on EVERY slide
#: ("no exceptions for rhythm"), so on any deck that puts one substantial component per
#: slide, `Eyebrow` is ~45% of the census by construction. Counting it would make C1 fire on
#: decks that are doing exactly what the house style demands, and the only way to satisfy
#: both rules would be to pad slides with components nobody needs.
#:
#: Found by running this gate against its own fixture (tests/fixtures/deck-smoke), which is
#: what a fixture is for. The real corpus hid it: those decks stack 4-6 cards per slide, so
#: `Eyebrow` sat at 22-24% and never tripped the ceiling.
#:
#: `Banner` is deliberately NOT chrome — it carries a source citation or an editorial punch,
#: which is content, and a deck that is half Banner has a real problem.
CHROME = frozenset({"Eyebrow", "BrandMark", "BrandTag", "CanvasAmbience"})


def _is_chrome(name: str) -> bool:
    """True for CHROME plus any tenant-specific legacy brand-mark alias.

    A theme may keep a backward-compat shim named after its own brand (e.g. a
    pinned mark that ignores the active brand contract) — that shim is chrome
    by construction, but its name is tenant data, so it is matched by the
    generic `*Mark` suffix rather than hardcoded here.
    """
    return name in CHROME or name.endswith("Mark")


#: Below this many component instances a percentage is noise, not a signal — a 6-slide
#: teaser legitimately leans on two components. C1 stands down rather than firing wrongly.
_MIN_INSTANCES_FOR_RATIO = 20

#: A deck this long has room to vary; below it, breadth is a warning rather than an error.
_LONG_DECK_SLIDES = 12

_CONCENTRATION_CEILING = 0.35
_BREADTH_FLOOR = 8
_MAX_LAYOUT_RUN = 2
_MAX_HOLD_SLIDES = 3

_VALID_TEMPO = ("hold", "brisk", "burst")


#: `deck_lint.Slide.suppressed` matches `D\d+` only — it was written when D was the only
#: tier family, and widening it there would change which strings suppress D-tier findings.
#: This module parses its own C-tier suppressions instead, with the same `lint-ok <TIER>:
#: <reason>` spelling so authors learn one convention, not two.
_SUPPRESS = re.compile(r"lint-ok\s+(C\d+)")


def _suppressed(slide: Slide) -> set[str]:
    return {m.upper() for m in _SUPPRESS.findall(slide.body + slide.notes)}


def component_census(slides: list[Slide]) -> Counter[str]:
    """How many times each theme component is used across the deck body.

    Counts opening tags only, so ``<GlassCard>…</GlassCard>`` is one use. Builtins are
    excluded: they say nothing about whether the deck reached past the default five.
    """
    counts: Counter[str] = Counter()
    for slide in slides:
        for name in _TAG.findall(slide.body):
            if name not in _BUILTINS:
                counts[name] += 1
    return counts


def content_census(census: Counter[str]) -> Counter[str]:
    """The census with chrome removed — what C1/C2/C5 actually reason about.

    C6 keeps using the full census: an unknown component is a defect whether it is chrome
    or not.
    """
    return Counter({name: n for name, n in census.items() if not _is_chrome(name)})


def _theme_components(theme_dir: Path) -> set[str]:
    return {p.stem for p in (theme_dir / "components").glob("*.vue")}


# ══════════════════════════════════════════════════════════════════════════════════════
# Rules
# ══════════════════════════════════════════════════════════════════════════════════════


def _c1_concentration(census: Counter[str]) -> list[Finding]:
    """No single component may dominate the deck."""
    total = sum(census.values())
    if total < _MIN_INSTANCES_FOR_RATIO:
        return []
    name, count = census.most_common(1)[0]
    share = count / total
    if share <= _CONCENTRATION_CEILING:
        return []
    return [
        Finding(
            tier="C1",
            rule="component-concentration",
            severity=ERROR,
            slide=0,
            excerpt=(
                f"<{name}> is {count}/{total} component uses ({share:.0%}) — "
                f"ceiling is {_CONCENTRATION_CEILING:.0%}"
            ),
            fix=(
                f"Replace some <{name}> instances with a component that carries more "
                "information: a diagram (StackDiagram, FlowTrack, ScopeMap), a comparison "
                "(DuoGrid, ProofContrast), or numbers (StatsRow, ImpactRow). A deck built "
                "from one component reads as a template no matter how good the copy is."
            ),
        )
    ]


def _c2_breadth(slides: list[Slide], census: Counter[str]) -> list[Finding]:
    """A long deck has to reach past a handful of components."""
    distinct = len(census)
    if len(slides) >= _LONG_DECK_SLIDES:
        if distinct >= _BREADTH_FLOOR:
            return []
        severity, floor = ERROR, _BREADTH_FLOOR
    else:
        if distinct >= 5:
            return []
        severity, floor = WARN, 5
    return [
        Finding(
            tier="C2",
            rule="component-breadth",
            severity=severity,
            slide=0,
            excerpt=(
                f"{len(slides)} slides use only {distinct} distinct components "
                f"({', '.join(sorted(census)) or 'none'}) — floor is {floor}"
            ),
            fix=(
                "Open the component catalog in the composer knowledge file and pick shapes "
                "that fit the content. 34 components ship; a deck using fewer than "
                f"{floor} is almost always describing varied things in one shape."
            ),
        )
    ]


def _c3_layout_runs(slides: list[Slide]) -> list[Finding]:
    """Consecutive slides on the same layout are the visual equivalent of a monotone."""
    out: list[Finding] = []
    run_start = 0
    for i in range(1, len(slides) + 1):
        same = i < len(slides) and slides[i].layout == slides[run_start].layout
        if same:
            continue
        run = i - run_start
        if run > _MAX_LAYOUT_RUN:
            first = slides[run_start]
            out.append(
                Finding(
                    tier="C3",
                    rule="layout-run-length",
                    severity=ERROR,
                    slide=first.index,
                    excerpt=(
                        f"slides {first.index}–{slides[i - 1].index} all use "
                        f"layout '{first.layout}' ({run} in a row, max {_MAX_LAYOUT_RUN})"
                    ),
                    fix=(
                        "Break the run with a different layout — a chapter divider, a "
                        "statement, or a two-pane. If the run is deliberate (an intentional "
                        "drumbeat), suppress it with <!-- lint-ok C3: reason --> on the "
                        "first slide."
                    ),
                )
            )
        run_start = i
    return out


def _c4_tempo(slides: list[Slide]) -> list[Finding]:
    """Rhythm as data. Until the theme ships `tempo:`, absence is one note, not N."""
    tempos = [s.frontmatter.get("tempo", "").strip("\"'").lower() for s in slides]
    present = [t for t in tempos if t]
    if not present:
        return [
            Finding(
                tier="C4",
                rule="tempo-absent",
                severity=WARN,
                slide=0,
                excerpt="no slide declares a `tempo:` — every slide gets identical timing",
                fix=(
                    "Once the theme's tempo triple ships (PRD D2.2), give each slide "
                    "tempo: hold | brisk | burst. Until then this is informational: "
                    "uniform pacing is what makes a deck read as templated."
                ),
            )
        ]

    out: list[Finding] = []
    for slide, tempo in zip(slides, tempos, strict=True):
        if tempo and tempo not in _VALID_TEMPO:
            out.append(
                Finding(
                    tier="C4",
                    rule="tempo-unknown",
                    severity=ERROR,
                    slide=slide.index,
                    excerpt=f"tempo: {tempo!r} is not one of {', '.join(_VALID_TEMPO)}",
                    fix=f"Use one of: {', '.join(_VALID_TEMPO)}.",
                )
            )
    holds = sum(1 for t in present if t == "hold")
    if holds > _MAX_HOLD_SLIDES:
        out.append(
            Finding(
                tier="C4",
                rule="tempo-too-many-holds",
                severity=ERROR,
                slide=0,
                excerpt=f"{holds} slides are tempo: hold — max {_MAX_HOLD_SLIDES}",
                fix=(
                    "`hold` is the expensive treatment; it only reads as emphasis when it "
                    "is rare. Pick the 2–3 moments the deck actually rests on and demote "
                    "the rest to brisk."
                ),
            )
        )
    if "burst" not in present:
        out.append(
            Finding(
                tier="C4",
                rule="tempo-no-burst",
                severity=WARN,
                slide=0,
                excerpt="no slide uses tempo: burst — the deck has no fast beat",
                fix="Give lists, chip rows and dense grids tempo: burst so they land quickly.",
            )
        )
    return out


def _c5_catalog_reach(census: Counter[str]) -> list[Finding]:
    """Did this deck reach past the five components that were 85% of the 2026-08 corpus?"""
    if not census:
        return []
    beyond = set(census) - DEFAULT_FIVE
    if beyond:
        return []
    return [
        Finding(
            tier="C5",
            rule="default-five-only",
            severity=WARN,
            slide=0,
            excerpt=(
                "every component in this deck is one of the default five "
                f"({', '.join(sorted(DEFAULT_FIVE))})"
            ),
            fix=(
                "These five were 85% of the entire deck corpus on 2026-08-29. A deck built "
                "only from them is the corpus average by construction. Reach for a diagram, "
                "a comparison, or a stat row wherever the content is not literally a card."
            ),
        )
    ]


def _c6_unknown_components(census: Counter[str], theme_dir: Path | None) -> list[Finding]:
    """A component the theme does not ship renders as nothing at all."""
    if theme_dir is None:
        return []
    known = _theme_components(theme_dir)
    if not known:  # a wrong --theme path must fail loudly, not pass vacuously
        return [
            Finding(
                tier="C6",
                rule="theme-unreadable",
                severity=ERROR,
                slide=0,
                excerpt=f"no components found under {theme_dir}/components",
                fix="Point --theme at the deck-theme directory (it must contain components/*.vue).",
            )
        ]
    out: list[Finding] = []
    for name in sorted(set(census) - known):
        out.append(
            Finding(
                tier="C6",
                rule="unknown-component",
                severity=ERROR,
                slide=0,
                excerpt=f"<{name}> is used but the theme does not ship it",
                fix=(
                    f"Either the component was renamed, or the composer catalog is stale and "
                    f"lists something that does not exist. Vue renders an unknown component "
                    f"as nothing — the slide silently loses its content. Check "
                    f"{theme_dir}/components/."
                ),
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# Entry points
# ══════════════════════════════════════════════════════════════════════════════════════


def check(text: str, *, theme_dir: Path | None = None) -> list[Finding]:
    slides = parse_slides(text)
    census = component_census(slides)
    content = content_census(census)
    findings = (
        _c1_concentration(content)
        + _c2_breadth(slides, content)
        + _c3_layout_runs(slides)
        + _c4_tempo(slides)
        + _c5_catalog_reach(content)
        + _c6_unknown_components(census, theme_dir)
    )
    # Deck-level findings carry slide=0 and can be suppressed from any slide; slide-scoped
    # ones only from their own slide. Same `lint-ok <TIER>` spelling as deck_lint.
    suppressed_anywhere = {tier for s in slides for tier in _suppressed(s)}
    by_index = {s.index: _suppressed(s) for s in slides}
    return [
        f
        for f in findings
        if f.tier not in (suppressed_anywhere if f.slide == 0 else by_index.get(f.slide, set()))
    ]


def report(path: Path, findings: list[Finding], census: Counter[str]) -> None:
    content = content_census(census)
    total = sum(content.values())
    print(f"\n{path}")
    if not findings:
        print("  ✓ no deck-level consistency findings")
    for f in sorted(findings, key=lambda f: (f.slide, f.tier)):
        mark = "✗" if f.severity == ERROR else "!"
        where = f"slide {f.slide}" if f.slide else "deck"
        print(f"  {mark} [{f.tier} {f.rule}] {where}: {f.excerpt}")
        print(f"      → {f.fix}")
    if total:
        top = ", ".join(f"{n}×{c}" for n, c in content.most_common(6))
        print(f"\n  {len(content)} content components, {total} uses — {top}")
        chrome = sum(census.values()) - total
        if chrome:
            print(
                f"  ({chrome} chrome uses excluded from the ratios: "
                f"{', '.join(sorted(n for n in census if _is_chrome(n)))})"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deck-consistency")
    parser.add_argument("paths", nargs="+", type=Path, help="one or more slides.md")
    parser.add_argument(
        "--theme",
        type=Path,
        help="deck-theme dir — enables C6 (a component the theme does not ship renders as "
        "nothing). Defaults to docker/deck-renderer/deck-theme when it exists.",
    )
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    theme = args.theme
    if theme is None:
        default = Path(__file__).resolve().parents[1] / "docker" / "deck-renderer" / "deck-theme"
        if (default / "components").is_dir():
            theme = default

    failed = False
    payload: dict[str, list[dict]] = {}
    for path in args.paths:
        if not path.exists():
            print(f"✗ no such file: {path}", file=sys.stderr)
            failed = True
            continue
        text = path.read_text(encoding="utf-8")
        findings = check(text, theme_dir=theme)
        if args.as_json:
            payload[str(path)] = [f.__dict__ for f in findings]
        else:
            report(path, findings, component_census(parse_slides(text)))
        if any(f.severity == ERROR for f in findings) or (args.strict and findings):
            failed = True

    if args.as_json:
        print(json.dumps(payload, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
