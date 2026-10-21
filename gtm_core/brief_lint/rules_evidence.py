from __future__ import annotations

import bisect
import re

from .model import ERROR, WARN, Finding
from .parse import _flatten, _strip_tags
from .sections import _T14_CITED_SECTIONS, _html_sections
from .vocab import AMBIGUOUS_NOUNS, COUNTED_NOUNS, NUMBER_WORDS, QUALIFIER_AFTER, QUALIFIER_BEFORE


def t3_counts(flat: str, line_of, expected: dict[str, set[int]]) -> list[Finding]:
    """Every countable claim, checked against the code that owns the count.

    This is the hard one on purpose. Three wrong source counts reached a reader in one
    document, and a wrong number does more damage than a clumsy sentence: it is the part
    a reader quotes.
    """
    findings: list[Finding] = []
    word_alt = "|".join(NUMBER_WORDS)
    noun_alt = "|".join(sorted(COUNTED_NOUNS, key=len, reverse=True))
    # One optional adjective is allowed between the number and the noun: "all nine external
    # lanes are current" was stale and invisible while the pattern demanded adjacency. The
    # list is deliberately closed — any word would let "five of the ten posts in one lane"
    # read as an inventory claim.
    adjective = r"(?:live|active|external|internal|distinct|separate|total|tracked|named|configured|wired|present)\s+"
    plain = re.compile(rf"\b(\d+|{word_alt})\s+(?:{adjective})?({noun_alt})\b", re.IGNORECASE)
    ratio = re.compile(rf"\b(\d+)\s*(?:of|/)\s*(\d+)\s+({noun_alt})\b", re.IGNORECASE)

    for match in plain.finditer(flat):
        value, noun = match.group(1), match.group(2)
        key = COUNTED_NOUNS[noun.lower()]
        # A section-number span ("08 Sources key") is a heading, not a claim.
        if value.startswith("0") or noun[:1].isupper():
            continue
        if key in AMBIGUOUS_NOUNS:
            before = flat[max(0, match.start() - 30) : match.start()]
            after = flat[match.end() : match.end() + 30]
            if not (QUALIFIER_BEFORE.search(before) or QUALIFIER_AFTER.match(after)):
                continue
        actual = NUMBER_WORDS.get(value.lower())
        if actual is None:
            actual = int(value)
        if actual not in expected[key]:
            want = " or ".join(str(n) for n in sorted(expected[key]))
            findings.append(
                Finding(
                    "T3",
                    f"stale {key} count",
                    ERROR,
                    line_of(match.start()),
                    " ".join(f"{value} {noun}".split()),
                    f"there are {want} {key}",
                )
            )
    for match in ratio.finditer(flat):
        total, noun = match.group(2), match.group(3)
        key = COUNTED_NOUNS[noun.lower()]
        if int(total) not in expected[key]:
            want = " or ".join(str(n) for n in sorted(expected[key]))
            findings.append(
                Finding(
                    "T3",
                    f"stale {key} total",
                    ERROR,
                    line_of(match.start()),
                    " ".join(f"of {total} {noun}".split()),
                    f"there are {want} {key}",
                )
            )
    return findings


def t5_unsourced_figures(text: str, flat: str, line_of) -> list[Finding]:
    """A figure a reader cannot look up.

    "Followable" is measured by PROXIMITY IN READING ORDER, not by tag structure. The first
    version asked whether the source sat in the same `<p>`/`<td>`, and reported every figure
    in the §09 action cards as unsourced — each card carries its evidence on a `Where:` line
    one paragraph down, which is exactly where a reader looks. A table row had the same
    problem from the other direction. A character window covers both without special-casing
    either.

    Warning, not error: some numbers are arithmetic on figures sourced further up the
    section, and forcing a link onto every one of those pushes writers toward vaguer prose.
    """
    findings: list[Finding] = []
    # Offsets align between `text` and `flat`, so a link's position in the markup can be
    # compared directly against a figure's position in the prose.
    anchors = sorted(
        [m.start() for m in re.finditer(r"href\s*=", text)]
        + [m.start() for m in re.finditer(r"§", text)]
    )
    reach = 500

    def followable(offset: int) -> bool:
        i = bisect.bisect_left(anchors, offset - reach)
        return i < len(anchors) and anchors[i] <= offset + reach

    # No trailing \b after `%`: a word boundary needs a word character on one side, and
    # "36.2% this" has none — which made every percentage in the brief invisible to T5.
    figure = re.compile(
        r"(?<![\w$])(?:\$\s?\d[\d,.]*\s?[MBK]?\b|\d[\d,.]*\s?%|\d+(?:\.\d+)?\s?[×x]\b)"
    )
    orphans: dict[int, list[str]] = {}
    for match in figure.finditer(flat):
        if followable(match.start()):
            continue
        orphans.setdefault(line_of(match.start()), []).append(match.group(0).strip())
    for line, hits in sorted(orphans.items()):
        findings.append(
            Finding(
                "T5",
                "figure with no source",
                WARN,
                line,
                ", ".join(dict.fromkeys(hits))[:60],
                "add the source link, or a section ref where it is sourced",
            )
        )
    return findings


