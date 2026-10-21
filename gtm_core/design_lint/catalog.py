from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

COVERAGE_TOML = Path(__file__).with_name("coverage.toml")

# The three maturity tags a capability claim may carry. Closed by design: an untagged
# claim is what SD6 catches, and a tag nobody defined is not a tag.
MATURITY_TAGS = frozenset({"enforced", "simulated", "design-target"})

# Headings that open each tier of the three-tier read. Matched against Section.slug.
EXEC_MARKERS = frozenset({"executive summary", "exec summary"})
TIER1_MARKERS = frozenset({"tier 1", "customer overview"})
TIER2_MARKERS = frozenset({"tier 2", "technical appendix"})

# A section naming a diagram must also walk the reader through it (SD13).
DIAGRAM_MARKERS = frozenset({"```mermaid", "<!-- diagram", "!["})
WALKTHROUGH_MARKERS = frozenset({"how to read", "reading this", "walkthrough", "what you see"})

# `figure` is NOT a diagram marker on its own, and the tripwire corpus is what proved it:
# in this document family the word is a NUMBER far more often than a picture — "a figure
# per attribute", "the figures below", "the availability figure". Word-bounding it (the fix
# for "con**figure**d") was necessary and not sufficient; the first clean fixture written
# after that fix still tripped SD13 on the phrase "every name, figure and system in it is
# invented". A prose reference to an actual diagram carries its number.
DIAGRAM_REFERENCE = re.compile(r"\bfigure\s+\d", re.IGNORECASE)

# Number words SD12 checks against the list they introduce.
NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def matches_marker(text: str, markers: frozenset[str]) -> bool:
    """True when `text` contains any marker as a WHOLE WORD (or, for symbolic markers,
    as a literal substring).

    Substring matching is how SD13 came to fire on eleven correct documents: the marker
    `figure` matched inside "con**figure**d route through the gateway". This repo has been
    bitten by the same class before — `org_token` tokenising a subdomain as the company,
    and the third-party roster's dictionary-word blind spot — so the fix is the general
    one, not a patch on the word `figure`.

    A marker that does not begin and end with a word character (```mermaid, ![) cannot
    carry a word boundary, so it is matched literally.

    The boundary is spelled out rather than `\b`, because `\b` treats `_` as a word
    character and this is markdown: the house style writes a walkthrough as
    `_How to read this:_`, and `\bhow to read\b` does not match it — there is no boundary
    between `_` and `h`. SD13 fired on the skill's own blank template for exactly that
    reason, which is the same substring/boundary family as `con**figure**d` one step over.
    """
    lowered = text.lower()
    for marker in markers:
        m = marker.lower()
        if m and m[0].isalnum() and m[-1].isalnum():
            if re.search(rf"(?<![0-9a-z]){re.escape(m)}(?![0-9a-z])", lowered):
                return True
        elif m in lowered:
            return True
    return False


@dataclass(frozen=True)
class Dimension:
    id: str
    guid: str
    name: str
    category: str
    question: str
    satisfied_by: str
    match: tuple[str, ...]
    severity: str
    source: str


class CoverageError(ValueError):
    """coverage.toml is missing, malformed, or draws on a value it does not define."""


