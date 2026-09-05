from __future__ import annotations

import re

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


AUDIENCES = frozenset({"all", "marketing", "sales", "product", "operator"})
