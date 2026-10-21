from __future__ import annotations

import re

from .model import ERROR, WARN, Finding, Slide
from .rules_density import _LOW_TEXT_LAYOUTS
from .text import _BANNER, _STYLE_BLOCK, LAYOUT_LINE_BUDGET, _fit_estimate, _text_of

# ══════════════════════════════════════════════════════════════════════════════════════
# D1 — fit budget
# ══════════════════════════════════════════════════════════════════════════════════════
#
# Calibrated against a rendered 980×551 canvas (scripts/deck_fit_probe.mjs). A `v-click`
# element still occupies its box, so clicks buy nothing here — that is the trap this rule
# exists to catch, and the reason six banners shipped clipped.

BANNER_MAX_CHARS = 180
BANNER_MAX_PER_SLIDE = 1


def _d1(slide: Slide) -> list[Finding]:
    out: list[Finding] = []
    banners = _BANNER.findall(_STYLE_BLOCK.sub("", slide.body))

    if len(banners) > BANNER_MAX_PER_SLIDE:
        out.append(
            Finding(
                "D1",
                "banner count",
                ERROR,
                slide.index,
                f"{len(banners)} banners",
                "one banner per slide — the second one is what clips off the canvas",
            )
        )
    for banner in banners:
        text = _text_of(banner)
        if len(text) > BANNER_MAX_CHARS:
            out.append(
                Finding(
                    "D1",
                    "banner length",
                    WARN,
                    slide.index,
                    text[:70] + "…",
                    f"{len(text)} chars — a punch banner is uppercase and wraps at ~70; "
                    f"past {BANNER_MAX_CHARS} it takes three lines and eats the margin",
                )
            )

    budget = LAYOUT_LINE_BUDGET.get(slide.layout, 21)
    estimate = _fit_estimate(slide)
    if estimate > budget:
        out.append(
            Finding(
                "D1",
                "over budget",
                WARN,
                slide.index,
                f"~{estimate} lines against a {budget}-line {slide.layout} budget",
                "cut copy or split the slide — v-click hides content but keeps its box, "
                "so clicks do not buy space. Confirm with scripts/deck_fit_probe.mjs",
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# D5 — notes schema
# ══════════════════════════════════════════════════════════════════════════════════════

MIN_NOTE_CHARS = 40
_NO_NOTE_LAYOUTS = frozenset({"chapter"})


def _d5(slides: list[Slide]) -> list[Finding]:
    out: list[Finding] = []
    for slide in slides:
        if slide.layout in _NO_NOTE_LAYOUTS:
            continue
        if len(slide.notes) < MIN_NOTE_CHARS:
            out.append(
                Finding(
                    "D5",
                    "no presenter note",
                    ERROR,
                    slide.index,
                    f"{len(slide.notes)} chars of notes",
                    "every slide carries how to deliver it, what not to say, and the "
                    "fallback if challenged — a slide with no note is unfinished",
                )
            )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# D6 — deck budget
# ══════════════════════════════════════════════════════════════════════════════════════

SLIDES_PER_MINUTE = 0.25  # 60 minutes → 15 slides, most of the hour theirs
_APPENDIX = re.compile(r"\bappendix\b", re.IGNORECASE)


def _d6(slides: list[Slide], minutes: int, allow_appendix: bool) -> list[Finding]:
    out: list[Finding] = []
    budget = max(5, round(minutes * SLIDES_PER_MINUTE))
    # The budget is derived from meeting MINUTES, so it should count the slides that consume
    # them. A chapter divider is on screen for about five seconds and an end card outlives
    # the talking, so charging them against a time budget is a category error — and it
    # pushes decks to drop the structural beats that make an hour navigable. Same exemption,
    # and same reasoning, as D10's word count.
    #
    # Capped at a quarter of the budget so the exemption cannot become a padding route: a
    # deck with more chrome than that is padding, and the surplus goes back on the clock.
    chrome = [s for s in slides if s.layout in _LOW_TEXT_LAYOUTS]
    exempt = min(len(chrome), budget // 4)
    charged = len(slides) - exempt
    if charged > budget:
        detail = f"{charged} slides for a {minutes}-minute meeting"
        if exempt:
            detail += f" ({len(slides)} total, {exempt} chrome not charged)"
        out.append(
            Finding(
                "D6",
                "over slide budget",
                ERROR,
                0,
                detail,
                f"{budget} maximum — cut or merge. Most of the meeting should be theirs",
            )
        )
    if not allow_appendix:
        for slide in slides:
            if _APPENDIX.search(slide.tag) or _APPENDIX.search(_text_of(slide.body)[:120]):
                out.append(
                    Finding(
                        "D6",
                        "appendix",
                        ERROR,
                        slide.index,
                        slide.tag or "appendix slide",
                        "no appendix by default — keep a trigger index in the account "
                        "folder to pull up on demand instead",
                    )
                )
    return out
