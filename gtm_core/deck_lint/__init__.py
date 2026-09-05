"""Gate a Slidev deck on the defects that survive a human read but not a meeting.

A partner deck for an ASEAN systems integrator took five review rounds in August 2026.
Not one of those rounds was about a typo. They were about: a banner clipped below the canvas (six times, on five
different slides); an argument pitched as risk reduction to a buyer whose own dossier says
governance clears his bar only as a deal-win; questions invented by the deck instead of
taken from the question bank sitting in the account folder; an outcome claim ("shorter
security review, more margin") with no precedent behind it; two regulator quotes that were
not in the source; and a buyer quote he never said.

Every one of those passed every existing gate, because no gate had an opinion about the
*artifact*. The outline gate approves slide titles, and you cannot reject an argument from
a title.

Ten tiers, split between what a machine can decide and what it cannot:

    D1  fit budget         — content that will not fit the canvas at max clicks     ERROR
    D2  question placement — dispersed, sourced, never lumped on the CTA            ERROR
    D3  dossier guardrails — the account's DON'T list, compiled                     ERROR
    D4  claim classes      — outcome / regulatory / borrowed / quoted               ERROR
    D5  notes schema       — a slide with no presenter note is an unfinished slide  ERROR
    D6  deck budget        — slide count against the meeting, appendices            ERROR
    D7  visual density     — prose with nothing to look at, or an image faking it   ERROR/WARN
    D8  render integrity   — a class with no rule, a component that does not exist  ERROR/WARN
    D9  render weight      — a theme effect Chromium can only rasterize             ERROR/WARN
    D10 word budget        — too much to read, or too little to mean anything        ERROR/WARN

D1 is deliberately a *static estimate*. The ground truth is `scripts/deck_fit_probe.mjs`,
which builds the deck and measures the real DOM — but node is denied on the VPS, so the
estimate is what runs everywhere and the probe is what calibrates it. When the two
disagree, the probe is right and the constants here are wrong.

D9 is different from D1–D8: it audits the shared **deck-theme source** (`.engine/deck-theme/`
in the deck workspace), not a specific `slides.md`. On 2026-08-14 a customer deck export was both
blurry and 24MB for 15 slides — not from anything an author wrote, but from four theme components
(`PulseHalo`, `MeshAurora`, `CanvasAmbience`, `GlassCard`'s `backdrop-filter`) and entrance-animation
keyframes ending on `filter: blur(0)` instead of `filter: none`. Chromium's print-to-PDF path has no
vector representation for `filter`, `backdrop-filter`, or `background-clip: text` — each becomes its
own rasterized bitmap, at whatever pixel size its compositor layer happened to have (not a
consistent DPI, which is why it read as *both* blurry and huge). Every author of every future deck
inherits the theme, so this has to be caught once, at the theme, not per-deck. See `lint_theme()`.

A rule that is genuinely wrong about a line can be suppressed with
`<!-- lint-ok D4: reason -->` inside that slide. Name the tier and say why.

CLI:
    uv run python -m gtm_core.deck_lint slides.md
    uv run python -m gtm_core.deck_lint slides.md --dossier dossier-spec.json --inputs inputs/
    uv run python -m gtm_core.deck_lint slides.md --minutes 60 --template A4 --json
    uv run python -m gtm_core.deck_lint --theme .engine/deck-theme          # D9 only
    uv run python -m gtm_core.deck_lint slides.md --theme .engine/deck-theme
"""

from __future__ import annotations

from .api import lint, report, verify_manifest  # noqa: F401
from .catalog import (  # noqa: F401
    COMPONENTS,
    DIAGRAM_COMPONENTS,
    GUARDED_AMBIENT_CLASSES,
    GUARDED_GRADIENT_TEXT_CLASSES,
    LAYOUTS,
    PHOTO_BOUND_CLASSES,
    THEME_CLASSES,
    VISUAL_COMPONENTS,
)
from .cli import main  # noqa: F401

