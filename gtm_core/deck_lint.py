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
in the deck workspace), not a specific `slides.md`. On 2026-08-14 the Appistoki export was both
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

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ERROR = "error"
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    tier: str
    rule: str
    severity: str
    slide: int
    excerpt: str
    fix: str


# ══════════════════════════════════════════════════════════════════════════════════════
# Parsing — a Slidev deck is headmatter, then slides separated by `---`, each optionally
# carrying its own frontmatter and ending with an HTML comment that is the presenter note.
# ══════════════════════════════════════════════════════════════════════════════════════


@dataclass
class Slide:
    index: int  # 1-based, as Slidev numbers them
    line: int  # 1-based line in the source file where the slide body starts
    frontmatter: dict[str, str] = field(default_factory=dict)
    body: str = ""
    notes: str = ""

    @property
    def layout(self) -> str:
        return self.frontmatter.get("layout", "default")

    @property
    def clicks(self) -> int:
        try:
            return int(self.frontmatter.get("clicks", "0"))
        except ValueError:
            return 0

    @property
    def tag(self) -> str:
        return self.frontmatter.get("tag", "").strip("\"'")

    @property
    def suppressed(self) -> set[str]:
        # `D\d+`, not `D\d` — with a two-digit tier in play (D10) a single-digit capture would
        # read `lint-ok D10` as `D1` and suppress the wrong rule while looking like it worked.
        return {m.upper() for m in re.findall(r"lint-ok\s+(D\d+)", self.body + self.notes)}


_FM_KEY = re.compile(r"^[A-Za-z_][\w-]*:")
_NOTES = re.compile(r"<!--(?!\s*lint-ok)(.*?)-->", re.DOTALL)


def _looks_like_frontmatter(block: list[str]) -> bool:
    """A frontmatter block is key: value lines (plus continuations and comments)."""
    meaningful = [ln for ln in block if ln.strip() and not ln.lstrip().startswith("#")]
    if not meaningful:
        return False
    return any(_FM_KEY.match(ln) for ln in meaningful) and all(
        _FM_KEY.match(ln) or ln.startswith((" ", "\t", "-")) for ln in meaningful
    )


