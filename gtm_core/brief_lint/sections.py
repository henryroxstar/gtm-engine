from __future__ import annotations

import html
import re

from .parse import _TAG_RE

_SECTION_RE = re.compile(r"<section\b[^>]*>(.*?)</section>", re.DOTALL | re.IGNORECASE)
_H2_RE = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.DOTALL | re.IGNORECASE)


def _html_sections(text: str) -> list[tuple[str, str, int]]:
    """Every top-level ``<section>...</section>`` block as (heading, inner html, content offset).

    Sections in this brief are never nested, so a non-greedy match to the first ``</section>``
    is correct. The heading is read from the first ``<h2>`` inside, tags stripped — matched
    against the canonical section table in ``html-companion.md`` rather than the anchor id,
    because the id an author picks for a given section is not a stable convention across
    issues (the 2026-09-01 brief used ``id="s3"`` for "What people say about us", which the
    template file uses for "BD focus"). T14 and T15 both key off this.
    """
    out: list[tuple[str, str, int]] = []
    for match in _SECTION_RE.finditer(text):
        content = match.group(1)
        heading_match = _H2_RE.search(content)
        heading = (
            html.unescape(_TAG_RE.sub("", heading_match.group(1))).strip() if heading_match else ""
        )
        out.append((heading, content, match.start(1)))
    return out


# Section key -> heading patterns (any one matches), from html-companion.md's reader-surface
# table ("Reader-surface structure ... 12 sections + 5 appendices"). Wording is expected to
# vary issue-to-issue (these are hand-authored, not templated), so patterns match on the
# stable topic phrase, not the exact heading string.
_T14_CITED_SECTIONS: list[tuple[str, str]] = [
    ("02 What buyers say", r"what buyers say|customer voice"),
    ("04b Competitor moves", r"competitor"),
    ("04c Regulatory & enforcement clock", r"regulat"),
    ("04d Proof points", r"proof points|measured failures"),
]

REQUIRED_READER_SECTIONS: list[tuple[str, str]] = [
    ("00 Since last issue", r"since last issue"),
    ("01 Who we heard from", r"who we heard from|who this is pulled from"),
    ("02 What buyers say", r"what buyers say|customer voice"),
    ("03 What people say about us", r"what people say about us|outside-in"),
    ("04b Competitor moves", r"competitor"),
    ("04c Regulatory & enforcement clock", r"regulat"),
    ("04d Proof points", r"proof points|measured failures"),
    ("04e Account moves", r"account.{0,20}moves|customer & prospect moves|prospect moves"),
    ("05 Are we aligned?", r"are we aligned|alignment"),
    ("06 What to validate", r"what to validate|signals to validate"),
    ("07 Standards watch", r"standards"),
    ("08 What this means for each team", r"what this means for each team"),
]

REQUIRED_READER_APPENDICES: list[tuple[str, str]] = [
    ("App. A Sources & how to read them", r"sources & how to read|how to read the sources"),
    ("App. B Evidence", r"evidence library|verbatim evidence"),
    ("App. C SEC search", r"sec search|corpus detail"),
    ("App. D Glossary", r"glossary"),
    ("App. E Operator notes", r"operator notes"),
]
