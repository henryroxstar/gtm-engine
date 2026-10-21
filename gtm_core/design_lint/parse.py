from __future__ import annotations

import re

from .model import Finding, Section

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


class UnparseableDesign(ValueError):
    """The input is not a design document this linter can read.

    Raised rather than returning ``[]``. A document that parses to zero sections lints
    clean, and a confidently clean result on a file nobody could read is indistinguishable
    from a correct one — the failure mode §4.4 of the test plan exists to prevent.
    """


def _fence_lines(lines: list[str]) -> set[int]:
    """Indices inside a fenced code block — a `#` there is code, not a heading."""
    inside: set[int] = set()
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            inside.add(i)
            continue
        if in_fence:
            inside.add(i)
    return inside


def parse_sections(text: str) -> list[Section]:
    """Split a design document into one Section per heading, in document order.

    Text before the first heading becomes section 0 (level 0) so a preamble is never
    silently dropped. Raises :class:`UnparseableDesign` when the text carries no heading
    at all — refusing is the contract, never an empty clean run.
    """
    if not text.strip():
        raise UnparseableDesign("empty document")

    lines = text.split("\n")
    fences = _fence_lines(lines)
    heads = [
        (i, m.group(1), m.group(2))
        for i, ln in enumerate(lines)
        if i not in fences and (m := _HEADING.match(ln))
    ]
    if not heads:
        raise UnparseableDesign("no markdown headings found — not a design document")

    sections: list[Section] = []
    preamble = "\n".join(lines[: heads[0][0]]).strip()
    if preamble:
        sections.append(Section(index=0, line=1, level=0, heading="", body=preamble))

    for n, (line_no, hashes, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        sections.append(
            Section(
                index=len(sections) + 1 if preamble else n + 1,
                line=line_no + 1,
                level=len(hashes),
                heading=title.strip(),
                body="\n".join(lines[line_no + 1 : end]).strip(),
            )
        )
    return sections


def document_text(sections: list[Section]) -> str:
    """Every heading and body concatenated — for rules that reason over the whole doc."""
    return "\n".join(f"{s.heading}\n{s.body}" for s in sections)


def _section_suppressions(sections: list[Section], finding: Finding) -> set[str]:
    """Suppressions visible to a finding.

    Document-level findings (``section == 0``) may be suppressed from anywhere in the
    document, because there is no single section that owns them. A section-scoped finding
    may only be suppressed from its own section. deck_consistency makes the same split,
    and for the same reason: an unsuppressable deck-level finding has no escape hatch.
    """
    if finding.section == 0:
        return {tier for s in sections for tier in s.suppressed}
    for section in sections:
        if section.index == finding.section:
            return section.suppressed
    return set()
