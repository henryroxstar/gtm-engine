from __future__ import annotations

import json
import re
from pathlib import Path

from .model import ERROR, WARN, Finding, Slide
from .text import _text_of

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


def question_slides(slides: list[Slide]) -> list[int]:
    """Which slides actually carry an on-slide question, in order."""
    return [s.index for s in slides if _questions(s)]