@lru_cache(maxsize=1)
def _raw() -> dict:
    if not COVERAGE_TOML.exists():
        raise CoverageError(f"coverage taxonomy not found: {COVERAGE_TOML}")
    try:
        return tomllib.loads(COVERAGE_TOML.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - a corrupt file is refused
        raise CoverageError(f"coverage.toml is not valid TOML: {exc}") from exc


@lru_cache(maxsize=1)
def dimensions() -> tuple[Dimension, ...]:
    """Every coverage dimension, validated against the vocabularies the same file declares.

    A dimension citing a severity `coverage.toml` does not list is refused here rather
    than silently defaulting — an unrecognised value must block, never pass.
    """
    raw = _raw()
    severities = set(raw.get("meta", {}).get("severities", []))
    categories = set(raw.get("meta", {}).get("categories", []))
    if not severities or not categories:
        raise CoverageError("coverage.toml declares no severities or no categories")

    out: list[Dimension] = []
    seen_guid: set[str] = set()
    for entry in raw.get("dimensions", []):
        missing = {"id", "guid", "name", "category", "question", "match", "severity"} - set(entry)
        if missing:
            raise CoverageError(f"dimension {entry.get('id', '?')} is missing {sorted(missing)}")
        if entry["severity"] not in severities:
            raise CoverageError(
                f"dimension {entry['id']} uses severity {entry['severity']!r}, "
                f"which coverage.toml does not declare"
            )
        if entry["category"] not in categories:
            raise CoverageError(
                f"dimension {entry['id']} uses category {entry['category']!r}, "
                f"which coverage.toml does not declare"
            )
        if entry["guid"] in seen_guid:
            raise CoverageError(f"duplicate guid on {entry['id']} — guid is the stable identity")
        seen_guid.add(entry["guid"])
        out.append(
            Dimension(
                id=entry["id"],
                guid=entry["guid"],
                name=entry["name"],
                category=entry["category"],
                question=entry["question"],
                satisfied_by=entry.get("satisfied_by", ""),
                match=tuple(entry["match"]),
                severity=entry["severity"],
                source=entry.get("source", ""),
            )
        )
    if not out:
        raise CoverageError("coverage.toml declares no dimensions")
    return tuple(out)


def statuses() -> frozenset[str]:
    return frozenset(_raw().get("meta", {}).get("statuses", []))


# ══════════════════════════════════════════════════════════════════════════════════════
# The claim vocabulary — SD6–SD9
# ══════════════════════════════════════════════════════════════════════════════════════
#
# These four rules reason about what a design CLAIMS, not how it is shaped, and every one
# of them can be wrong in a way SD1–SD4 cannot: the author knows what shipped this week
# and this file knows what shipped when it was written. That asymmetry is why they emit
# ADVISORY (see `model.ADVISORY`) and why the vocabularies below are deliberately narrow.
# Each one drops the ambiguous members rather than reaching for recall.

# A capability claim in the indicative — "the gateway **enforces** the policy".
#
# Third-person singular only, plus the explicit passive. "supports" is here and "support"
# is not, because the bare stem is a noun far more often than a verb in this document
# family ("customer support", "support for X"). Likewise absent, and on purpose:
# `issues`, `signs`, `blocks`, `audits`, `routes`, `logs` — every one is a commoner noun
# than verb here ("open issues", "audit logs", "the two routes"), and SD6 firing on a noun
# is the `con**figure**d` family one step over. Recall lost knowingly; an advisory that
# cries wolf is one nobody reads, which is the failure this severity exists to avoid.
_CLAIM_VERBS = (
    "enforces",
    "validates",
    "verifies",
    "authenticates",
    "authorises",
    "authorizes",
    "revokes",
    "encrypts",
    "throttles",
    "rate-limits",
    "provisions",
    "refuses",
    "rejects",
    "guarantees",
    "prevents",
    "ensures",
    "supports",
    "provides",
    "detects",
    "mediates",
    "brokers",
    "federates",
)
_PASSIVE_CLAIM = (
    "enforced",
    "validated",
    "verified",
    "authenticated",
    "authorised",
    "authorized",
    "revoked",
    "encrypted",
    "throttled",
    "rate-limited",
    "refused",
    "rejected",
    "blocked",
)

CAPABILITY_CLAIM = re.compile(
    r"\b(?:"
    + "|".join(re.escape(v) for v in _CLAIM_VERBS)
    + r")\b|\b(?:is|are)\s+(?:"
    + "|".join(re.escape(v) for v in _PASSIVE_CLAIM)
    + r")\b",
    re.IGNORECASE,
)

# Every maturity label in play, across both houses that name one: `solution-design` tags a
# capability Enforced / Simulated / Design-target, and `solution-scope-check` §4b labels a
# product live / beta / preview / hypothesis. SD6 asks whether a claim carries any of them.
_TAG_WORDS = (
    "enforced",
    "simulated",
    "design-target",
    "design target",
    "live",
    "ga",
    "beta",
    "preview",
    "hypothesis",
    "roadmap",
    "planned",
)

# The ones that place a capability in the FUTURE. SD7's whole object: a claim wearing one
# of these while written as though it already works.
FORWARD_TAGS = frozenset({"design-target", "design target", "hypothesis", "roadmap", "planned"})

_TAG_ALT = "|".join(re.escape(w) for w in _TAG_WORDS)


def _tag_pattern(words: frozenset[str] | tuple[str, ...]) -> re.Pattern[str]:
    """A maturity tag in TAG POSITION — emphasised, a table cell of its own, bracketed, or
    a pill span.

    Position is load-bearing, not cosmetic. `enforced` is simultaneously the most important
    tag and a passive capability verb: "access **is enforced** at the gateway" is a *claim*,
    and reading the word as its own tag would make SD6 silent exactly where overclaiming
    hides. So a tag is only a tag where the house style puts one — `**Enforced**`, a
    `| Enforced |` cell, `[beta]`, or the companion pill `<span class="tag ok">Enforced</span>`
    that `solution-design` Step 5 mandates. A maturity word in flowing prose is prose.
    """
    alt = "|".join(re.escape(w) for w in sorted(words))
    return re.compile(
        rf"(?:\*\*|__)\s*(?:{alt})\b[^*_|]*(?:\*\*|__)"  # **Design-target**
        rf"|\|\s*(?:{alt})\b[^|]*\|"  # a table cell of its own
        rf"|[\[(]\s*(?:{alt})\b[^\])]*[\])]"  # [beta] / (design-target)
        rf'|class="tag[^"]*"\s*>\s*(?:{alt})\b',  # the companion pill
        re.IGNORECASE,
    )


MATURITY_TAG = _tag_pattern(tuple(_TAG_WORDS))
FORWARD_TAG = _tag_pattern(FORWARD_TAGS)

# A claim already in the conditional needs no tag to keep it honest — it is not asserting
# that the thing works. SD7 fires only where the mood and the label disagree.
CONDITIONAL = re.compile(
    r"\b(?:would|will|could|might|may|shall|once|when|until|if|subject\s+to|"
    r"plans?\s+to|planned\s+to|intends?\s+to|intended\s+to|proposed|target(?:ed)?\s+for|"
    r"not\s+yet|yet\s+to|on\s+the\s+roadmap)\b",
    re.IGNORECASE,
)

# Headings whose body states what is FIXED — the limits a later claim can contradict.
CONSTRAINT_MARKERS = frozenset(
    {
        "constraints",
        "constraints and assumptions",
        "guardrails",
        "limits",
        "limitations",
        "out of scope",
        "not in scope",
        "known gaps",
        "boundaries",
        "non-goals",
    }
)


def names_section(slug: str, markers: frozenset[str]) -> bool:
    """True when a heading IS one of these sections, not merely mentions one.

    `matches_marker` asks whether the word appears, which is right for a body and wrong for
    a heading. Measured against this workspace's corpus it matched a document's own H1 —
    a title of the form "Solution design — secure runtime guardrails for <product>" was
    read as a Constraints section, so SD8 drew its limits from a title. A section heading
    either opens with the marker or ends with it; a marker buried mid-title is describing
    the document, not labelling the block.
    """
    for marker in markers:
        m = marker.lower()
        if slug == m or slug.startswith(m + " ") or slug.endswith(" " + m):
            return True
    return False


# What a constraint in a solution design actually sounds like. It is NOT the register SD11
# detects: SD11 was tuned on skill guardrails ("never ship a version log"), where a
# prohibition carries a negated directive verb. Measured against this workspace's corpus,
# no sentence in any Constraints section had that shape — a design freezes things instead
# ("read-only to us", "stays in place", "not ours to choose", "outside our boundary"). A
# detector aimed at the wrong register is a rule that cannot fire, which is indistinguishable
# from a rule that is dead.
FREEZE_MARKERS = re.compile(
    r"\b(?:read[- ]only|stays?\s+in\s+place|unchanged|not\s+modified|not\s+ours|"
    r"cannot\s+be\s+(?:changed|modified|touched|replaced)|must\s+not\s+be\s+(?:changed|modified)|"
    r"out\s+of\s+scope|outside\s+(?:our|the)\s+boundary|fixed\s+(?:and|for)|"
    r"no\s+changes?\s+to|frozen|off[- ]limits|owned\s+by\s+(?:them|the\s+\w+)\s+and\s+not)\b",
    re.IGNORECASE,
)

# A claim that WRITES to something. A limit freezes an object; the contradiction is a later
# sentence mutating it. Perception and read verbs are deliberately absent — "we read the
# clinical record" does not contradict "the clinical record is read-only to us", it obeys it.
# Inflected forms only — no bare stems. The stems are nouns in this domain and the clean
# tripwire fixture proved it on the first run: `change` matched "the change **window**", so
# an open question in the Risks section read as a write to a frozen system. Same family as
# `con**figure**d`, `figure`-as-a-number, and `solution` in every H1 — the fourth time, and
# the fix is the same one every time: take the form that can only be the verb.
MUTATING_CLAIM = re.compile(
    r"\b(?:writes|writing|modifies|modifying|changing|replaces|replacing|"
    r"updating|extends|extending|migrates|migrating|rewrites|rewriting|deletes|"
    r"deleting|removes|removing|provisions|provisioning|reconfigures|reconfiguring|"
    r"patching|inserts|inserting|overwrites|overwriting)\b",
    re.IGNORECASE,
)

# ── SD9's two actor families ──────────────────────────────────────────────────────────
#
# `solution-design` Step 2 pins an actor/operator/principal line and then says: hold these
# roles consistent through every section, because "operator-as-actor in one place and
# principal-as-actor in another is the #1 review finding". SD9 is that sentence executable.
#
# What it looks for is NOT both families appearing — a design describes both sides of an
# integration and must. It is the SAME action attributed to both: we revoke it here, the
# holder revokes it there. One of the two is wrong and the reader cannot tell which.
OPERATOR_ACTORS = frozenset(
    {"we", "our", "the operator", "the vendor", "the platform team", "the integrator"}
)
PRINCIPAL_ACTORS = frozenset(
    {
        "the customer",
        "the client",
        "the holder",
        "the issuer",
        "the verifier",
        "the subject",
        "the end user",
        "the end-user",
        "the principal",
        "the user",
        "the merchant",
        "the tenant",
    }
)

# Broader than `CAPABILITY_CLAIM` because here the subject is spelled out: "the holder
# issues" cannot be the noun "issues", so the words that had to be dropped above are safe.
#
# Every member is AGENTIVE — it names something an actor does to the system. Perception and
# consumption verbs are deliberately excluded (`sees`, `receives`, `reads`, `views`, `gets`):
# "the customer sees the dashboard" and "we see the audit log" share a verb and two actors
# and are not drift, they are two parties looking at two things. Only an action can be
# mis-attributed, because only an action has one rightful owner.
ACTOR_VERBS = frozenset(
    """enforces enforce validates validate verifies verify authenticates authenticate
    revokes revoke issues issue signs sign holds hold presents present requests request
    approves approve configures configure provisions provision operates operate runs run
    owns own encrypts encrypt rejects reject refuses refuse routes route audits audit
    stores store submits submit initiates initiate triggers trigger invokes invoke calls
    call sends send grants grant consents consent registers register onboards onboard
    creates create deletes delete uploads upload publishes publish enrols enrol enrolls
    enroll manages manage defines define selects select chooses choose accepts accept
    declines decline confirms confirm pays pay attests attest delegates delegate""".split()
)


# A glossary defines terms; SD4 owns whether one exists. SD6 skips it, because a definition
# ("a credential that authorises an agent") carries a capability verb and promises nothing.
GLOSSARY_MARKERS = frozenset({"glossary", "terms", "definitions", "terminology"})


# Header cells that declare a column to be the maturity column. This is what makes SD6
# decidable: a capability matrix says in its own header that it tags status, and then a
# blank cell in that column is an untagged capability as a matter of structure — no guess
# about whether a sentence was a claim.
STATUS_HEADER = re.compile(r"\b(?:status|maturity|state|tagged|enforced)\b", re.IGNORECASE)

# A cell that is present but says nothing. "—", "n/a", "tbd" and "?" are not maturity.
EMPTY_CELL = re.compile(r"^\s*(?:[-–—*.]*|n/?a|tbd|tbc|\?+|none)\s*$", re.IGNORECASE)