# Eager, complete re-export of the pre-split module surface (PRD §5 rule 1):
# every submodule is imported here, so module-level registrations run on
# `import <package>` exactly as they did on `import <module>`.
from .model import ERROR, WARN, Finding, Slide  # noqa: F401
from .parse import (  # noqa: F401
    _FM_KEY,
    _NOTES,
    _looks_like_frontmatter,
    _parse_frontmatter,
    _slide_suppressions,
    headmatter,
    parse_slides,
)
from .rules_claims import (  # noqa: F401
    _KILLED,
    _OUTCOME,
    _PRECEDENT,
    _QUOTE,
    _REGULATOR,
    _VOLUNTARY,
    BORROWED_TERMS,
    UNIVERSAL_BANS,
    _d3,
    _d4,
    guardrails_from,
)
from .rules_density import (  # noqa: F401
    _LOW_TEXT_LAYOUTS,
    DECK_WORDS_AVG,
    DECK_WORDS_AVG_FRACTION,
    WORDS_PER_SLIDE,
    WORDS_PER_SLIDE_DIAGRAM,
    WORDS_PER_SLIDE_DIAGRAM_HARD,
    WORDS_PER_SLIDE_HARD,
    WORDS_PER_SLIDE_MIN,
    _d10,
    _deck_words_target,
)
from .rules_questions import (  # noqa: F401
    _ASKBOX_DIV,
    _ASKBOX_PROP,
    _ASKBOX_SLOT,
    _CTA_TAG,
    MIN_QUESTIONS,
    _d2,
    _normalise,
    _questions,
    question_bank,
    question_slides,
)
from .rules_structure import (  # noqa: F401
    _APPENDIX,
    _NO_NOTE_LAYOUTS,
    BANNER_MAX_CHARS,
    BANNER_MAX_PER_SLIDE,
    MIN_NOTE_CHARS,
    SLIDES_PER_MINUTE,
    _d1,
    _d5,
    _d6,
)
from .rules_visual import (  # noqa: F401
    _CLASS_DEF,
    _CLASS_USE,
    _COMPONENT_USE,
    PROSE_LINES_WITHOUT_VISUAL,
    VISUAL_TEMPLATES,
    _d7,
    _d8,
)
from .text import (  # noqa: F401
    _ARRAY_STRING,
    _BANNER,
    _CARD,
    _FLAT_PROP,
    _NON_TEXT_PROPS,
    _PROP_ARRAY,
    _STYLE_BLOCK,
    _TAG,
    CHARS_PER_LINE,
    LAYOUT_LINE_BUDGET,
    TWO_PANE_CHARS_PER_LINE,
    _fit_estimate,
    _flat_prop_text,
    _has_diagram,
    _has_image,
    _has_visual,
    _prop_text,
    _slide_words,
    _squash,
    _text_of,
    _wrapped_lines,
)
from .theme import (  # noqa: F401
    _BACKDROP_FILTER_DECL,
    _BG_CLIP_TEXT,
    _CLASS_IN_SELECTOR,
    _COMMENT,
    _FILTER_DECL,
    _KEYFRAMES,
    _RAW_FILTER,
    _STYLE_TAG,
    _brace_blocks,
    _keyframes_blocks,
    _strip_keyframes,
    _theme_css,
    lint_theme,
)

__all__ = [
    "ERROR",
    "WARN",
    "Finding",
    "Slide",
    "parse_slides",
    "headmatter",
    "COMPONENTS",
    "LAYOUTS",
    "VISUAL_COMPONENTS",
    "DIAGRAM_COMPONENTS",
    "THEME_CLASSES",
    "BANNER_MAX_CHARS",
    "BANNER_MAX_PER_SLIDE",
    "LAYOUT_LINE_BUDGET",
    "CHARS_PER_LINE",
    "TWO_PANE_CHARS_PER_LINE",
    "MIN_QUESTIONS",
    "UNIVERSAL_BANS",
    "BORROWED_TERMS",
    "MIN_NOTE_CHARS",
    "verify_manifest",
    "SLIDES_PER_MINUTE",
    "PROSE_LINES_WITHOUT_VISUAL",
    "VISUAL_TEMPLATES",
    "WORDS_PER_SLIDE",
    "WORDS_PER_SLIDE_HARD",
    "WORDS_PER_SLIDE_DIAGRAM",
    "WORDS_PER_SLIDE_DIAGRAM_HARD",
    "WORDS_PER_SLIDE_MIN",
    "DECK_WORDS_AVG",
    "DECK_WORDS_AVG_FRACTION",
    "GUARDED_AMBIENT_CLASSES",
    "PHOTO_BOUND_CLASSES",
    "GUARDED_GRADIENT_TEXT_CLASSES",
    "lint_theme",
    "lint",
    "question_bank",
    "guardrails_from",
    "question_slides",
    "report",
    "main",
]
