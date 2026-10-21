from __future__ import annotations

import re

from .catalog import COMPONENTS, LAYOUTS, THEME_CLASSES
from .model import ERROR, WARN, Finding, Slide
from .text import _STYLE_BLOCK, _fit_estimate, _has_image, _has_visual

# ══════════════════════════════════════════════════════════════════════════════════════
# D7 — visual density
# ══════════════════════════════════════════════════════════════════════════════════════

PROSE_LINES_WITHOUT_VISUAL = 6
VISUAL_TEMPLATES = frozenset({"A4", "A6", "A7"})  # partner / platform decks must show, not tell


def _d7(slides: list[Slide], template: str | None) -> list[Finding]:
    severity = ERROR if (template or "").upper() in VISUAL_TEMPLATES else WARN
    out: list[Finding] = []
    for slide in slides:
        if slide.layout in {"chapter", "end", "statement"}:
            continue
        if _has_visual(slide):
            continue
        if _has_image(slide):
            out.append(
                Finding(
                    "D7",
                    "image is doing the explaining",
                    severity,
                    slide.index,
                    "background image, no visual component",
                    "a generated image can restate a headline; it cannot explain a mechanism. "
                    "Draw the structure instead (a crossing → FlowSequence, two spans → "
                    "ScopeMap, a constriction → GateFunnel, reuse vs rebuild → ReuseTrack, "
                    "self-attested vs verifiable → ProofContrast) and keep the image for "
                    "chapter dividers",
                )
            )
            continue
        prose = _fit_estimate(slide)
        if prose > PROSE_LINES_WITHOUT_VISUAL:
            out.append(
                Finding(
                    "D7",
                    "wall of prose",
                    severity,
                    slide.index,
                    f"~{prose} text lines",
                    "give it something to look at — route the content shape to a component "
                    "(a sequence → FlowTrack, layers → StackDiagram, before/after → DuoGrid, "
                    "a record → TerminalWindow). See references/slide-library.md",
                )
            )
    visuals = sum(1 for s in slides if _has_visual(s))
    if slides and visuals < len(slides) / 5:
        out.append(
            Finding(
                "D7",
                "deck is text",
                severity,
                0,
                f"{visuals} visual slides of {len(slides)}",
                "at least one visual per five slides",
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# D8 — render integrity
# ══════════════════════════════════════════════════════════════════════════════════════

_CLASS_USE = re.compile(r'class="([^"]+)"')
_CLASS_DEF = re.compile(r"\.([a-z][\w-]*)\s*(?=[,{ :])", re.IGNORECASE)
_COMPONENT_USE = re.compile(r"<([A-Z][A-Za-z0-9]*)\b")


def _d8(slides: list[Slide]) -> list[Finding]:
    out: list[Finding] = []
    for slide in slides:
        styles = "\n".join(_STYLE_BLOCK.findall(slide.body))
        defined = set(_CLASS_DEF.findall(styles))
        used: set[str] = set()
        for attr in _CLASS_USE.findall(_STYLE_BLOCK.sub("", slide.body)):
            used.update(attr.split())

        for cls in sorted(used - defined - THEME_CLASSES):
            out.append(
                Finding(
                    "D8",
                    "class with no rule",
                    WARN,
                    slide.index,
                    f".{cls}",
                    "no scoped rule and not a theme class — it renders as unstyled text",
                )
            )
        for cls in sorted(defined - used - THEME_CLASSES):
            if cls.startswith(("slidev", "deep")):
                continue
            out.append(
                Finding(
                    "D8",
                    "unused rule",
                    WARN,
                    slide.index,
                    f".{cls}",
                    "styled but never applied — leftover from an earlier revision",
                )
            )

        for comp in sorted(set(_COMPONENT_USE.findall(_STYLE_BLOCK.sub("", slide.body)))):
            if comp not in COMPONENTS:
                out.append(
                    Finding(
                        "D8",
                        "unknown component",
                        ERROR,
                        slide.index,
                        f"<{comp}>",
                        "not in the deck-theme catalog — it will render as nothing",
                    )
                )
        layout = slide.layout
        if layout not in LAYOUTS:
            out.append(
                Finding(
                    "D8",
                    "unknown layout",
                    ERROR,
                    slide.index,
                    layout,
                    f"pick one of: {', '.join(sorted(LAYOUTS))}",
                )
            )
    return out