def _claim_rows(text: str) -> list[tuple[int, str, str]]:
    """`(offset, normalized label, leading count)` for every table row that opens with a label
    and carries a number. The brief states the same claims in more than one table — the reader
    surface in §02 and the evidence appendix — so the same label must carry the same count."""
    rows: list[tuple[int, str, str]] = []
    for m in re.finditer(r"<tr>(.*?)</tr>", text, re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", m.group(1), re.S | re.I)
        if len(cells) < 2:
            continue
        label = _flatten(_strip_tags(cells[0])).strip().lower()
        label = re.sub(r"[^a-z0-9 ]+", " ", label)
        label = re.sub(r"\s+", " ", label).strip()
        if len(label.split()) < 3:
            continue
        for cell in cells[1:]:
            num = re.match(r"\s*<b>(\d+)</b>", cell)
            if num:
                rows.append((m.start(), label, num.group(1)))
                break
    return rows


def t11_internal_consistency(text: str, line_of) -> list[Finding]:
    """Does the brief still agree with itself after an edit?

    Every defect this tier catches is a *stale dependent*: a fact was updated in one place and
    the sentences that depended on it were not. In a single 2026-08-11 session this happened
    three times — the headline was rewritten while the opening callout still asserted the
    opposite; a claim's source count went 5 → 6 while a later section still said that claim had
    no customer evidence; a source note was corrected while the brief still carried the wrong
    diagnosis. A human caught two of the three. Nothing in T1–T10 looks for it, because each
    sentence is individually fine — only the *pair* is wrong.

    Two rules, deliberately split by how exactly they can be decided:

    * ``count-disagrees-across-tables`` (ERROR) — the same claim label carries two different
      numbers in two tables. Exact, no judgement, no false positives: either the strings match
      and the numbers differ, or they do not.
    * ``cross-section-assertion`` (WARN) — a sentence asserts what ANOTHER section does or does
      not contain ("§02 has no customer evidence for this"). That sentence silently goes stale
      the moment the referenced section changes, and it cannot be machine-verified. Flagged so
      it is re-read every issue rather than trusted, which is exactly what went wrong.
    """
    findings: list[Finding] = []

    by_label: dict[str, dict[str, int]] = {}
    for offset, label, count in _claim_rows(text):
        by_label.setdefault(label, {}).setdefault(count, offset)
    for label, counts in by_label.items():
        if len(counts) > 1:
            shown = ", ".join(sorted(counts))
            worst = max(counts.values())
            findings.append(
                Finding(
                    "T11",
                    "count-disagrees-across-tables",
                    ERROR,
                    line_of(worst),
                    f"“{label[:60]}” = {shown}",
                    "the same claim carries two different source counts — update every table, "
                    "or the reader trusts whichever they read first",
                )
            )

    # A claim about another section's contents. The negation must be grammatically ATTACHED to
    # the reference — "§02 ... has no customer evidence for this" — not merely nearby. A window
    # that accepts any "no"/"not" fires on every table row that ends in an evidence link, which
    # is 20 warnings of pure noise; a gate people learn to scroll past is worse than no gate.
    absence = (
        r"^\s*\W*(?:\w+\s+){0,4}?"
        r"(?:has|have|had|contains?|shows?|carr(?:y|ies|ied)|lists?|says?|holds?|offers?|gives?)"
        r"\s+(?:no|none|nothing|zero)\b"
    )
    for m in re.finditer(r'<a[^>]+href="#([\w-]+)"[^>]*>(.*?)</a>', text, re.S | re.I):
        after = _flatten(_strip_tags(text[m.end() : m.end() + 110]))
        window = _flatten(_strip_tags(text[max(0, m.start() - 110) : m.end() + 110]))
        if re.search(absence, after, re.I):
            findings.append(
                Finding(
                    "T11",
                    "cross-section-assertion",
                    WARN,
                    line_of(m.start()),
                    window[:96],
                    f"this states what §{m.group(1)} does or does not contain — re-verify it "
                    f"against that section this issue; it goes stale silently when that section changes",
                )
            )
    return findings


def t14_evidence_linkage(text: str, flat: str, line_of) -> list[Finding]:
    """T14: sections 02, 04b, 04c, 04d — every table row cites primary evidence.

    A row counts as cited if it links out (any ``href``) or carries a speaker chip
    (``.spk``/``.vchip``/``.eref``) per html-companion.md's evidence-gating rule — an
    explicit "no movement in window" row with neither is exactly the gap this tier exists
    to catch, not a false positive.
    """
    findings: list[Finding] = []
    sections = _html_sections(text)

    for label, pattern in _T14_CITED_SECTIONS:
        match = next((s for s in sections if re.search(pattern, s[0], re.IGNORECASE)), None)
        if match is None:
            continue  # T15 reports the missing section; don't double-report here
        _heading, content, content_start = match
        tbody_match = re.search(r"<tbody\b[^>]*>(.*?)</tbody>", content, re.DOTALL | re.IGNORECASE)
        row_source = tbody_match.group(1) if tbody_match else content
        row_offset_base = content_start + (tbody_match.start(1) if tbody_match else 0)
        for row_match in re.finditer(r"<tr\b[^>]*>.*?</tr>", row_source, re.DOTALL | re.IGNORECASE):
            row = row_match.group(0)
            # A header row states the columns, not a claim, so it has nothing to cite. Tables
            # here are written without <thead>, so the <th> cells are the only marker.
            if re.search(r"<th\b", row, re.IGNORECASE):
                continue
            cited = (
                'href="' in row or "spk " in row or 'class="vchip"' in row or 'class="eref"' in row
            )
            if not cited:
                offset = row_offset_base + row_match.start()
                findings.append(
                    Finding(
                        "T14",
                        "uncited row",
                        ERROR,
                        line_of(offset),
                        row[:60],
                        f"§{label} — link to primary evidence or Appendix B, or add a speaker chip",
                    )
                )
    return findings
