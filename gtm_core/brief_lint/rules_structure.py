from __future__ import annotations

import re

from .model import ERROR, Finding
from .parse import _flatten, _line_index
from .sections import REQUIRED_READER_APPENDICES, REQUIRED_READER_SECTIONS, _html_sections


def t6_structure(text: str) -> list[Finding]:
    """The mechanical integrity a reader notices when it breaks."""
    findings: list[Finding] = []
    line_of = _line_index(text)

    for match in re.finditer(r"<script\b", text, re.IGNORECASE):
        findings.append(
            Finding(
                "T6", "script tag", ERROR, line_of(match.start()), "<script", "the brief is static"
            )
        )
    for match in re.finditer(r"""(?:src|href)\s*=\s*["']https?://[^"']+\.(?:js|css)""", text):
        findings.append(
            Finding(
                "T6",
                "external asset",
                ERROR,
                line_of(match.start()),
                match.group(0)[:60],
                "inline it — the brief must open with no network",
            )
        )

    ids = re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', text)
    seen: set[str] = set()
    for value in ids:
        if value in seen:
            findings.append(
                Finding("T6", "duplicate id", ERROR, 0, value, "ids must be unique — anchors break")
            )
        seen.add(value)

    for match in re.finditer(r'href\s*=\s*["\']#([^"\']+)["\']', text):
        if match.group(1) not in seen:
            findings.append(
                Finding(
                    "T6",
                    "dangling anchor",
                    ERROR,
                    line_of(match.start()),
                    f"#{match.group(1)}",
                    "the link goes nowhere — fix the target or drop the link",
                )
            )

    opens = len(re.findall(r"<div\b", text))
    closes = len(re.findall(r"</div>", text))
    if opens != closes:
        findings.append(
            Finding(
                "T6",
                "unbalanced <div>",
                ERROR,
                0,
                f"{opens} open / {closes} closed",
                "the layout will collapse somewhere below the mismatch",
            )
        )

    sections = set(re.findall(r'<section\b[^>]*\bid\s*=\s*["\']([^"\']+)["\']', text))
    nav = set(re.findall(r"<nav\b.*?</nav>", text, re.DOTALL | re.IGNORECASE))
    linked = {m for block in nav for m in re.findall(r'href\s*=\s*["\']#([^"\']+)["\']', block)}
    if nav:
        for orphan in sorted(sections - linked):
            findings.append(
                Finding(
                    "T6",
                    "section missing from contents",
                    ERROR,
                    0,
                    orphan,
                    "a section nobody can navigate to is a section nobody reads",
                )
            )
    return findings


def t12_template_residue(text: str, line_of) -> list[Finding]:
    """Did the template's own scaffolding ship inside the deliverable?

    The HTML companion is produced by copying a de-branded template and replacing its
    ``{{PLACEHOLDER}}`` content. The 2026-08-18 brief shipped with three pieces of that
    scaffolding still in place, and every one was visible to a reader:

    * the browser tab read ``{{Voice of the Customer}} · {{MONTH YEAR}}`` — the ``<title>``
      is outside the body, so replacing the body content leaves it untouched;
    * the template's 22-line build instruction comment ("Copy this file, then: 1. Replace
      every {{PLACEHOLDER}}…") was still the first thing in the file;
    * a CSS comment still announced a "PLACEHOLDER ACCENT (neutral slate-blue)" above the
      tenant's real brand colour, so the one comment a future author would trust was a lie.

    None of T1–T11 looked for any of it, because each tier inspects the *rendered prose* and
    this is scaffolding around it. Statically decidable and unambiguous: a shipped brief
    contains no ``{{...}}``, no instruction to itself, and no comment describing a
    placeholder that has since been replaced.
    """
    findings: list[Finding] = []
    for m in re.finditer(r"\{\{[^{}]{0,120}\}\}", text):
        findings.append(
            Finding(
                "T12",
                "unreplaced-placeholder",
                ERROR,
                line_of(m.start()),
                m.group(0)[:60],
                "the template placeholder was never filled in — replace it with this "
                "run's content (check the <title> and <head>, not just the body)",
            )
        )
    scaffolding = (
        (r"Replace every \{\{PLACEHOLDER\}\}", "the template's build instructions"),
        (r"Copy this file, then", "the template's build instructions"),
        (r"PLACEHOLDER ACCENT", "a comment describing an accent that has been replaced"),
        (r"generic HTML companion template", "the template's own self-description"),
        (r"\{\{COMPANY", "an unreplaced company token"),
    )
    for pattern, what in scaffolding:
        for m in re.finditer(pattern, text):
            findings.append(
                Finding(
                    "T12",
                    "template-scaffolding",
                    ERROR,
                    line_of(m.start()),
                    _flatten(text[m.start() : m.start() + 58]),
                    f"{what} shipped inside the deliverable — delete it, or replace it "
                    f"with a one-line provenance note naming this issue's date",
                )
            )
    return findings


def t15_required_sections(text: str) -> list[Finding]:
    """T15: the reader surface carries all 12 sections + 5 appendices html-companion.md defines.

    Statically decidable, and worth gating on: the 2026-09-01 brief shipped with 4 of 12
    sections (04e, 06, 07, and a correctly-numbered but misplaced 08) and 3 of 5 appendices
    (C, D, E) missing outright, while the paired `.md` record had every one of them — the
    HTML rewrite dropped sections rather than compressing them. T14 cannot catch this: it
    only inspects sections that exist.

    Completeness is a whole-document property, unlike every other tier here — so it is
    scoped to a document that has a masthead (``<h1>``), the report-cover element every
    real brief carries exactly once. A one-section fixture built to exercise some other
    tier in isolation has no masthead and is correctly exempt; a real brief always has one.
    """
    if not re.search(r"<h1\b", text, re.IGNORECASE):
        return []

    findings: list[Finding] = []
    sections = _html_sections(text)
    headings = [s[0] for s in sections]

    for label, pattern in REQUIRED_READER_SECTIONS + REQUIRED_READER_APPENDICES:
        if not any(re.search(pattern, heading, re.IGNORECASE) for heading in headings):
            findings.append(
                Finding(
                    "T15",
                    "missing required section",
                    ERROR,
                    0,
                    label,
                    f"add §{label} per html-companion.md's reader-surface table — every "
                    "section listed there must appear, even if only as a short strip",
                )
            )
    return findings
