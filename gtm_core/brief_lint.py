"""Gate the market-intelligence brief on whether a human outside the build can read it.

The 2026-07-29 brief shipped with `enterprise_filings` in a table, "neither reproduces"
as a finding, "seven speakers" in the opening paragraph, and three different wrong source
counts ("ten live sources", "10 / 10 present", "11 of 15 present"). Every one of those
passed every existing gate, because no gate had an opinion about the *reader*. The brief
was written for someone who already knew the system — which is everyone who built it and
nobody who needs it.

The fix is a surface split, and this module enforces it:

    the markdown brief is the INTERNAL RECORD  — mechanics belong there
    the HTML companion is the READER SURFACE   — mechanics are a defect there

So `gtm_core.voc.evidence` may appear in the `.md` and never in the `.html`. Same facts,
two registers, one of them chosen for a product/sales/strategy reader who has no idea what
a lane is.

Eleven rule tiers, deliberately split between what a machine can decide and what it cannot:

    T1  banned identifiers  — module paths, filenames, field names, source ids   ERROR
    T2  banned vocabulary   — our words for our machinery, with replacements     ERROR
    T3  stale counts        — every "N sources" claim, checked against the code  ERROR
    T4  opaque cross-refs   — "see §04c" with no section name attached           ERROR
    T5  unsourced figures   — a number in a reader block with no link near it    WARN
    T6  structure           — dangling anchors, duplicate ids, scripts, CDN      ERROR
    T7  density             — sentence and paragraph length, bold-per-sentence   WARN
    T8  audience routing    — every section declares its audience; operator      ERROR
                              content only in the operator appendix
    T9  takeaway-first      — a long callout must open with its takeaway;        ERROR
                              caveats may not precede findings; actions are
                              split by team (Marketing / Sales / Product)
    T10 render integrity    — markup that does not render as written: a class     ERROR
                              with no CSS rule, or an inline element whose
                              vertical margin is silently dropped
    T11 internal consistency— the same claim counted two ways in two tables      ERROR
                              (exact); a sentence asserting what another          WARN
                              section contains (re-verify by hand)
    T12 template residue    — an unreplaced {{placeholder}}, the template's own    ERROR
                              build instructions, or a comment describing a
                              placeholder that has since been replaced
    T13 component contract  — a styled component written without the child its     ERROR
                              own CSS targets (a bar track with no fill)

T8 exists because the 2026-07-29 brief interleaved operator notes — registry-tier
decisions, a blocked follow-up, a fact-check of an internal newsletter — with reader
content, and the reader (correctly) asked why any of it was there. Sections carry
`data-audience="all|marketing|sales|product|operator"`; operator-tagged sections must
follow every reader-facing one, and INSIDE an operator section the reader-surface tiers
(T1/T2/T5/T7) do not apply — the operator appendix is the one place mechanics are allowed
on the reader surface, which is precisely what makes banning them everywhere else fair.

T9 encodes the reviewed failure mode of the callouts: leading with method ("65 tasks, 824
criteria…") instead of meaning. A callout over 60 words must open with a bold takeaway
sentence; a "before quoting" caveat block may not appear before the first findings
section; and the actions section must be split by team, because "what does this mean for
me" was the question every reader block failed to answer.

T10 exists because the 2026-08-11 brief shipped twice with markup naming classes its own
stylesheet never defined. A whole section rendered as unstyled run-on text, and every
number in a table collided with its sub-label — "<b>8</b><span class=sub>8 companies</span>"
rendered as "88 companies", which the reader parsed as eighty-eight. Both times a human
reading the published page was the first thing to notice, and the source looks correct:
the markup IS separated, it is the inline box that drops the margin. Statically decidable,
so there is no excuse for a person finding it first.

T12 and T13 both exist because the 2026-08-18 brief shipped with defects a reader saw
first. T12: the browser tab read "{{Voice of the Customer}}" because replacing the body
leaves the <head> untouched, and the template's own build instructions were still the first
22 lines of the file. T13: a bar chart rendered as five empty outlines, because ".dseg" is
the track and the coloured fill is an inner "<i>" the author never wrote — every class was
defined and spelled correctly, so T10 passed. T13's contract is derived from the stylesheet
(".cls tag" implies an element with "cls" contains a "<tag>"), never transcribed, so a new
component gets checked the moment its CSS lands.

T1's identifier list and T3's expected counts are DERIVED from the collector and the
watermark policies at run time, never transcribed here. That is the point: add a
seventeenth source and this linter learns its id and its human-readable name in the same
commit, instead of going quietly out of date the way the counts did.

What this cannot do, stated plainly so nobody mistakes a green run for a readable brief:
it cannot tell you whether an executive understands a sentence. "Add the 36.2% benchmark
with its method line" passes all seven tiers and is still unreadable. T5 and T7 are the
closest approximation and they are warnings on purpose.

Usage:
    python -m gtm_core.brief_lint <path...>            # surface inferred from suffix
    python -m gtm_core.brief_lint --strict <path...>    # warnings become failures
    python -m gtm_core.brief_lint --surface reader ...  # force the stricter surface
    python -m gtm_core.brief_lint --json <path...>      # machine-readable findings

Exit 0 clean, 1 on any ERROR (or any finding under --strict).

Escape hatch, for the case the rules get wrong rather than the prose: put
`<!-- lint-ok T2: quoting a customer verbatim -->` on the offending line or the line
above it. It must name the tier and give a reason — a bare marker is rejected, because an
unexplained suppression is how a gate becomes decorative.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ERROR = "error"
WARN = "warn"

READER = "reader"  # the .html companion — a product/sales/strategy person reads this
RECORD = "record"  # the .md brief and per-source notes — the internal record


@dataclass(frozen=True)
class Finding:
    tier: str
    rule: str
    severity: str
    line: int
    excerpt: str
    fix: str


# --------------------------------------------------------------------------------------
# Derived vocabulary: the things this linter bans are read out of the code that owns them.
# --------------------------------------------------------------------------------------


def source_names() -> dict[str, str]:
    """`{source_id: human label}` straight from the collector, over an empty tree.

    The collector is the only place that knows what sources exist. Deriving the map here
    means a new source arrives already banned as an identifier AND already supplied with
    the name to use instead — the two halves of the fix land together.
    """
    from gtm_core.voc import collect as voc

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = voc.collect(root / "content", root / "profiles", "acme", today=date(2026, 1, 1))
    return {s["id"]: s["label"] for s in manifest["sources"]}


def expected_counts() -> dict[str, set[int]]:
    """What a truthful "N of these" claim in the brief may say.

    `speakers` accepts two values because both readings are honest: nine speaker labels
    exist, one of which (`mixed`) is an instruction to split rather than a speaker, so
    "eight" and "nine" are each defensible. Any other number is stale.
    """
    from gtm_core.voc import collect as voc
    from gtm_core.voc import watermark

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = voc.collect(root / "content", root / "profiles", "acme", today=date(2026, 1, 1))
    speakers = len(manifest["speakers"])
    return {
        "sources": {len(manifest["sources"])},
        "speakers": {speakers, speakers - 1},
        "lanes": {len(watermark.POLICIES)},
    }


# T1 — identifiers that only mean something to someone reading the repo.
IDENTIFIER_RULES: tuple[tuple[str, str, str], ...] = (
    (r"\bgtm_core[\w.]*", "module path", "name what it does, not the module that does it"),
    (r"\b(?:uv run|python -m)\s[\w.\- ]+", "shell command", "delete — readers do not run it"),
    (
        r"\b\w+\.(?:jsonl|toml|py|sh)\b",
        "repo filename",
        "describe the thing, e.g. 'our competitor list'",
    ),
    (r"§\s?R\d+", "internal rule ref", "state the rule in words, or delete it"),
    (
        r"\b(?:settled_by|superseded_by|counts_toward_breadth|max_age_days|provenance_class"
        r"|first_run_days|min_days|source_id|content_root|api_key_env)\b",
        "schema field",
        "say it as a sentence: 'what would settle it'",
    ),
    (
        r"\bverified:\s*(?:true|false)\b",
        "schema field",
        "'we read the original source' / 'we have not read it yet'",
    ),
    (r"\b[A-Z][A-Z0-9]{3,}(?:_[A-Z0-9]+)+\b", "code constant", "delete — say the rule in words"),
    (
        r"\b(?:pull-failed|not-pulled|nothing-new-since|do-not-use|unconfirmed-lead)\b",
        "status token",
        "plain English: 'could not be reached', 'not checked this week'",
    ),
)

# T2 — our house words. Left of the arrow is banned on the reader surface; right of it is
# what to write instead. Everything here was a real complaint about a real shipped brief.
VOCABULARY: tuple[tuple[str, str], ...] = (
    (r"\bspeakers?\b", "who is talking / whose words these are"),
    (r"\bbreadth[- ]eligible\b", "counts as demand"),
    (r"\bbreadth\b", "number of independent sources"),
    (r"\blanes?\b", "check / source"),
    (r"\bharvest(?:ed|ing|s)?\b", "pulled / read"),
    (r"\breproduces?\b(?!\s+the)", "has a source / we could not find a source"),
    (r"\bcorpus\b", "collection"),
    (r"\bprovenance\b", "where it came from"),
    (r"\bwatermark(?:s|ed)?\b", "the window this covers"),
    (r"\bsidecars?\b", "companion file"),
    (r"\bmanifests?\b", "list"),
    (r"\bingest(?:ed|ion)?\b", "read"),
    (r"\bfetch(?:ed|es|ing)?\b", "read / looked at"),
    (r"\bstat cards?\b", "figure"),
    (r"\bfail-?OPEN\b", "spell it out: 'lets the request through when no rule matches'"),
    (r"\bas-of snapshot\b", "where things stood on <date>"),
    (r"\bissue-to-issue delta\b", "what changed since last week"),
    (r"\bdisposition\b", "say what to do about it"),
    (r"\bcold start\b", "first run, so the window is wider"),
    (r"\bdedup(?:e|ed|ing|lication)?\b", "removed repeats"),
    (r"\bforcing function\b", "creates a deadline"),
    (r"\bregister rule\b", "which section it belongs in"),
)

# T2, second half: tool names we pay for. They are facts about our plumbing, not about the
# market, and a sales reader has never heard of them — "two Firecrawl credits" was a real
# complaint about a real line. Error rather than warning, because there is always a better
# phrasing: name the kind of source. Intent-data vendors are excluded on purpose — a seller
# knows what Bombora is, and there the vendor IS where the number came from.
VENDOR_TOOLS: tuple[str, ...] = ("Syften", "Firecrawl", "Slidev", "Higgsfield", "WebFetch")

# T4 — a section reference followed by one of these is telling the reader nothing.
REF_STOPWORDS = frozenset(
    """and or for the but see plus then also with from into in on of to is are was were it
    this that which where below above and/or per via covers explains""".split()
)

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}  # fmt: skip

COUNTED_NOUNS = {
    "source": "sources", "sources": "sources",
    "lane": "lanes", "lanes": "lanes",
    "speaker": "speakers", "speakers": "speakers",
    # "voice"/"voices" is the reader-facing word for the same thing, so it carries the same
    # staleness risk. The glossary said "which of the five voices" long after the eighth
    # arrived, and T3 could not see it while it only knew the word we use internally.
    "voice": "speakers", "voices": "speakers",
}  # fmt: skip

# "N sources" is ambiguous and the ambiguity matters: "4 sources" almost always means four
# independent companies backing ONE claim — the evidence count, which is a fact about that
# claim and not about our inventory. Only an inventory claim can be stale, so `sources`
# needs one of these qualifiers nearby before T3 will judge it. `lanes` and `speakers` have
# no such second reading (no single signal has eight speakers), so they are checked bare —
# which is what "seven speakers" in the hero paragraph needed.
# Adjacency matters, not proximity: a 100-character window around "4 sources, strong" picks
# up the "all" from "But all four are companies inside one channel" and fires on an evidence
# count. The qualifier has to sit directly against the phrase.
QUALIFIER_BEFORE = re.compile(
    r"\b(?:all|across|of|reads?|read|checked|checks|tracked|only)\s+(?:the\s+)?$", re.IGNORECASE
)
QUALIFIER_AFTER = re.compile(
    r"^\s*(?:present|absent|read|checked|live|tracked|in total|total|available|configured|wired"
    r"|under|feeding)\b",
    re.IGNORECASE,
)
AMBIGUOUS_NOUNS = frozenset({"sources"})

_TAG_RE = re.compile(r"<[^>]+>")
_LINT_OK_RE = re.compile(r"<!--\s*lint-ok\s+(T\d)\s*:\s*(\S.*?)-->", re.IGNORECASE)
_BLOCKQUOTE_OPEN = re.compile(r"<blockquote\b", re.IGNORECASE)
_BLOCKQUOTE_CLOSE = re.compile(r"</blockquote>", re.IGNORECASE)


def _strip_tags(line: str) -> str:
    """Reader-visible text only. An `id="s4c"` attribute is not prose."""
    return _TAG_RE.sub(" ", line)


def _flatten(text: str) -> str:
    """Reader-visible prose as one line, with every character offset preserved.

    Line-based rules miss anything a hard wrap splits, and this repo wraps markdown at 100
    columns — `the five\\nspeakers` sailed through the stale-count check for exactly that
    reason, in the file that tells the skill how many speakers there are. Tags and newlines
    are replaced by *equal-length* runs of spaces rather than removed, so an offset in the
    flattened text still maps to the right line in the original.

    Style rules and HTML comments are blanked too. `/* speaker series */` in a stylesheet is
    code that no reader sees, and flagging it would push a writer toward worse variable
    names to satisfy a prose rule. `lint-ok` markers are read off the raw lines separately,
    so blanking comments here does not disarm the escape hatch.
    """

    def blank(match: re.Match[str]) -> str:
        return re.sub(r"\S", " ", match.group(0))

    hidden = re.sub(r"<style\b.*?</style>", blank, text, flags=re.DOTALL | re.IGNORECASE)
    hidden = re.sub(r"<script\b.*?</script>", blank, hidden, flags=re.DOTALL | re.IGNORECASE)
    hidden = re.sub(r"<!--.*?-->", blank, hidden, flags=re.DOTALL)
    blanked = _TAG_RE.sub(lambda m: " " * len(m.group(0)), hidden)
    # Markdown emphasis and code fences break word adjacency: `**all nine** external lanes`
    # is one phrase to a reader and three to a regex. Underscores are left alone — T1 needs
    # them to spot a snake_case id.
    for char in "*`":
        blanked = blanked.replace(char, " ")
    return blanked.replace("\n", " ").replace("\r", " ")


def _suppressions(lines: list[str]) -> dict[int, set[str]]:
    """`{line number: {tiers suppressed}}`, honouring the line above as well as the line itself."""
    out: dict[int, set[str]] = {}
    for idx, line in enumerate(lines, 1):
        for tier, _reason in _LINT_OK_RE.findall(line):
            for target in (idx, idx + 1):
                out.setdefault(target, set()).add(tier.upper())
    return out


def _quoted_lines(lines: list[str]) -> set[int]:
    """Line numbers inside a `<blockquote>`.

    Quoted material is somebody else's register. If a customer said "fetch", the brief
    must be able to print "fetch" — flagging it would teach people to paraphrase quotes,
    which is a worse outcome than the jargon.
    """
    inside = False
    out: set[int] = set()
    for idx, line in enumerate(lines, 1):
        opened = bool(_BLOCKQUOTE_OPEN.search(line))
        if inside or opened:
            out.add(idx)
        if opened:
            inside = True
        if _BLOCKQUOTE_CLOSE.search(line):
            inside = False
    return out


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


AUDIENCES = frozenset({"all", "marketing", "sales", "product", "operator"})


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


def t10_render_integrity(text: str, line_of) -> list[Finding]:
    """Does the markup actually render as written?

    The `.html` companion is hand-authored against its own inline stylesheet, and nothing
    else checks that the two agree. Twice now a brief has shipped with markup naming classes
    the sheet never defined — a whole section rendered as unstyled run-on text, and every
    number in a table collided with its own sub-label ("<b>8</b><span class=sub>8 companies"
    read as "88 companies", which a reader reasonably parsed as eighty-eight). Both were
    caught by a human reading the published page, which is the wrong last line of defence.

    Two rules, both statically decidable from the file alone — no browser, no network:

    * ``class-not-styled`` — a class appears in ``class="..."`` and in no CSS rule. This is
      the defect that produced the unstyled section: a container class that simply did not
      exist, so the block inherited body text.
    * ``glued-inline`` — a classed inline element abuts the preceding text with no
      whitespace, and its class declares a *vertical* margin but is never given a block
      display. Vertical margins do not apply to inline boxes, so the author's intended
      spacing is silently dropped and the two texts run together. This is the "88" bug
      exactly, and it is invisible in the source: the markup looks correctly separated.
    """
    findings: list[Finding] = []
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I))
    if not styles:
        return findings
    markup = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)

    defined = set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", styles))
    used: dict[str, int] = {}
    for m in re.finditer(r'class="([^"]*)"', markup):
        for cls in m.group(1).split():
            used.setdefault(cls, m.start())
    for cls in sorted(set(used) - defined):
        findings.append(
            Finding(
                "T10",
                "class-not-styled",
                ERROR,
                line_of(used[cls]),
                f'class="{cls}"',
                f'no CSS rule defines ".{cls}" — it renders unstyled; '
                f"add the rule or use an existing class",
            )
        )

    # Rules mentioning a class, so we can ask what that class actually declares.
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", styles)
    for cls in sorted(set(used) & defined):
        body = " ".join(b for sel, b in rules if re.search(rf"\.{re.escape(cls)}(?![\w-])", sel))
        if not body:
            continue
        vertical = re.search(r"margin(-top|-bottom)?\s*:", body)
        blockish = re.search(r"display\s*:\s*(block|grid|flex|table|list-item|inline-block)", body)
        if not vertical or blockish:
            continue
        for m in re.finditer(
            rf'(\S)(<(?:span|b|i|em)[^>]*class="[^"]*\b{re.escape(cls)}\b)', markup
        ):
            findings.append(
                Finding(
                    "T10",
                    "glued-inline",
                    ERROR,
                    line_of(m.start()),
                    _flatten(markup[max(0, m.start() - 26) : m.start() + 34]),
                    f'".{cls}" sets a vertical margin but stays inline, so the margin is '
                    f"dropped and this runs into the text before it — give it "
                    f"display:block (scope it, e.g. `td .{cls}`) or add a separator",
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


def t13_component_contract(text: str, line_of) -> list[Finding]:
    """Is a styled component being used with markup its own CSS cannot style?

    T10 catches a class with *no* rule. This catches the subtler and more damaging case: the
    class exists, the rule exists, and the markup still renders wrong because the rule
    targets a **child that was never written**.

    The 2026-08-18 brief shipped a bar chart with invisible bars. The stylesheet says::

        .dseg   { display:flex; height:9px; overflow:hidden }   /* the track  */
        .dseg i { display:block; height:100% }                  /* the fill   */

    — the coloured fill is an inner ``<i>``. The author wrote ``<span class="dseg"
    style="width:52%"></span>``: a track with no fill. Every class was defined, every class
    was spelled right, T10 passed, and the chart rendered as five empty outlines.

    Two rules, both derived from the stylesheet rather than transcribed, and both chosen to
    fire only where the absence actually costs the reader something:

    * ``styled-child-missing`` — the child's rule **paints or sizes** it (``display``,
      ``width``, ``height``, ``background``, ``border``) and no such child exists. When a
      rule that draws something has nothing to draw on, the component renders empty. A
      child rule that only sets typography is deliberately NOT flagged: a ``.note`` whose
      stylesheet also styles ``.note li`` is simply a callout that happens not to contain a
      list this time, which is correct and common. That distinction was measured — the
      typography-inclusive version of this check produced 14 false positives on one prior
      brief and 1 true one.
    * ``list-container-not-a-list`` — a class that declares itself a list container (its own
      rule sets ``list-style``, or the sheet styles ``.cls dt`` / ``.cls dd``) is applied to
      an element that carries no list item. This is the glossary defect: ``.gloss`` is styled
      through ``.gloss dt`` / ``.gloss dd`` and was written as ``<div><b>…</b><span>…</span></div>``,
      so the definition typography silently never applied.
    """
    # (?<![-\w]) not \b: "\bheight" also matches inside "line-height", which made every
    # typographic rule look like a painting one and reintroduced the false positives.
    PAINTS = re.compile(r"(?<![-\w])(display|width|height|background|border|box-shadow)\s*:")
    findings: list[Finding] = []
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I))
    if not styles:
        return findings
    markup = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", styles)

    painted: dict[str, set[str]] = {}  # cls -> child tags whose rule draws something
    listish: dict[str, set[str]] = {}  # cls -> the list-item tags it expects
    own_body: dict[str, str] = {}
    for selector, body in rules:
        for sel in selector.split(","):
            sel = sel.strip()
            m = re.fullmatch(r"\.(-?[A-Za-z_][\w-]*)", sel)
            if m:
                own_body[m.group(1)] = own_body.get(m.group(1), "") + " " + body
                continue
            m = re.fullmatch(r"\.(-?[A-Za-z_][\w-]*)\s+([a-z]+)", sel)
            if not m:
                continue
            cls, tag = m.group(1), m.group(2)
            if tag in {"dt", "dd"}:
                listish.setdefault(cls, set()).add(tag)
            elif tag == "li":
                listish.setdefault(cls, set())  # confirmed below by list-style
            if PAINTS.search(body):
                painted.setdefault(cls, set()).add(tag)
    # An `li` rule only implies a list container when the class calls itself one.
    for cls in list(listish):
        if not listish[cls] and "list-style" not in own_body.get(cls, ""):
            del listish[cls]
        elif not listish[cls]:
            listish[cls] = {"li"}

    def elements(cls: str):
        for m in re.finditer(rf'<(\w+)[^>]*class="[^"]*\b{re.escape(cls)}\b[^"]*"[^>]*>', markup):
            inner = _element_inner(markup, m.end(), m.group(1))
            if inner is not None:
                yield m, inner

    for cls, tags in sorted(painted.items()):
        for m, inner in elements(cls):
            missing = sorted(t for t in tags if not re.search(rf"<{t}[\s>]", inner))
            if not missing:
                continue
            findings.append(
                Finding(
                    "T13",
                    "styled-child-missing",
                    ERROR,
                    line_of(m.start()),
                    _flatten(markup[m.start() : m.start() + 70]),
                    f'".{cls}" draws through ".{cls} {missing[0]}" but this element contains '
                    f"no <{missing[0]}> — the rule that paints it has nothing to paint, so "
                    f"the component renders empty",
                )
            )

    for cls, tags in sorted(listish.items()):
        for m, inner in elements(cls):
            if any(re.search(rf"<{t}[\s>]", inner) for t in tags):
                continue
            want = "/".join(sorted(tags))
            findings.append(
                Finding(
                    "T13",
                    "list-container-not-a-list",
                    ERROR,
                    line_of(m.start()),
                    _flatten(markup[m.start() : m.start() + 70]),
                    f'".{cls}" is styled as a list container (".{cls} {want}") but this '
                    f"element has no <{want}> — use the real list markup "
                    f"(<dl>/<dt>/<dd> or <ul>/<li>) or a plain container for prose",
                )
            )
    return findings


def _element_inner(markup: str, open_end: int, tag: str) -> str | None:
    """Inner HTML of the element whose opening tag ends at ``open_end``, or None if the
    document is too malformed to tell (in which case we say nothing rather than guess)."""
    depth, i = 1, open_end
    open_re = re.compile(rf"<{tag}[\s>]", re.I)
    close = f"</{tag}>"
    while i < len(markup):
        nxt_close = markup.find(close, i)
        if nxt_close == -1:
            return None
        nxt_open = open_re.search(markup, i, nxt_close)
        if nxt_open:
            depth += 1
            i = nxt_open.end()
            continue
        depth -= 1
        if depth == 0:
            return markup[open_end:nxt_close]
        i = nxt_close + len(close)
    return None


def t7_density(text: str) -> list[Finding]:
    """Length heuristics. Advisory always — prose length is a judgement call."""
    findings: list[Finding] = []
    line_of = _line_index(text)
    seen: set[tuple[str, int, str]] = set()
    # `_blocks` yields leaf-level prose and is always measured. A leaf `<div>` is only prose
    # when it holds no block children of its own: a table is dense by design (that is what a
    # table is for), and a div wrapping five numbered `<p>` items is not a 232-word paragraph
    # — each item is already measured on its own, so counting the wrapper double-counts it.
    candidates = list(_blocks(text))
    candidates += [
        (start, block)
        for start, block in _leaf_divs(text)
        if not re.search(r"<(?:table|tr|p|li)\b", block, re.IGNORECASE)
    ]
    quoted = _quoted_lines(text.splitlines())
    for block_start, block in candidates:
        # A verbatim quote's length is the source's choice, not the writer's. Flagging a
        # 123-word passage lifted from an SEC filing invites paraphrasing the evidence,
        # which is worse than a long sentence.
        if line_of(block_start) in quoted or _is_mostly_quotation(block):
            continue
        visible = " ".join(_strip_tags(block).split())
        if not visible:
            continue
        words = visible.split()
        if len(words) > 120:
            findings.append(
                Finding(
                    "T7",
                    "long paragraph",
                    WARN,
                    line_of(block_start),
                    f"{len(words)} words",
                    "split it, or turn it into a list",
                )
            )
        for sentence in re.split(r"(?<=[.!?])\s+", visible):
            count = len(sentence.split())
            if count > 45:
                findings.append(
                    Finding(
                        "T7",
                        "long sentence",
                        WARN,
                        line_of(block_start),
                        f"{count} words",
                        f"break it up: “{sentence[:60]}…”",
                    )
                )
        bolds = len(re.findall(r"<(?:b|strong)\b", block))
        sentences = max(1, len(re.split(r"(?<=[.!?])\s+", visible)))
        if bolds / sentences > 3:
            findings.append(
                Finding(
                    "T7",
                    "bold overload",
                    WARN,
                    line_of(block_start),
                    f"{bolds} bolds / {sentences} sentences",
                    "when everything is emphasised, nothing is",
                )
            )
    # `<p>` blocks and leaf `<div>`s overlap, so the same sentence can be measured twice.
    out: list[Finding] = []
    for finding in findings:
        key = (finding.rule, finding.line, finding.excerpt)
        if key not in seen:
            seen.add(key)
            out.append(finding)
    return out


# --------------------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------------------


def _blocks(text: str, tags: str = "p|li|td|h[1-6]") -> list[tuple[int, str]]:
    """`(offset, block)` for each closed prose container, so findings land near their text.

    The tag set differs by tier on purpose. T5 asks "can the reader follow this number?",
    and in a table the answer lives one cell over — so T5 takes the whole `<tr>`. T7 asks
    "is this block too dense?", where the cell is the right unit.
    """
    out: list[tuple[int, str]] = []
    for match in re.finditer(rf"<({tags})\b.*?</\1>", text, re.DOTALL | re.IGNORECASE):
        out.append((match.start(), match.group(0)))
    return out


def _is_mostly_quotation(block: str) -> bool:
    """True when over half the block sits inside quotation marks.

    Catches the evidence library, where a record is a filing's own sentence in curly quotes
    rather than a `<blockquote>`.
    """
    visible = _strip_tags(block)
    inside = sum(len(m.group(1)) for m in re.finditer(r"[“\"]([^”\"]{25,})[”\"]", visible))
    return bool(visible.strip()) and inside > len(visible.strip()) * 0.5


def _leaf_divs(text: str) -> list[tuple[int, str]]:
    """Innermost `<div>`s — where this brief actually keeps most of its prose.

    Without this, T7 measures only the `<p>` tags and reports a document of short
    paragraphs while the callout boxes run to two hundred words.
    """
    pattern = re.compile(r"<div\b[^>]*>((?:(?!<div\b)[\s\S])*?)</div>", re.IGNORECASE)
    return [(m.start(), m.group(0)) for m in pattern.finditer(text)]


def _line_index(text: str):
    """Offset → 1-based line number."""
    starts = [0]
    for idx, char in enumerate(text):
        if char == "\n":
            starts.append(idx + 1)

    def line_of(offset: int) -> int:
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    return line_of


def infer_surface(path: Path) -> str:
    """HTML is the reader surface; everything else is the internal record."""
    return READER if path.suffix.lower() in {".html", ".htm"} else RECORD


def lint(text: str, surface: str) -> list[Finding]:
    """All applicable tiers for one document."""
    lines = text.splitlines()
    suppressed = _suppressions(lines)
    flat = _flatten(text)
    line_of = _line_index(text)
    findings: list[Finding] = []

    # Both surfaces: a wrong count and a pointer to nowhere are defects in the record too.
    findings += t3_counts(flat, line_of, expected_counts())
    findings += t4_opaque_refs(flat, line_of)

    if surface == READER:
        # The operator appendix is the one place mechanics are allowed on the reader
        # surface — reader-register tiers skip it; structural tiers still apply.
        op_lines: set[int] = set()
        for start, end in _operator_ranges(text):
            op_lines.update(range(line_of(start), line_of(max(start, end - 1)) + 1))
        reader_findings = (
            t1_identifiers(text, flat, line_of, source_names())
            + t2_vocabulary(flat, line_of, _quoted_lines(lines))
            + t5_unsourced_figures(text, flat, line_of)
            + t7_density(text)
        )
        findings += [f for f in reader_findings if f.line not in op_lines]
        findings += t6_structure(text)
        findings += t8_audience(text, line_of)
        findings += t9_takeaway_first(text, line_of)
        # Render integrity is NOT filtered by op_lines: markup that does not render is a
        # defect in the operator appendix exactly as much as on the reader surface.
        findings += t10_render_integrity(text, line_of)
        # Scaffolding and broken component contracts are defects everywhere in the file,
        # including the operator appendix and the <head>, so they are not op_lines-filtered.
        findings += t12_template_residue(text, line_of)
        findings += t13_component_contract(text, line_of)
        # Same reasoning as T10: a brief that contradicts itself is wrong everywhere, so the
        # operator appendix is not exempt.
        findings += t11_internal_consistency(text, line_of)

    return [f for f in findings if f.tier not in suppressed.get(f.line, set())]


def report(path: Path, findings: list[Finding], strict: bool) -> None:
    """Group by tier, worst first, with the fix on every line."""
    order = ["T1", "T2", "T3", "T4", "T6", "T8", "T9", "T10", "T11", "T12", "T13", "T5", "T7"]
    titles = {
        "T1": "internal identifiers on the reader surface",
        "T2": "house jargon a reader outside the build will not know",
        "T3": "counts that no longer match the code",
        "T4": "section references that do not say what they point at",
        "T5": "figures with no source a reader can follow",
        "T6": "structural defects",
        "T7": "density (advisory)",
        "T8": "audience routing",
        "T9": "takeaway-first",
        "T10": "render integrity — markup that does not render as written",
        "T11": "internal consistency — the brief disagreeing with itself after an edit",
        "T12": "template residue — the template's own scaffolding still in the file",
        "T13": "component contract — a styled component missing the child its CSS targets",
    }
    print(f"\n{path}")
    for tier in order:
        rows = [f for f in findings if f.tier == tier]
        if not rows:
            continue
        gate = "FAIL" if (rows[0].severity == ERROR or strict) else "warn"
        print(f"\n  [{tier}] {titles[tier]} — {len(rows)} ({gate})")
        for finding in rows[:15]:
            where = f":{finding.line}" if finding.line else ""
            print(f"    {finding.rule}{where}: “{finding.excerpt}” → {finding.fix}")
        if len(rows) > 15:
            print(f"    ... and {len(rows) - 15} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gtm_core.brief_lint", description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--surface", choices=[READER, RECORD], default=None)
    parser.add_argument("--strict", action="store_true", help="treat T5/T7 warnings as failures")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    failed = False
    payload: dict[str, list[dict]] = {}

    for path in args.paths:
        if not path.exists():
            print(f"✗ no such file: {path}", file=sys.stderr)
            failed = True
            continue
        surface = args.surface or infer_surface(path)
        findings = lint(path.read_text(encoding="utf-8"), surface)
        if args.as_json:
            payload[str(path)] = [f.__dict__ | {"surface": surface} for f in findings]
        else:
            if findings:
                report(path, findings, args.strict)
            else:
                print(f"✓ {path} ({surface} surface) — clean")
        if any(f.severity == ERROR for f in findings) or (args.strict and findings):
            failed = True

    if args.as_json:
        print(json.dumps(payload, indent=2))
    elif failed:
        print(
            "\n  The reader surface is for a product, sales or strategy person who has never"
            "\n  opened this repo. Mechanics belong in the markdown brief, which is the"
            "\n  internal record and is linted far more loosely."
            "\n\n  A rule that is genuinely wrong about a line can be suppressed with"
            "\n  <!-- lint-ok T2: reason --> on that line. Name the tier and say why."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
