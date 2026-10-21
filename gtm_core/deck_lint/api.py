from __future__ import annotations

from pathlib import Path

from .model import ERROR, Finding, Slide
from .parse import _slide_suppressions, parse_slides
from .rules_claims import _d3, _d4
from .rules_density import _d10
from .rules_questions import _d2
from .rules_structure import _d1, _d5, _d6
from .rules_visual import _d7, _d8


def verify_manifest(slides: list[Slide]) -> list[tuple[int, str]]:
    """Every ⚠️ line in the notes, collected — the pre-send check-before-you-present list."""
    out: list[tuple[int, str]] = []
    for slide in slides:
        for line in slide.notes.split("\n"):
            if "⚠️" in line:
                out.append((slide.index, line.strip()))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════════════


def lint(
    text: str,
    *,
    bank: list[str] | None = None,
    guardrails: list[tuple[str, str]] | None = None,
    corpus: str | None = None,
    minutes: int = 60,
    template: str | None = None,
    allow_appendix: bool = False,
) -> list[Finding]:
    slides = parse_slides(text)
    findings: list[Finding] = []
    for slide in slides:
        if "D1" not in slide.suppressed:
            findings += _d1(slide)
    findings += _d2(slides, bank)
    findings += _d3(slides, guardrails or [])
    findings += _d4(slides, corpus)
    findings += _d5(slides)
    findings += _d6(slides, minutes, allow_appendix)
    findings += _d7(slides, template)
    findings += _d8(slides)
    findings += _d10(slides)
    return [f for f in findings if f.tier not in _slide_suppressions(slides, f)]


def report(
    path: Path,
    findings: list[Finding],
    manifest: list[tuple[int, str]],
    questions: list[int] | None = None,
) -> None:
    if not findings:
        print(f"✓ {path} — clean")
    else:
        print(f"\n{path}")
        for f in sorted(findings, key=lambda f: (f.slide, f.tier)):
            mark = "✗" if f.severity == ERROR else "!"
            where = f"slide {f.slide}" if f.slide else "deck"
            print(f"  {mark} [{f.tier} {f.rule}] {where}: {f.excerpt}")
            print(f"      → {f.fix}")
    if manifest:
        print("\n  Verify before presenting:")
        for slide, line in manifest:
            print(f"    slide {slide}: {line}")
    # Not a finding — nothing here is wrong. It is printed because the ONE thing that reliably
    # goes stale in a deck is a slide NUMBER: reorder two slides and every "questions are on
    # 04, 07, 10" reference in the frontmatter, the notes and the approved-questions register is
    # silently wrong, with no rule that can catch it. Printing where the questions actually are,
    # every run, makes that drift visible without spending a warning on a healthy deck.
    if questions:
        print(
            "\n  Questions are on: "
            + ", ".join(f"{n:02d}" for n in questions)
            + "\n    check this against the deck's approved-questions register and any "
            "'questions are on …' line in the frontmatter or notes"
        )