def _parse_frontmatter(block: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in block:
        if ln.lstrip().startswith("#") or not ln.strip():
            continue
        if _FM_KEY.match(ln):
            key, _, value = ln.partition(":")
            out[key.strip()] = value.strip()
    return out


def parse_slides(text: str) -> list[Slide]:
    """Split a `slides.md` into slides. Fenced code blocks never contain separators."""
    lines = text.split("\n")
    fences: set[int] = set()
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            fences.add(i)

    seps = [i for i, ln in enumerate(lines) if ln.strip() == "---" and i not in fences]

    slides: list[Slide] = []
    cursor = 0
    pending_fm: dict[str, str] = {}
    slide_start = 0

    def flush(end: int) -> None:
        body_lines = lines[slide_start:end]
        raw = "\n".join(body_lines)
        notes = ""
        found = _NOTES.findall(raw)
        if found:
            notes = found[-1].strip()
            raw = _NOTES.sub("", raw)
        slides.append(
            Slide(
                index=len(slides) + 1,
                line=slide_start + 1,
                frontmatter=dict(pending_fm),
                body=raw.strip(),
                notes=notes,
            )
        )

    i = 0
    while i < len(seps):
        sep = seps[i]
        # Does a frontmatter block open here (this separator and the next one bracket it)?
        if i + 1 < len(seps):
            block = lines[sep + 1 : seps[i + 1]]
            if _looks_like_frontmatter(block):
                if slides or cursor:  # not the headmatter — close the previous slide first
                    flush(sep)
                pending_fm = _parse_frontmatter(block)
                slide_start = seps[i + 1] + 1
                cursor = slide_start
                i += 2
                continue
        # A bare separator: end of slide.
        if cursor:
            flush(sep)
            pending_fm = {}
            slide_start = sep + 1
            cursor = slide_start
        i += 1

    if cursor and slide_start < len(lines):
        flush(len(lines))
    return slides


def headmatter(text: str) -> dict[str, str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return _parse_frontmatter(lines[1:i])
    return {}


# ══════════════════════════════════════════════════════════════════════════════════════
# Catalog — what the deck-theme actually ships. A name outside this set will not render.
# ══════════════════════════════════════════════════════════════════════════════════════

COMPONENTS: frozenset[str] = frozenset(
    {
        "AskBox",
        "Banner",
        "BrandMark",
        "BrandTag",
        "BreakTests",
        "CalloutRow",
        "CanvasAmbience",
        "ChatWindow",
        "ChipRow",
        "DuoGrid",
        "EntityCrossing",
        "Eyebrow",
        "FlowSequence",
        "FlowTrack",
        "GateFunnel",
        "GlassCard",
        "GradientText",
        "HeroTitle",
        "ImpactRow",
        "MeshAurora",
        "ParticleField",
        "PillarCard",
        "ProofContrast",
        "PulseHalo",
        "ReqAnchors",
        "ReuseTrack",
        "ScopeMap",
        "SpeakerCard",
        "StackDiagram",
        "StatBlock",
        "StatsRow",
        "SurfaceGrid",
        "TerminalWindow",
    }
)

LAYOUTS: frozenset[str] = frozenset(
    {"chapter", "cover", "default", "end", "handoff", "pillars", "statement", "two-pane"}
)

# Components that give a slide something to look at. A Banner is a caption, not a visual.
VISUAL_COMPONENTS: frozenset[str] = frozenset(
    {
        "BreakTests",
        "CalloutRow",
        "ChatWindow",
        "ChipRow",
        "DuoGrid",
        "EntityCrossing",
        "FlowSequence",
        "FlowTrack",
        "GateFunnel",
        "GlassCard",
        "ImpactRow",
        "PillarCard",
        "ProofContrast",
        "ReqAnchors",
        "ReuseTrack",
        "ScopeMap",
        "StackDiagram",
        "StatBlock",
        "StatsRow",
        "SurfaceGrid",
        "TerminalWindow",
    }
)

# The strict subset that draws a STRUCTURE — a path, a span, a boundary, a constriction.
# The distinction is not decorative: a GlassCard grid is something to look at, but only a
# schematic can carry an explanation that would otherwise be a paragraph. Slides built on
# one of these are allowed a larger word budget (D10), because a label sitting inside a
# diagram is read at a glance and a sentence is not.
DIAGRAM_COMPONENTS: frozenset[str] = frozenset(
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

# Classes the theme styles globally; a slide may use them without a scoped rule.
THEME_CLASSES: frozenset[str] = frozenset(
    {
        "anim-fade-up",
        "anim-scale-in",
        "anim-soft-fade",
        "askbox",
        "banner",
        "bk",
        "bl",
        "brk-head",
        "brks",
        "caps",
        "caps-wide",
        "caps-wider",
        "clean-list",
        "close-for",
        "close-gain",
        "display-callout",
        "divider",
        "font-display",
        "font-mono",
        "font-sans",
        "gradient-text",
        "gradient-text-product",
        "highlight",
        "lede",
        "mini-kicker",
        "ql",
        "scrim-bottom",
        "scrim-left",
        "scrim-radial",
        "slidev-code",
        "slidev-layout",
        "stagger",
    }
)


# ══════════════════════════════════════════════════════════════════════════════════════
# D1 — fit budget
# ══════════════════════════════════════════════════════════════════════════════════════
#
# Calibrated against a rendered 980×551 canvas (scripts/deck_fit_probe.mjs). A `v-click`
# element still occupies its box, so clicks buy nothing here — that is the trap this rule
# exists to catch, and the reason six banners shipped clipped.

BANNER_MAX_CHARS = 180
BANNER_MAX_PER_SLIDE = 1

# Usable text lines per layout, calibrated against measured headroom on a 980×551 canvas:
# at these numbers the slides the probe reports with <24px of headroom sit at budget, and
# a slide one paragraph past them trips. Slides routinely override font sizes in scoped
# CSS, so this is an estimate with a real error bar — it warns, and the probe decides.
LAYOUT_LINE_BUDGET: dict[str, int] = {
    "cover": 21,
    "default": 21,
    "pillars": 18,
    "statement": 18,
    "two-pane": 21,
    "chapter": 8,
    "end": 10,
    "handoff": 12,
}

# Characters per rendered line. A two-pane gives ~66% of the canvas to text.
CHARS_PER_LINE = 108
TWO_PANE_CHARS_PER_LINE = 74

_TAG = re.compile(r"<[^>]+>")
_STYLE_BLOCK = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)
_BANNER = re.compile(r"<Banner\b[^>]*>(.*?)</Banner>", re.DOTALL | re.IGNORECASE)
_CARD = re.compile(r"<(GlassCard|PillarCard|StatBlock)\b", re.IGNORECASE)


def _text_of(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()


def _wrapped_lines(text: str, per_line: int) -> int:
    return max(1, math.ceil(len(text) / per_line)) if text else 0


def _fit_estimate(slide: Slide) -> int:
    """Estimated rendered text lines for a slide, excluding chrome."""
    body = _STYLE_BLOCK.sub("", slide.body)
    per_line = TWO_PANE_CHARS_PER_LINE if slide.layout == "two-pane" else CHARS_PER_LINE

    lines = 0
    # Headline: <h2> wraps hard and sits at display size — 2 rendered lines per <br/>.
    for h in re.findall(r"<h[12][^>]*>(.*?)</h[12]>", body, re.DOTALL | re.IGNORECASE):
        lines += 2 * (h.count("<br") + 1)
    body = re.sub(r"<h[12][^>]*>.*?</h[12]>", "", body, flags=re.DOTALL | re.IGNORECASE)

    # Cards laid out in a grid cost one row each, not one per card.
    cards = len(_CARD.findall(body))
    if cards:
        lines += 4 * max(1, math.ceil(cards / 3))
        body = re.sub(
            r"<(GlassCard|PillarCard|StatBlock)\b.*?</\1>",
            "",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )

    for banner in _BANNER.findall(body):
        lines += _wrapped_lines(_text_of(banner), 70) + 1
    body = _BANNER.sub("", body)

    for para in re.findall(
        r"<(?:p|li|blockquote)\b[^>]*>(.*?)</(?:p|li|blockquote)>", body, re.DOTALL | re.IGNORECASE
    ):
        lines += _wrapped_lines(_text_of(para), per_line)

    return lines


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
# D2 — question placement
# ══════════════════════════════════════════════════════════════════════════════════════

MIN_QUESTIONS = 3
# Three shapes: the component (self-closing, question as a prop), the component with a slot,
# and the hand-rolled div that predates the component.
_ASKBOX_PROP = re.compile(r"<AskBox\b[^>]*\bquestion=\"([^\"]+)\"", re.IGNORECASE)
_ASKBOX_SLOT = re.compile(r"<AskBox\b[^>]*>(.*?)</AskBox>", re.DOTALL | re.IGNORECASE)
_ASKBOX_DIV = re.compile(
    r"<div[^>]*\bclass=\"[^\"]*\baskbox\b[^\"]*\"[^>]*>(.*?)</div>", re.DOTALL | re.IGNORECASE
)
_CTA_TAG = re.compile(r"\b(the ask|ask|next step|cta)\b", re.IGNORECASE)


def _questions(slide: Slide) -> list[str]:
    found: list[str] = []
    for raw in (
        *_ASKBOX_PROP.findall(slide.body),
        *_ASKBOX_SLOT.findall(slide.body),
        *_ASKBOX_DIV.findall(slide.body),
    ):
        text = _text_of(raw)
        text = re.sub(r"^(A question for you|Question)\s*", "", text, flags=re.IGNORECASE)
        if text:
            found.append(text.strip())
    return found


def _normalise(q: str) -> set[str]:
    stop = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "do",
        "does",
        "you",
        "your",
        "of",
        "in",
        "to",
        "and",
        "that",
        "what",
        "how",
        "it",
        "for",
        "on",
        "at",
        "with",
        "would",
        "if",
    }
    return {w for w in re.findall(r"[a-z]{3,}", q.lower()) if w not in stop}


def _d2(slides: list[Slide], bank: list[str] | None) -> list[Finding]:
    out: list[Finding] = []
    total = 0
    for slide in slides:
        qs = _questions(slide)
        total += len(qs)
        if len(qs) > 1:
            out.append(
                Finding(
                    "D2",
                    "stacked questions",
                    ERROR,
                    slide.index,
                    f"{len(qs)} questions",
                    "one question per slide — a list of questions reads as an interrogation",
                )
            )
        if qs and _CTA_TAG.search(slide.tag):
            out.append(
                Finding(
                    "D2",
                    "questions on the CTA",
                    ERROR,
                    slide.index,
                    qs[0][:70] + "…",
                    "move it to the slide whose claim it tests — the ask slide proposes, "
                    "it does not interrogate",
                )
            )
        if bank is not None:
            for q in qs:
                words = _normalise(q)
                if not any(len(words & _normalise(b)) >= 3 for b in bank):
                    out.append(
                        Finding(
                            "D2",
                            "unsourced question",
                            ERROR,
                            slide.index,
                            q[:70] + "…",
                            "every on-slide question comes from the account dossier's "
                            "question bank — do not invent one",
                        )
                    )
    if total < MIN_QUESTIONS:
        out.append(
            Finding(
                "D2",
                "too few questions",
                ERROR,
                0,
                f"{total} questions in the deck",
                f"at least {MIN_QUESTIONS}, dispersed — a deck that never asks anything "
                "is a broadcast, not a qualification instrument",
            )
        )
    if bank is None:
        out.append(
            Finding(
                "D2",
                "no question bank",
                WARN,
                0,
                "no --dossier supplied",
                "pass the account dossier so on-slide questions can be checked against "
                "its question bank",
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# D3 — dossier guardrails
# ══════════════════════════════════════════════════════════════════════════════════════
#
# Universal bans come from the product-accuracy rules; per-account bans are compiled from
# the dossier's DON'T column, which is where "never congratulate them on the award — PwC
# won it" lives.

UNIVERSAL_BANS: tuple[tuple[str, str], ...] = (
    (r"\bSOC\s?2\b", "ISO 27001 only — we do not hold SOC 2 Type II"),
    (
        r"single pane of glass",
        "no cross-estate console is productised; say 'one consistent "
        "evidence trail, exportable into what you already run'",
    ),
    (
        r"\b(?:MAS|IMDA|PDPA)\b[^.]{0,40}\b(?:requires|mandates|deadline|compels)\b",
        "these frameworks are voluntary — selling them as a mandate loses the room",
    ),
    (
        r"\bcertified (?:against|for|to)\b",
        "the product produces evidence; it is not itself certified — say 'evidence for X'",
    ),
    (
        r"\bdecentrali[sz]ed identity\b|\bverifiable credential\b|\bDIDs?\b",
        "vocabulary ban: say 'its own verified identity', 'who vouched for it'",
    ),
)


def _d3(slides: list[Slide], extra: list[tuple[str, str]]) -> list[Finding]:
    out: list[Finding] = []
    for pattern, fix in (*UNIVERSAL_BANS, *extra):
        rx = re.compile(pattern, re.IGNORECASE)
        for slide in slides:
            if "D3" in slide.suppressed:
                continue
            hit = rx.search(_text_of(_STYLE_BLOCK.sub("", slide.body)))
            if hit:
                out.append(Finding("D3", "guardrail", ERROR, slide.index, hit.group(0), fix))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# D4 — claim classes
# ══════════════════════════════════════════════════════════════════════════════════════

_OUTCOME = re.compile(
    r"\b(shorter|shorten|faster|quicker|cheaper|reduces?|cuts?|lifts?|improves?|higher|more)\b"
    r"[^.]{0,60}\b(review|cycle|procurement|margin|revenue|win[- ]rate|cost|time to)\b",
    re.IGNORECASE,
)
_PRECEDENT = re.compile(r"PRECEDENT:|https?://", re.IGNORECASE)
_REGULATOR = re.compile(r"\b(MAS|IMDA|PDPA|SAFR|EU AI Act|FINRA)\b")
_VOLUNTARY = re.compile(
    r"voluntary|non-binding|not (?:a )?regulat|no deadline|principles-based", re.IGNORECASE
)
_KILLED = re.compile(
    r"\b(kills?|killed|blocks? it|shuts? it down)\b[^.]{0,40}"
    r"\b(review|security|risk|compliance)\b|"
    r"\b(review|security|risk|compliance)\b[^.]{0,30}\b(kills?|killed)\b",
    re.IGNORECASE,
)
_QUOTE = re.compile(r"[\"“]([^\"”]{25,300})[\"”]")

BORROWED_TERMS: tuple[tuple[str, str, str], ...] = (
    (
        "lethal trifecta",
        "Willison",
        "credit Simon Willison on the slide — his term, and his "
        "name is the one their architects know",
    ),
)


def _d4(slides: list[Slide], corpus: str | None) -> list[Finding]:
    out: list[Finding] = []
    for slide in slides:
        if "D4" in slide.suppressed:
            continue
        body = _STYLE_BLOCK.sub("", slide.body)
        text = _text_of(body)

        hit = _OUTCOME.search(text)
        if hit and not _PRECEDENT.search(slide.notes):
            out.append(
                Finding(
                    "D4",
                    "outcome claim",
                    ERROR,
                    slide.index,
                    hit.group(0),
                    "an outcome claim needs a precedent in the notes (PRECEDENT: … or a "
                    "source link), or it must be reworded as a question the buyer answers",
                )
            )

        hit = _KILLED.search(text)
        if hit:
            out.append(
                Finding(
                    "D4",
                    "overstated outcome",
                    ERROR,
                    slide.index,
                    hit.group(0),
                    "review rarely kills an agent — it descopes it. Say what actually "
                    "happens: the cross-entity scope is cut and the agent ships smaller",
                )
            )

        if _REGULATOR.search(text) and not _VOLUNTARY.search(slide.notes):
            out.append(
                Finding(
                    "D4",
                    "regulatory status",
                    ERROR,
                    slide.index,
                    _REGULATOR.search(text).group(0),
                    "a slide citing a framework must carry its binding status in the notes "
                    "(voluntary / non-binding / no deadline) so the room is never oversold",
                )
            )

        for term, attribution, fix in BORROWED_TERMS:
            if term in text.lower() and attribution.lower() not in text.lower():
                out.append(Finding("D4", "unattributed term", ERROR, slide.index, term, fix))

        for quote in _QUOTE.findall(text):
            if len(quote.split()) < 6:
                continue
            if corpus is None:
                out.append(
                    Finding(
                        "D4",
                        "unverified quote",
                        WARN,
                        slide.index,
                        quote[:60] + "…",
                        "pass --inputs so quotes can be checked verbatim against the "
                        "sourced factoids file",
                    )
                )
            elif _squash(quote) not in _squash(corpus):
                out.append(
                    Finding(
                        "D4",
                        "quote not in sources",
                        ERROR,
                        slide.index,
                        quote[:60] + "…",
                        "this exact string is not in the deck's inputs — a paraphrase in "
                        "quotation marks is a fabricated quote",
                    )
                )
    return out


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


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


def verify_manifest(slides: list[Slide]) -> list[tuple[int, str]]:
    """Every ⚠️ line in the notes, collected — the pre-send check-before-you-present list."""
    out: list[tuple[int, str]] = []
    for slide in slides:
        for line in slide.notes.split("\n"):
            if "⚠️" in line:
                out.append((slide.index, line.strip()))
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


# ══════════════════════════════════════════════════════════════════════════════════════
# D7 — visual density
# ══════════════════════════════════════════════════════════════════════════════════════

PROSE_LINES_WITHOUT_VISUAL = 6
VISUAL_TEMPLATES = frozenset({"A4", "A6", "A7"})  # partner / platform decks must show, not tell


def _has_image(slide: Slide) -> bool:
    return "image:" in "\n".join(f"{k}: {v}" for k, v in slide.frontmatter.items())


def _has_visual(slide: Slide) -> bool:
    """
    A frontmatter `image:` deliberately does NOT count.

    It used to. That is how the Appistoki deck reached a state where six slides each had a
    generated background image, satisfied this rule, satisfied the D10 word budget, and
    explained less than the version they replaced — the image restated the headline and the
    prose that carried the argument had been deleted to make room for it. A background photo
    is atmosphere. Only a component puts information on the slide.
    """
    body = _STYLE_BLOCK.sub("", slide.body)
    return any(re.search(rf"<{c}\b", body) for c in VISUAL_COMPONENTS)


def _has_diagram(slide: Slide) -> bool:
    body = _STYLE_BLOCK.sub("", slide.body)
    return any(re.search(rf"<{c}\b", body) for c in DIAGRAM_COMPONENTS)


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


# ══════════════════════════════════════════════════════════════════════════════════════
# D10 — word budget
# ══════════════════════════════════════════════════════════════════════════════════════
#
# D1 asks "does it fit the canvas"; D7 asks "is there anything to look at". Neither asks
# "is anyone going to read this". The Appistoki deck passed all nine tiers at 1229 words
# across 15 slides — one slide carried 160 — because every one of them *fit*. A slide can be
# perfectly composed, fully sourced, inside its click budget, and still be a wall the buyer
# skims while you talk over it.
#
# The counted text is what the audience must read on the slide. Presenter notes are excluded
# deliberately: cutting a slide moves its argument into the spoken track, so notes getting
# LONGER as slides get shorter is the intended trade, not a regression.

WORDS_PER_SLIDE = 55
WORDS_PER_SLIDE_HARD = 90
# A diagram's labels are read at a glance, in the position that gives them meaning — a
# 90-word schematic is not a 90-word paragraph, and holding both to one budget is what
# pushed this deck toward slides that were short because they were empty.
WORDS_PER_SLIDE_DIAGRAM = 95
WORDS_PER_SLIDE_DIAGRAM_HARD = 135
# The floor. A slide can fail by saying too little just as easily as too much: a headline,
# a background image and nothing else passes every other tier while explaining nothing.
# Under this, with no diagram to carry the argument, the slide is decoration.
WORDS_PER_SLIDE_MIN = 22
# A deck-wide average is the honest measure — one dense evidence slide is fine, a deck of them
# is not. Raised from 45 when diagram labels started counting toward the total: a deck that
# draws its arguments legitimately carries more words than one that hides them in pictures,
# and the per-slide caps above catch a genuine wall long before this average moves.
DECK_WORDS_AVG = 80
# ...but a FLAT average was internally inconsistent with the per-slide budgets above, and the
# inconsistency mattered: a diagram slide is allowed 95 soft, which is ABOVE this 80, so a deck
# whose every slide passed individually could still fail the deck check — permanently, with no
# edit that clears it. A warning that cannot be cleared is worse than no warning: it trains the
# reader to skim the whole tier, which is where the real errors were the day this was found.
#
# So the target is derived per slide instead. 80 sits 71% of the way from the text soft budget
# (55) to its hard cap (90); apply that same fraction to whichever pair of budgets the slide is
# actually held to, and average those. A text-only deck still gets exactly 80 — this is a
# generalisation of the old constant, not a loosening of it.
DECK_WORDS_AVG_FRACTION = (DECK_WORDS_AVG - WORDS_PER_SLIDE) / (
    WORDS_PER_SLIDE_HARD - WORDS_PER_SLIDE
)


def _deck_words_target(slides: list[Slide]) -> float:
    """The deck-average target, weighted by how many slides legitimately carry diagram labels."""
    per_slide = []
    for slide in slides:
        diagram = _has_diagram(slide)
        soft = WORDS_PER_SLIDE_DIAGRAM if diagram else WORDS_PER_SLIDE
        hard = WORDS_PER_SLIDE_DIAGRAM_HARD if diagram else WORDS_PER_SLIDE_HARD
        per_slide.append(soft + DECK_WORDS_AVG_FRACTION * (hard - soft))
    return sum(per_slide) / len(per_slide) if per_slide else float(DECK_WORDS_AVG)


_LOW_TEXT_LAYOUTS = frozenset({"chapter", "end"})


# Text the audience reads often lives in a component *prop*, not in the markup body:
# `<AskBox question="…" />`, `<BreakTests :tests="[{claim: '…'}]" />`, `<StackDiagram :layers="…" />`.
# `_text_of` strips whole tags, so that text vanishes — which would make exactly the
# component-led slides this rule exists to police look empty. Count those strings too.
_PROP_ARRAY = re.compile(
    r":(?:tests|layers|items|nodes|lines|cards|stats|rows|steps|pillars"
    r"|checks|entities|gaps|blockers|columns|zones|anchors)=\"(\[.*?\])\"",
    re.DOTALL | re.IGNORECASE,
)
_ARRAY_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'|`([^`]*)`")

# Audience-visible text also lives in *flat* props — `left-caption="…"`, `ships-items="…"`,
# `in-sub="…"`, `heading="…"`. Counting only the array props would let a diagram component
# park forty words of real copy outside the budget, which is the same blind spot that let
# component-led slides read as empty before `_prop_text` existed at all.
_FLAT_PROP = re.compile(r'(?<![:\w-])([a-z][a-z0-9-]*)="([^"]{2,})"')
_NON_TEXT_PROPS = frozenset(
    {
        "class",
        "style",
        "src",
        "href",
        "image",
        "icon",
        "kind",
        "pad",
        "variant",
        "position",
        "page",
        "tag",
        "side",
        "state",
        "color",
        "size",
        "top",
        "left",
        "duration",
        "width",
        "height",
        "id",
        "role",
        "aria-label",
        "v-click",
        "v-if",
        "v-for",
        "v-show",
        "transition",
        "layout",
        "align",
        "name",
    }
)


def _flat_prop_text(body: str) -> list[str]:
    return [
        value
        for prop, value in _FLAT_PROP.findall(body)
        if prop not in _NON_TEXT_PROPS and " " in value
    ]


def _prop_text(body: str) -> str:
    parts: list[str] = _flat_prop_text(body)
    for array in _PROP_ARRAY.findall(body):
        for single, backtick in _ARRAY_STRING.findall(array):
            value = single or backtick
            # Skip the short machine-ish values (icon names, keys like 'WHO') — they are labels,
            # not prose. Anything with a space is content the audience reads.
            if " " in value:
                parts.append(value.replace("\\'", "'"))
    return " ".join(parts)


def _slide_words(slide: Slide) -> int:
    body = _STYLE_BLOCK.sub("", slide.body)
    return len((_text_of(body) + " " + _prop_text(body)).split())


def _d10(slides: list[Slide]) -> list[Finding]:
    out: list[Finding] = []
    counted = [s for s in slides if s.layout not in _LOW_TEXT_LAYOUTS]
    for slide in counted:
        words = _slide_words(slide)
        diagram = _has_diagram(slide)
        hard = WORDS_PER_SLIDE_DIAGRAM_HARD if diagram else WORDS_PER_SLIDE_HARD
        soft = WORDS_PER_SLIDE_DIAGRAM if diagram else WORDS_PER_SLIDE
        if words > hard:
            out.append(
                Finding(
                    "D10",
                    "wall of words",
                    ERROR,
                    slide.index,
                    f"{words} words",
                    f"over {hard} — nobody reads this while you are talking. Move the "
                    "explanation into the presenter notes and leave the claim, or route the "
                    "content to a component that carries it",
                )
            )
        elif words > soft:
            out.append(
                Finding(
                    "D10",
                    "over word budget",
                    WARN,
                    slide.index,
                    f"{words} words against a {soft}-word budget",
                    "trim to the claim — the argument belongs in the notes, not on the slide",
                )
            )
        elif words < WORDS_PER_SLIDE_MIN and not diagram:
            out.append(
                Finding(
                    "D10",
                    "under-explained",
                    ERROR,
                    slide.index,
                    f"{words} words, no diagram",
                    f"under {WORDS_PER_SLIDE_MIN} words with nothing structural on the slide — "
                    "a headline over a background is not an explanation. Draw the argument "
                    "(FlowSequence / ScopeMap / GateFunnel / ReuseTrack / ProofContrast) or "
                    "put the substance back",
                )
            )
    if counted:
        avg = sum(_slide_words(s) for s in counted) / len(counted)
        target = _deck_words_target(counted)
        if avg > target:
            drawn = sum(1 for s in counted if _has_diagram(s))
            detail = f"{avg:.0f} words/slide average across {len(counted)} slides"
            if drawn:
                detail += f" ({drawn} carrying diagrams)"
            out.append(
                Finding(
                    "D10",
                    "deck is dense",
                    WARN,
                    0,
                    detail,
                    f"target {target:.0f} — a deck that averages more than this is a "
                    "document being read aloud",
                )
            )
    return out


# ══════════════════════════════════════════════════════════════════════════════════════
# D9 — render weight (theme-level, not per-deck)
# ══════════════════════════════════════════════════════════════════════════════════════
#
# The print-mode guard lives in styles/slidev-overrides.css, keyed off `html.deck-export`
# (set by setup/main.ts). Two escape hatches are already wired through it:
#   - `backdrop-filter: var(--card-blur)` — the token is redefined to `none` under the guard.
#   - the ambient/gradient-text classnames below — hidden or flattened under the guard.
# A new component that writes a raw `blur()`/`backdrop-filter` outside those two hatches will
# render fine on screen and silently reintroduce the bug on the next export. That is what this
# tier exists to catch — before anyone builds a deck with it.

GUARDED_AMBIENT_CLASSES: frozenset[str] = frozenset(
    {
        "pulse-halo",
        "mesh-aurora",
        "canvas-ambience",
        "orb",
        "grid-plane",
        "pulse-ring",
        "scan-line",
        "flow-beam",  # FlowTrack's connector line — aria-hidden, hidden under html.deck-export
        "slidev-vclick-hidden",  # Slidev's own pre-reveal state — neutralized under html.deck-export
    }
)
# Elements whose `filter:` sits on top of a real background photo (`$frontmatter.image`) — the
# photo rasterizes regardless of the filter, so the filter adds no marginal raster cost and does
# not need to be stripped in print mode the way a purely-decorative layer does.
PHOTO_BOUND_CLASSES: frozenset[str] = frozenset({"bg-image"})
GUARDED_GRADIENT_TEXT_CLASSES: frozenset[str] = frozenset(
    {
        "gradient-text",
        "gradient-text-product",
        "gt",
        "gt-product",
        "gt-animated",
        # Both set `color: transparent` with no visible fallback, so their print-mode rules in
        # slidev-overrides.css must supply a colour as well as undoing the clip — see the comment
        # there. `.numeral` alone was ~2MB of raster on every chapter divider.
        "numeral",
        "surface-index",
    }
)

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STYLE_TAG = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
_KEYFRAMES = re.compile(r"@keyframes\s+([\w-]+)\s*\{")
_FILTER_DECL = re.compile(r"filter\s*:\s*([^;]+);")
_BACKDROP_FILTER_DECL = re.compile(r"backdrop-filter\s*:\s*([^;]+);")
# Any explicit `filter:` value — not just blur() — forces Chromium to keep the element on its
# own compositing layer for print-to-PDF (saturate/contrast/drop-shadow are no exception).
_RAW_FILTER = re.compile(r"(?<!backdrop-)filter\s*:\s*([^;]+);")
_BG_CLIP_TEXT = re.compile(r"background-clip\s*:\s*text", re.IGNORECASE)
_CLASS_IN_SELECTOR = re.compile(r"\.([\w-]+)")


def _theme_css(path: Path) -> str:
    text = _COMMENT.sub("", path.read_text(encoding="utf-8"))
    if path.suffix == ".vue":
        return "\n".join(_STYLE_TAG.findall(text))
    return text


def _brace_blocks(css: str) -> list[tuple[str, str]]:
    """Top-level `selector { body }` blocks. `@media`/`@supports` are unwrapped (their
    contents matter); `@keyframes` is left alone — `_keyframes_blocks` handles those."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace == -1:
            break
        selector = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1 : j - 1]
        if selector.startswith("@keyframes"):
            pass
        elif selector.startswith("@"):
            out.extend(_brace_blocks(body))
        else:
            out.append((selector, body))
        i = j
    return out


def _keyframes_blocks(css: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _KEYFRAMES.finditer(css):
        depth, j, n = 1, m.end(), len(css)
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append((m.group(1), css[m.end() : j - 1]))
    return out


def _strip_keyframes(css: str) -> str:
    """Remove every `@keyframes … { … }` block by exact character span (not string
    replacement) — a keyframes body containing `filter: blur(...)` is exactly the content
    the D9 checks below must never see as an ordinary rule, so silent under-stripping here
    would misfire the very check the keyframes fix (animations.css) exists to satisfy."""
    spans: list[tuple[int, int]] = []
    for m in _KEYFRAMES.finditer(css):
        depth, j, n = 1, m.end(), len(css)
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        spans.append((m.start(), j))
    for start, end in reversed(spans):
        css = css[:start] + css[end:]
    return css


def lint_theme(theme_dir: Path) -> list[Finding]:
    out: list[Finding] = []
    overrides = theme_dir / "styles" / "slidev-overrides.css"
    guard_text = overrides.read_text(encoding="utf-8") if overrides.exists() else ""
    if "html.deck-export" not in guard_text or "--card-blur: none" not in guard_text:
        out.append(
            Finding(
                "D9",
                "print guard missing",
                ERROR,
                0,
                str(overrides),
                "styles/slidev-overrides.css must define `html.deck-export { --card-blur: none; }` "
                "— every component's backdrop-filter routes through that token to survive export",
            )
        )

    for path in sorted(theme_dir.rglob("*.vue")) + sorted(theme_dir.rglob("*.css")):
        rel = path.relative_to(theme_dir)
        css = _theme_css(path)
        if not css.strip():
            continue

        for name, body in _keyframes_blocks(css):
            filters = _FILTER_DECL.findall(body)
            if filters and "blur" in filters[-1] and filters[-1].strip() != "none":
                out.append(
                    Finding(
                        "D9",
                        "keyframes ends non-none filter",
                        ERROR,
                        0,
                        f"@keyframes {name} in {rel}",
                        "Chromium keeps a compositing layer alive for any explicit filter value, "
                        "including blur(0) — end the keyframe on `filter: none`, not `blur(0)`",
                    )
                )

        for selector, body in _brace_blocks(_strip_keyframes(css)):
            classes = set(_CLASS_IN_SELECTOR.findall(selector))

            m = _BACKDROP_FILTER_DECL.search(body)
            if m and m.group(1).strip() != "var(--card-blur)":
                out.append(
                    Finding(
                        "D9",
                        "hardcoded backdrop-filter",
                        ERROR,
                        0,
                        f"{selector} in {rel}",
                        "route through var(--card-blur) so html.deck-export can neutralize it — "
                        "a literal blur() value survives export and rasterizes",
                    )
                )

            m = _RAW_FILTER.search(body)
            if (
                m
                and m.group(1).strip() != "none"
                and not (classes & (GUARDED_AMBIENT_CLASSES | PHOTO_BOUND_CLASSES))
            ):
                out.append(
                    Finding(
                        "D9",
                        "unguarded filter",
                        ERROR,
                        0,
                        f"{selector} in {rel}",
                        "no vector PDF equivalent — either hide this element under html.deck-export "
                        "in slidev-overrides.css and add its classname to GUARDED_AMBIENT_CLASSES, add "
                        "it to PHOTO_BOUND_CLASSES if it's applied to an already-rasterized "
                        "background photo, or explain in a comment why this one is safe",
                    )
                )

            if _BG_CLIP_TEXT.search(body) and not (classes & GUARDED_GRADIENT_TEXT_CLASSES):
                out.append(
                    Finding(
                        "D9",
                        "unguarded gradient text",
                        WARN,
                        0,
                        f"{selector} in {rel}",
                        "background-clip:text has no vector PDF equivalent — add the classname to "
                        "the print-mode fallback in slidev-overrides.css (see .gt / .gradient-text), "
                        "unless it's small enough that the raster cost doesn't matter (a badge, not "
                        "a headline)",
                    )
                )
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


def _slide_suppressions(slides: list[Slide], finding: Finding) -> set[str]:
    for slide in slides:
        if slide.index == finding.slide:
            return slide.suppressed
    return set()


def question_bank(dossier: Path) -> list[str]:
    """Pull `QUESTIONS TO ASK` out of an account-dossier spec (JSON) or markdown."""
    if dossier.suffix == ".json":
        data = json.loads(dossier.read_text(encoding="utf-8"))
        out: list[str] = []
        for block in data.get("blocks", []):
            if block.get("type") == "questions":
                for group in block.get("groups", []):
                    out.extend(group.get("items", []))
        return out
    # Markdown: questions live as bullets, prose lines, or — in the approved-questions file
    # the skill writes — inside a table cell, which never ends the line.
    out: list[str] = []
    for line in dossier.read_text(encoding="utf-8").split("\n"):
        for cell in line.split("|") if "|" in line else [line]:
            cell = cell.strip("-*# ").strip()
            if cell.endswith("?"):
                out.append(cell)
    return out


def guardrails_from(dossier: Path) -> list[tuple[str, str]]:
    """Compile the dossier's DON'T column into banned patterns."""
    if dossier.suffix != ".json":
        return []
    data = json.loads(dossier.read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for block in data.get("blocks", []):
        if block.get("type") == "two_col" and "DON'T" in (block.get("rightTitle") or ""):
            for item in block.get("right", []):
                subject = re.sub(r"^Don't\s+", "", item, flags=re.IGNORECASE)
                keywords = re.findall(r"[A-Za-z][\w']{4,}", subject)[:2]
                if len(keywords) == 2:
                    out.append(
                        (
                            rf"\b{re.escape(keywords[0])}\b.{{0,60}}\b{re.escape(keywords[1])}\b",
                            f"dossier DON'T — {item}",
                        )
                    )
    return out


def question_slides(slides: list[Slide]) -> list[int]:
    """Which slides actually carry an on-slide question, in order."""
    return [s.index for s in slides if _questions(s)]


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="deck-lint")
    parser.add_argument("paths", nargs="*", type=Path, help="one or more slides.md")
    parser.add_argument(
        "--theme",
        type=Path,
        help="deck-theme dir (e.g. .engine/deck-theme) — runs the D9 render-weight audit. "
        "May be passed with or without slides.md paths.",
    )
    parser.add_argument(
        "--dossier",
        type=Path,
        action="append",
        default=None,
        help="account dossier (questions + DON'Ts). Repeatable — pass the dossier spec AND the\n"
        "deck's approved-questions file, which is where an extension to the bank is recorded.",
    )
    parser.add_argument("--inputs", type=Path, help="deck inputs/ dir — the quote corpus")
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--template", help="A1–A10 · sets D7 severity")
    parser.add_argument("--allow-appendix", action="store_true")
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if not args.paths and not args.theme:
        parser.error("pass one or more slides.md paths, --theme, or both")

    bank = None
    rails: list[tuple[str, str]] = []
    for source in args.dossier or []:
        bank = (bank or []) + question_bank(source)
        rails += guardrails_from(source)
    corpus = None
    if args.inputs:
        files = args.inputs.rglob("*.md") if args.inputs.is_dir() else [args.inputs]
        corpus = "\n".join(p.read_text(encoding="utf-8") for p in files)
        # A deck may ask something the dossier never listed — but only by writing it into
        # its own inputs first, where a reviewer sees it. That is the difference between
        # an extension and an invention.
        if bank is not None:
            bank = bank + [
                ln.strip("-*# ").strip() for ln in corpus.split("\n") if ln.strip().endswith("?")
            ]

    failed = False
    payload: dict[str, list[dict]] = {}
    for path in args.paths:
        if not path.exists():
            print(f"✗ no such file: {path}", file=sys.stderr)
            failed = True
            continue
        text = path.read_text(encoding="utf-8")
        findings = lint(
            text,
            bank=bank,
            guardrails=rails,
            corpus=corpus,
            minutes=args.minutes,
            template=args.template,
            allow_appendix=args.allow_appendix,
        )
        if args.as_json:
            payload[str(path)] = [f.__dict__ for f in findings]
        else:
            parsed = parse_slides(text)
            report(path, findings, verify_manifest(parsed), question_slides(parsed))
        if any(f.severity == ERROR for f in findings) or (args.strict and findings):
            failed = True

    if args.theme:
        if not args.theme.exists():
            print(f"✗ no such theme dir: {args.theme}", file=sys.stderr)
            failed = True
        else:
            theme_findings = lint_theme(args.theme)
            if args.as_json:
                payload[f"theme:{args.theme}"] = [f.__dict__ for f in theme_findings]
            else:
                report(args.theme, theme_findings, [])
            if any(f.severity == ERROR for f in theme_findings) or (args.strict and theme_findings):
                failed = True

    if args.as_json:
        print(json.dumps(payload, indent=2))
    elif failed:
        print(
            "\n  D1 is an estimate — scripts/deck_fit_probe.mjs measures the real canvas and"
            "\n  is the tiebreaker. A rule that is genuinely wrong about a slide can be"
            "\n  suppressed with <!-- lint-ok D4: reason --> inside that slide."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
