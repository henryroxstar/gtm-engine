from __future__ import annotations

import re

from .model import ERROR, Finding
from .vocab import IDENTIFIER_RULES, REF_STOPWORDS, VENDOR_TOOLS, VOCABULARY

# --------------------------------------------------------------------------------------
# Tiers
# --------------------------------------------------------------------------------------


def t1_identifiers(text: str, flat: str, line_of, names: dict[str, str]) -> list[Finding]:
    """Repo vocabulary on the reader surface."""
    findings: list[Finding] = []
    id_re = re.compile(r"\b(" + "|".join(sorted(names, key=len, reverse=True)) + r")\b")
    for match in id_re.finditer(flat):
        source_id = match.group(1)
        findings.append(
            Finding(
                "T1",
                "source id",
                ERROR,
                line_of(match.start()),
                source_id,
                f'write "{names[source_id]}"',
            )
        )
    for pattern, rule, fix in IDENTIFIER_RULES:
        for match in re.finditer(pattern, flat):
            findings.append(
                Finding("T1", rule, ERROR, line_of(match.start()), match.group(0).strip(), fix)
            )
    # Catch-all over the raw markup: anything snake_case inside <code>, which is where ids
    # hide from a prose-only sweep.
    for block in re.finditer(r"<code>(.*?)</code>", text, re.DOTALL):
        for hit in re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", block.group(1)):
            findings.append(
                Finding(
                    "T1",
                    "snake_case in <code>",
                    ERROR,
                    line_of(block.start()),
                    hit,
                    "use words, not an id",
                )
            )
    return findings


def t2_vocabulary(flat: str, line_of, quoted: set[int]) -> list[Finding]:
    """Our words for our machinery, and the tool names behind it."""
    findings: list[Finding] = []
    for pattern, replacement in VOCABULARY:
        for match in re.finditer(pattern, flat, re.IGNORECASE):
            line = line_of(match.start())
            if line in quoted:
                continue
            findings.append(
                Finding("T2", "house jargon", ERROR, line, match.group(0), f'write "{replacement}"')
            )
    for tool in VENDOR_TOOLS:
        for match in re.finditer(rf"\b{tool}\b", flat):
            line = line_of(match.start())
            if line in quoted:
                continue
            findings.append(
                Finding(
                    "T2",
                    "internal tool name",
                    ERROR,
                    line,
                    tool,
                    "name the kind of source, not the tool we bought",
                )
            )
    return findings


def t4_opaque_refs(flat: str, line_of) -> list[Finding]:
    """A `§`-reference that does not say what it points at."""
    findings: list[Finding] = []
    ref = re.compile(r"§\s?\d+[a-z]?(?:\.\d+)?")
    for match in ref.finditer(flat):
        tail = flat[match.end() : match.end() + 60]
        # "§3 — what the market says and does" names the section in substance, just not with
        # its title. A dash or colon followed by a real phrase is a named reference.
        if re.match(r"\s*[—–:-]\s*\S+(?:\s+\S+){2,}", tail):
            continue
        word = re.match(r"\s*([A-Za-z][\w'’-]*)", tail)
        # Nothing but punctuation after the ref — "see §08." names nothing at all. A
        # sentence-ending period lands here too, which is right: the next sentence's first
        # word is not this reference's title.
        if word is not None:
            following = word.group(1)
            # Capitalisation is the only reliable tell. "where" is a stopword; "Where" is
            # how "§03 Where BD is pointed" starts, and banning it would force the section
            # titles to be renamed to satisfy the linter.
            if not following[0].islower() or following.lower() not in REF_STOPWORDS:
                continue
        findings.append(
            Finding(
                "T4",
                "unnamed section ref",
                ERROR,
                line_of(match.start()),
                " ".join(match.group(0).split()),
                "add the section's title, e.g. '§04c Regulatory clock'",
            )
        )
    return findings
