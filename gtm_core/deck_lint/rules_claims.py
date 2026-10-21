from __future__ import annotations

import json
import re
from pathlib import Path

from .model import ERROR, WARN, Finding, Slide
from .text import _STYLE_BLOCK, _squash, _text_of

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
