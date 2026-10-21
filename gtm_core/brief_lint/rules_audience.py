from __future__ import annotations

import re

from .model import ERROR, Finding
from .parse import _flatten, _strip_tags
from .vocab import AUDIENCES


def _operator_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges of operator-tagged sections — exempt from reader-surface tiers."""
    out: list[tuple[int, int]] = []
    for match in re.finditer(r'<section\b[^>]*data-audience="operator"[^>]*>', text):
        end = text.find("</section>", match.start())
        out.append((match.start(), end if end != -1 else len(text)))
    return out


def _in_ranges(offset: int, ranges: list[tuple[int, int]]) -> bool:
    return any(a <= offset < b for a, b in ranges)


def t8_audience(text: str, line_of) -> list[Finding]:
    """Every section declares who it is for; operator sections come last."""
    findings: list[Finding] = []
    sections = list(re.finditer(r"<section\b[^>]*>", text))
    if not sections:
        return findings
    last_reader = -1
    first_operator = None
    for match in sections:
        tag = match.group(0)
        aud = re.search(r'data-audience="([^"]*)"', tag)
        if not aud:
            findings.append(
                Finding(
                    "T8",
                    "section missing audience",
                    ERROR,
                    line_of(match.start()),
                    tag[:60],
                    'add data-audience="all|marketing|sales|product|operator"',
                )
            )
            continue
        if aud.group(1) not in AUDIENCES:
            findings.append(
                Finding(
                    "T8",
                    "unknown audience",
                    ERROR,
                    line_of(match.start()),
                    aud.group(1),
                    f"one of {sorted(AUDIENCES)}",
                )
            )
        elif aud.group(1) == "operator":
            if first_operator is None:
                first_operator = match.start()
        else:
            last_reader = match.start()
    if first_operator is not None and last_reader > first_operator:
        findings.append(
            Finding(
                "T8",
                "operator content before reader content",
                ERROR,
                line_of(first_operator),
                "operator section not last",
                "operator notes go after everything a reader is meant to see",
            )
        )
    return findings


def t9_takeaway_first(text: str, line_of) -> list[Finding]:
    """Long callouts open with their takeaway; caveats follow findings; actions split by team.

    Also gates the `<h1>`. The 2026-08-11 brief led with *"We finally asked buyers the right
    question — and three of them had already answered it in writing"*: a report on our own
    search hygiene, not on the market, and it passed every other tier. A brief's headline is the
    one sentence everybody reads, so it must state something true about the WORLD. Our method
    improving is an operator note; it goes in the appendix that exists for it.
    """
    findings: list[Finding] = []
    op_ranges = _operator_ranges(text)

    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S | re.I)
    if h1:
        headline = _flatten(_strip_tags(h1.group(1)))
        # First person + a method verb = a sentence about us, not about the market.
        method = (
            r"\b(?:we|our|us)\b[^.]{0,60}\b(?:ask(?:ed|ing)?|search(?:ed|ing)?|fix(?:ed|ing)?|"
            r"learn(?:ed|t)?|method|query|queries|process|realis|realiz|discover(?:ed)?|"
            r"widen(?:ed)?|check(?:ed)?|found out|got (?:it )?wrong)\b"
        )
        if re.search(method, headline, re.I):
            findings.append(
                Finding(
                    "T9",
                    "headline-about-method",
                    ERROR,
                    line_of(h1.start()),
                    headline[:96],
                    "the headline describes what WE did, not what the market did — lead with the "
                    "market fact and move the method note to the operator appendix",
                )
            )

    # A long callout must open with a bold takeaway, not with method or metadata.
    for match in re.finditer(r'<div class="note[^"]*">((?:(?!<div\b)[\s\S])*?)</div>', text):
        if _in_ranges(match.start(), op_ranges):
            continue
        inner = re.sub(r'^\s*<span class="cl">.*?</span>\s*', "", match.group(1), flags=re.S)
        words = len(_strip_tags(match.group(1)).split())
        if words <= 60:
            continue
        opens_bold = bool(re.match(r"\s*(?:<p\b[^>]*>\s*)?<b\b", inner))
        if not opens_bold:
            findings.append(
                Finding(
                    "T9",
                    "callout buries its takeaway",
                    ERROR,
                    line_of(match.start()),
                    " ".join(_strip_tags(inner).split()[:8]),
                    "open with the takeaway in bold, then the evidence",
                )
            )

    # The caveat BLOCK belongs in the appendix, after the findings. Matched on its label,
    # not on the phrase — a one-line pointer that says "the caveats are in Appendix A" is
    # the desired state, and a phrase match would flag exactly that.
    first_appx = re.search(r'<section\b[^>]*(?:class="[^"]*appx|id="appendix)', text)
    for match in re.finditer(r'<span class="cl">Before quoting[^<]*</span>', text):
        if first_appx is None or match.start() < first_appx.start():
            findings.append(
                Finding(
                    "T9",
                    "caveats before content",
                    ERROR,
                    line_of(match.start()),
                    "Before quoting any number",
                    "methodology caveats go in the appendix, not ahead of the findings",
                )
            )

    # The actions section answers "what does this mean for MY team", per team. Anchored on
    # the section's own <h2> — a cross-link whose text mentions the title must not match.
    for sec in re.finditer(r"<section\b[^>]*>[\s\S]*?</section>", text):
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", sec.group(0), re.S)
        if not h2 or "each team" not in _strip_tags(h2.group(1)).lower():
            continue
        blocks = re.findall(r"<h3[^>]*>\s*(\w+)", sec.group(0))
        for team in ("Marketing", "Sales", "Product"):
            if team not in blocks:
                findings.append(
                    Finding(
                        "T9",
                        "actions not split by team",
                        ERROR,
                        line_of(sec.start()),
                        f"no {team} block",
                        "the actions section needs Marketing, Sales and Product blocks",
                    )
                )
    return findings
