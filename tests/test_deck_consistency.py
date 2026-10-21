"""Tests for the deck-LEVEL consistency gate.

Every fixture here is synthetic and fictional (docs/RULES.md §R9): invented companies,
invented numbers, no real person or contact. The shapes are real — the monoculture fixture
reproduces the concentration measured across the 2026-08-29 corpus — the content is not.

The most important test in this file is `test_a_monoculture_deck_fails_c1`. A gate that
passes on the corpus that motivated it has failed, and that is the one outcome no amount of
green elsewhere makes acceptable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.deck_consistency import (
    DEFAULT_FIVE,
    check,
    component_census,
    main,
)
from gtm_core.deck_lint import ERROR, WARN, parse_slides

HEADMATTER = """---
theme: ../../deck-theme
title: Fixture Deck
---
"""


def _slide(layout: str, body: str, *, tempo: str | None = None) -> str:
    fm = f"layout: {layout}\n"
    if tempo:
        fm += f"tempo: {tempo}\n"
    return f"\n---\n{fm}---\n\n{body}\n"


def _deck(*slides: str) -> str:
    return HEADMATTER + "".join(slides)


def _tiers(findings) -> set[str]:
    return {f.tier for f in findings}


def _rules(findings) -> set[str]:
    return {f.rule for f in findings}


# ── non-vacuity: the parser and census must work, or every test below is empty ────


def test_the_census_counts_theme_components_and_ignores_builtins():
    text = _deck(
        _slide("default", "<GlassCard>a</GlassCard>\n<GlassCard>b</GlassCard>\n<Toc />"),
        _slide(
            "statement",
            "<HeroTitle :lines=\"['x']\" />\n<VSwitch><template #1>y</template></VSwitch>",
        ),
    )
    census = component_census(parse_slides(text))
    assert census["GlassCard"] == 2, f"census parse drifted: {census}"
    assert census["HeroTitle"] == 1
    assert "Toc" not in census, "Slidev builtins must not count toward component breadth"
    assert "VSwitch" not in census


def test_lowercase_html_is_never_counted_as_a_component():
    census = component_census(parse_slides(_deck(_slide("default", "<div><p>hi</p></div>"))))
    assert census == {}, f"HTML elements leaked into the census: {census}"


# ── C1 concentration — the rule the corpus motivated ─────────────────────────────


def test_a_monoculture_deck_fails_c1():
    """The 2026-08-29 corpus shape: one component, overwhelming share.

    If this ever passes, the gate has been neutered and the 85.1% concentration it exists
    to catch would sail through. Treat a green here as a defect in the gate, not a win.
    """
    body = "\n".join(f"<GlassCard>point {i}</GlassCard>" for i in range(5))
    deck = _deck(*[_slide(f"l{i % 4}", body) for i in range(6)])
    findings = check(deck)
    c1 = [f for f in findings if f.tier == "C1"]
    assert c1, f"a 30-instance single-component deck must trip C1; got {_tiers(findings)}"
    assert c1[0].severity == ERROR
    assert "GlassCard" in c1[0].excerpt


def test_a_varied_deck_passes_c1():
    names = [
        "GlassCard",
        "StackDiagram",
        "DuoGrid",
        "StatsRow",
        "FlowTrack",
        "ScopeMap",
        "ProofContrast",
        "ChipRow",
        "ImpactRow",
        "Banner",
    ]
    slides = [_slide(f"l{i}", "\n".join(f"<{n} />" for n in names)) for i in range(3)]
    c1 = [f for f in check(_deck(*slides)) if f.tier == "C1"]
    assert not c1, f"an evenly-spread deck must not trip C1: {[f.excerpt for f in c1]}"


def test_chrome_is_excluded_from_the_concentration_ratio():
    """`Eyebrow` is mandated on EVERY slide by the composer ("no exceptions for rhythm").

    Counting it would make C1 fire on decks doing exactly what the house style demands, and
    the only way to satisfy both rules would be padding slides with components nobody needs.
    Found by running this gate against its own fixture — the real corpus hid it, because
    those decks stack 4-6 cards per slide so `Eyebrow` sat at 22-24%.
    """
    # One substantial component per slide + the mandated Eyebrow = Eyebrow is ~50% by count.
    slides = [
        _slide(f"l{i}", f"<Eyebrow>label</Eyebrow>\n<{n} />")
        for i, n in enumerate(
            [
                "ScopeMap",
                "GateFunnel",
                "ProofContrast",
                "ReuseTrack",
                "ReqAnchors",
                "BreakTests",
                "FlowSequence",
                "EntityCrossing",
                "DuoGrid",
                "StatsRow",
            ]
        )
    ]
    findings = check(_deck(*slides))
    assert not [f for f in findings if f.tier == "C1"], (
        "C1 fired on a deck with ten different content components, because the mandated "
        f"Eyebrow was counted: {[f.excerpt for f in findings if f.tier == 'C1']}"
    )


def test_excluding_chrome_does_not_weaken_c1_on_a_real_monoculture():
    """The regression that matters: chrome exclusion must sharpen C1, never soften it.

    On the 2026-08-29 corpus it did exactly that — one deck's GlassCard share went from 54%
    to 69% once the mandated Eyebrow left the denominator.
    """
    body = "<Eyebrow>label</Eyebrow>\n" + "\n".join(
        f"<GlassCard>p{i}</GlassCard>" for i in range(6)
    )
    findings = check(_deck(*[_slide(f"l{i % 4}", body) for i in range(5)]))
    c1 = [f for f in findings if f.tier == "C1"]
    assert c1, "chrome exclusion must not let a GlassCard monoculture through"
    assert "GlassCard" in c1[0].excerpt


def test_chrome_does_not_count_toward_breadth():
    """Otherwise a deck could reach the breadth floor on logos and labels."""
    body = "\n".join(f"<{n} />" for n in ["Eyebrow", "BrandMark", "BrandTag", "AcmeMark"])
    deck = _deck(*[_slide(f"l{i % 5}", body + "\n<GlassCard />") for i in range(14)])
    c2 = [f for f in check(deck) if f.tier == "C2"]
    assert c2 and c2[0].severity == ERROR, "chrome inflated the breadth count"


def test_c1_stands_down_on_a_short_deck():
    """Below the instance floor a percentage is noise — a teaser may lean on one shape."""
    deck = _deck(_slide("cover", "<GlassCard>a</GlassCard>\n<GlassCard>b</GlassCard>"))
    assert not [f for f in check(deck) if f.tier == "C1"]


# ── C2 breadth ───────────────────────────────────────────────────────────────────


def test_a_long_thin_deck_fails_c2_as_an_error():
    deck = _deck(
        *[_slide(f"l{i % 5}", "<GlassCard>x</GlassCard>\n<Eyebrow>y</Eyebrow>") for i in range(14)]
    )
    c2 = [f for f in check(deck) if f.tier == "C2"]
    assert c2 and c2[0].severity == ERROR, f"14 slides / 2 components must be a C2 error: {c2}"


def test_a_short_thin_deck_only_warns_on_c2():
    deck = _deck(*[_slide(f"l{i}", "<GlassCard>x</GlassCard>") for i in range(4)])
    c2 = [f for f in check(deck) if f.tier == "C2"]
    assert c2 and c2[0].severity == WARN


# ── C3 layout runs ───────────────────────────────────────────────────────────────


def test_three_consecutive_slides_on_one_layout_fail_c3():
    deck = _deck(*[_slide("pillars", "<PillarCard />") for _ in range(3)])
    c3 = [f for f in check(deck) if f.tier == "C3"]
    assert c3 and c3[0].severity == ERROR
    assert "pillars" in c3[0].excerpt


def test_two_consecutive_slides_on_one_layout_are_fine():
    deck = _deck(_slide("pillars", "<PillarCard />"), _slide("pillars", "<PillarCard />"))
    assert not [f for f in check(deck) if f.tier == "C3"]


def test_a_layout_run_can_be_suppressed_from_its_first_slide():
    run = [_slide("pillars", "<PillarCard />") for _ in range(3)]
    run[0] = run[0].replace(
        "<PillarCard />", "<!-- lint-ok C3: deliberate drumbeat -->\n<PillarCard />"
    )
    assert not [f for f in check(_deck(*run)) if f.tier == "C3"]


# ── C4 tempo ─────────────────────────────────────────────────────────────────────


def test_a_deck_with_no_tempo_gets_exactly_one_warning():
    """Absence is one note about the deck, not one per slide — an unreadable gate is a
    missing gate."""
    deck = _deck(*[_slide(f"l{i}", "<GlassCard />") for i in range(8)])
    c4 = [f for f in check(deck) if f.tier == "C4"]
    assert len(c4) == 1, f"expected a single deck-level note, got {[f.rule for f in c4]}"
    assert c4[0].rule == "tempo-absent" and c4[0].severity == WARN


def test_too_many_hold_slides_is_an_error():
    slides = [_slide(f"l{i}", "<GlassCard />", tempo="hold") for i in range(4)]
    slides.append(_slide("x", "<ChipRow />", tempo="burst"))
    c4 = _rules(f for f in check(_deck(*slides)) if f.tier == "C4")
    assert "tempo-too-many-holds" in c4


def test_an_unknown_tempo_value_is_an_error():
    deck = _deck(_slide("a", "<GlassCard />", tempo="dramatic"))
    bad = [f for f in check(deck) if f.rule == "tempo-unknown"]
    assert bad and bad[0].severity == ERROR


def test_a_deck_with_no_burst_warns():
    slides = [_slide(f"l{i}", "<GlassCard />", tempo="brisk") for i in range(3)]
    assert "tempo-no-burst" in _rules(f for f in check(_deck(*slides)) if f.tier == "C4")


# ── C5 catalog reach ─────────────────────────────────────────────────────────────


def test_a_deck_built_only_from_the_default_five_warns():
    body = "\n".join(f"<{n} />" for n in sorted(DEFAULT_FIVE))
    deck = _deck(*[_slide(f"l{i}", body) for i in range(3)])
    c5 = [f for f in check(deck) if f.tier == "C5"]
    assert c5 and c5[0].severity == WARN


def test_reaching_one_component_past_the_default_five_clears_c5():
    body = "\n".join(f"<{n} />" for n in sorted(DEFAULT_FIVE)) + "\n<ScopeMap />"
    deck = _deck(*[_slide(f"l{i}", body) for i in range(3)])
    assert not [f for f in check(deck) if f.tier == "C5"]


# ── C6 unknown component — the AskBox/HUDBar class of defect ──────────────────────


@pytest.fixture
def theme(tmp_path: Path) -> Path:
    root = tmp_path / "deck-theme"
    (root / "components").mkdir(parents=True)
    for name in ("GlassCard", "ScopeMap", "Eyebrow"):
        (root / "components" / f"{name}.vue").write_text("<template><div/></template>")
    return root


def test_a_component_the_theme_does_not_ship_is_an_error(theme: Path):
    """Vue renders an unknown component as nothing — the slide silently loses content.

    This is the shape that let `AskBox` reach a shipped deck the sidecar could not render,
    and the shape `HUDBar` (documented in the catalog, never built) would have produced.
    """
    deck = _deck(_slide("default", "<GlassCard /><HUDBar />"))
    c6 = [f for f in check(deck, theme_dir=theme) if f.tier == "C6"]
    assert c6 and c6[0].severity == ERROR
    assert "HUDBar" in c6[0].excerpt


def test_known_components_and_builtins_clear_c6(theme: Path):
    deck = _deck(_slide("default", "<GlassCard /><ScopeMap /><AutoFitText />"))
    assert not [f for f in check(deck, theme_dir=theme) if f.tier == "C6"]


def test_a_wrong_theme_path_fails_loud_rather_than_passing_empty(tmp_path: Path):
    """A gate that silently passes because it could not read its input is worse than none."""
    deck = _deck(_slide("default", "<Anything />"))
    c6 = [f for f in check(deck, theme_dir=tmp_path / "nope") if f.tier == "C6"]
    assert c6 and c6[0].rule == "theme-unreadable"


def test_c6_is_off_when_no_theme_is_given():
    deck = _deck(_slide("default", "<TotallyMadeUp />"))
    assert not [f for f in check(deck) if f.tier == "C6"]


# ── CLI ──────────────────────────────────────────────────────────────────────────


def test_cli_exits_nonzero_on_an_error(tmp_path: Path, capsys):
    path = tmp_path / "slides.md"
    body = "\n".join(f"<GlassCard>p{i}</GlassCard>" for i in range(6))
    path.write_text(_deck(*[_slide(f"l{i % 3}", body) for i in range(5)]), encoding="utf-8")
    assert main([str(path), "--theme", str(tmp_path / "absent")]) == 1


def test_cli_exits_zero_on_a_clean_deck(tmp_path: Path, capsys):
    names = [
        "GlassCard",
        "StackDiagram",
        "DuoGrid",
        "StatsRow",
        "FlowTrack",
        "ScopeMap",
        "ProofContrast",
        "ChipRow",
    ]
    body = "\n".join(f"<{n} />" for n in names)
    path = tmp_path / "slides.md"
    slides = [_slide(f"l{i}", body, tempo="brisk") for i in range(3)]
    slides.append(_slide("z", "<ChipRow />", tempo="burst"))
    path.write_text(_deck(*slides), encoding="utf-8")
    assert main([str(path)]) == 0
    assert "no deck-level consistency findings" in capsys.readouterr().out


def test_cli_strict_makes_warnings_fail(tmp_path: Path):
    names = [
        "GlassCard",
        "StackDiagram",
        "DuoGrid",
        "StatsRow",
        "FlowTrack",
        "ScopeMap",
        "ProofContrast",
        "ChipRow",
    ]
    body = "\n".join(f"<{n} />" for n in names)
    path = tmp_path / "slides.md"
    path.write_text(_deck(*[_slide(f"l{i}", body) for i in range(3)]), encoding="utf-8")
    assert main([str(path)]) == 0  # only the tempo-absent warning
    assert main([str(path), "--strict"]) == 1


def test_cli_json_mode_emits_findings(tmp_path: Path, capsys):
    import json as _json

    path = tmp_path / "slides.md"
    path.write_text(_deck(_slide("a", "<GlassCard />")), encoding="utf-8")
    main([str(path), "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert str(path) in payload


# ── the real theme is reachable, so C6's default is not dead code ─────────────────


def test_the_smoke_fixture_passes_the_gate():
    """The fixture that proves the sidecar renders must itself be a compliant deck.

    A gate that can only fail is half a gate. This is the positive control: if it ever goes
    red, either the fixture drifted or a rule started over-firing — and the fixture is also
    what `scripts/deck_sidecar_smoke.sh` renders, so a red here means the smoke test is
    testing a deck we would reject.
    """
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/deck-smoke/slides.md"
    assert fixture.is_file(), f"{fixture} is missing — the smoke script renders it"
    errors = [f for f in check(fixture.read_text(encoding="utf-8")) if f.severity == ERROR]
    assert not errors, f"the smoke fixture fails its own gate: {[f.excerpt for f in errors]}"


def test_the_smoke_fixture_exercises_the_components_that_were_missing():
    """The fixture's whole job is proving the nine components D0 restored actually render.

    Named explicitly so that trimming the fixture for convenience fails loudly instead of
    quietly turning the smoke test back into "did anything draw at all".
    """
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/deck-smoke/slides.md"
    census = component_census(parse_slides(fixture.read_text(encoding="utf-8")))
    restored = {
        "AskBox",
        "BreakTests",
        "EntityCrossing",
        "FlowSequence",
        "GateFunnel",
        "ProofContrast",
        "ReqAnchors",
        "ReuseTrack",
        "ScopeMap",
    }
    missing = restored - set(census)
    assert not missing, (
        f"the smoke fixture no longer uses {sorted(missing)} — these are exactly the "
        "components the sidecar was missing between 2026-08-14 and 2026-08-29, so dropping "
        "them makes the smoke test unable to detect that regression."
    )


@pytest.mark.private_tree  # docker/deck-renderer/ is not carved; the theme home is private
def test_the_repo_theme_is_where_the_cli_default_expects_it():
    """C6 defaults to the in-repo theme home. If that moves, the default silently disables
    the rule — which is the failure mode this whole PRD is about."""
    theme = Path(__file__).resolve().parents[1] / "docker" / "deck-renderer" / "deck-theme"
    assert (theme / "components").is_dir(), (
        f"{theme}/components is gone — deck_consistency's --theme default now resolves to "
        "nothing and C6 turns itself off. See docker/deck-renderer/deck-theme/REFRESH.md."
    )
    assert len(list((theme / "components").glob("*.vue"))) >= 30
