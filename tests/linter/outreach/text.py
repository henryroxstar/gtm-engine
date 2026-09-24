"""Tokenisers, n-gram helpers and closed word tables. Imports nothing from this package."""

from __future__ import annotations

import re
from functools import cache

MAX_NGRAM_EMAILS = 3  # a 6-gram may appear in at most this many emails
NGRAM_N = 6
JACCARD_MAX = 0.45

# A pack whose contact is genuinely unresolved writes this sentinel in the greeting rather
# than a merge tag. It is the ONE way to say "no name yet": `{{First Name}}` in a 1:1 pack
# is the sequencer's vocabulary leaking into a hand-sent email and ships literally.
UNRESOLVED_SENTINEL = "[NAME UNRESOLVED]"

# A pack addressed to a ROLE INBOX — `enquiries@`, `hello@`, a contact-form mailbox — has no
# person behind it and never will have one. That is a DIFFERENT state from `[NAME UNRESOLVED]`,
# which says a seat exists and this pass did not resolve its name: one is blocked pending
# research, the other is finished and hand-sendable. Conflating them made `greeting` demand a
# name for a mailbox that has none — `_first_name` returned the literal word from a persona of
# "unresolved — team inbox <address>", so the rule asked for `Hi unresolved,` and the only way
# to satisfy it was to write something wrong.
# Declared as a sentinel rather than inferred from prose ("team inbox", "no named seat") for the
# same reason `[NAME UNRESOLVED]` is: a rule that sniffs prose is a rule any pack can talk its
# way past, and the state it asserts — nobody is on the other end — is a claim the drafter
# should have to make on purpose.
ROLE_INBOX_SENTINEL = "[ROLE INBOX]"
# A plausible human first name: Unicode letters, optionally hyphenated/apostrophised ("Joaquín",
# "Josué", "Jean-Luc", "O'Brien"). Deliberately Unicode-aware — an ASCII-only rule would reject
# real names, which is how a "fix" for this class of bug usually breaks worse things. Bare
# placeholder words are rejected even when they are alphabetic.
#: Words that are never a given name, checked per TOKEN so "name unconfirmed" is refused
#: whole and "Hui Jie" is not.
_NON_NAMES = frozenset(
    {
        "name",
        "first",
        "firstname",
        "there",
        "team",
        "unconfirmed",
        "unknown",
        "tbd",
        "none",
        "todo",
        "placeholder",
        "insert",
    }
)
_NAME_TOKEN_RE = re.compile(r"^[^\W\d_][\w'\-.]*$", re.IGNORECASE | re.UNICODE)


def _is_person_name(value: str) -> bool:
    """Is this a given name we can put after "Hi "?

    Until 2026-09-04 this was one regex with **no space in it**, so every given name written
    as two tokens was reported as "not a person's name": Hui Jie, Wei Ming, Siti, Mary Anne,
    Jean Luc. On a Singapore list that is not an edge case — romanised Chinese and Malay given
    names are routinely two tokens — so the rule systematically rejected the market the
    campaign was aimed at, and said something insulting while doing it.

    Three tokens is the ceiling: beyond that the field is carrying a title or a note, not a
    name. Digits, symbols and the placeholder vocabulary are refused exactly as before, and the
    blocklist now applies per token so "name unconfirmed" cannot slip through as two "words".
    """
    parts = value.split()
    if not parts or len(parts) > 3:
        return False
    return all(_NAME_TOKEN_RE.match(p) and p.lower() not in _NON_NAMES for p in parts)


class _NameRe:
    """Kept so existing call sites read unchanged; ``match`` now spans multi-token names."""

    @staticmethod
    def match(value: str):
        return _is_person_name(value) or None


_NAME_RE = _NameRe()

# CLOSED VOCABULARY. It used to GATE: `hedge-missing` matched a body against this tuple and
# nothing else, so a hedge that read as plain English but was not listed FAILED — which bit on
# 2026-08-21, when "If that is wrong, say so." had to be rewritten to a sanctioned cue mid-repair.
#
# `hedge-missing` retired 2026-09-24 and this tuple is now READ BY ONE THING:
# `_hedge_ngram_whitelist` below, which exempts a hedge's own 6-grams from the batch-duplication
# gate. So a new entry can no longer fail correct copy; the only way it can do harm is by
# whitelisting a 6-gram that `body-template-share` should have caught. Entries under NGRAM_N
# tokens cannot do even that. Keep adding phrases that actually shipped, keep the negative
# control, and do not widen it speculatively — the list is also the tenant-facing statement of
# what this house counts as hedging, mirrored in `voice-rules.toml` `[hedge].cues`.
HEDGE_CUES = (
    "tell me if you've got this covered",
    "tell me if this is already handled",
    "if you've already solved this",
    "my hunch",
    "my read",
    "my bet",
    "correct me if",
    "am i wrong",
    "you may well have this covered",
    # The close-touch out ("...say so and I will stop") is hedge language too: it concedes
    # the gap may not exist and hands the reader the exit. Added 2026-08-19 when the seat
    # specs moved from the hedge-colon frame to prediction framing + an explicit out.
    "say so and i will stop",
    # Added 2026-08-21 with the Phase 5 re-cut, which needed plain-speech hedges after the
    # operator's "sounds like AI slop, speak plainly" objection. Each of these is a phrase
    # that shipped in a re-cut body, not a speculative widening.
    "if that is wrong",
    "if i have that wrong",
    "if that is already covered",
    "if this is already covered",
    "you may already have",
    "if you already have",
    "tell me if i have this wrong",
    # Added 2026-09-04 from the sender's OWN sent mail, read against a batch he rejected as
    # "AI slop". The list above had made the slop mandatory: "tell me if this is already
    # handled" is IN it, and rotating that one sentence four ways to avoid repeating itself is
    # exactly what made the batch read as machine-written. His hedge is a shrug, not an
    # invitation to correct him.
    "might already be",
    "might already have",
    # Added 2026-09-05. voice.md rule 9 makes the CATEGORY claim the sanctioned way to state a
    # problem — "typically for platforms in that position, X" — and a category claim IS a hedge:
    # it concedes the reader may not have the problem at all, which is more than "my hunch" ever
    # did. `problem-asserts-internals` now REQUIRES one of these, so `hedge-missing` refusing to
    # count them put the two rules in direct contradiction.
    "typically for",
    "typically ",
    "tend to",
    "tends to",
    "usually",
    # Added 2026-09-24, closing the standing FR0 item "add `likely` before any voice doc
    # recommends it". It mirrors `voice-rules.toml` `[hedge].pending = ["likely"]`, and the
    # honest note is that on THIS side of the 2026-09-24 retirement it gates nothing: the one
    # rule that read `HEDGE_CUES` as a requirement (`hedge-missing`) is retired, so no body is
    # refused for lacking a hedge at all. The tuple's only surviving reader is
    # `_hedge_ngram_whitelist`, which ignores any cue under NGRAM_N tokens — so a one-word cue
    # also cannot widen the `body-template-share` exemption, which is the one way a new cue
    # could WEAKEN a kept gate. Both halves are asserted in
    # `test_outreach_linter.py::test_a_one_word_hedge_cue_cannot_widen_the_template_share_whitelist`.
    # Added 2026-08-26, replacing "say so and I will stop" on the close touch of the three
    # live v2 specs (admission/containment/questionnaire). Operator's read, backed by exec-
    # comms craft: "I will stop" centers the sender's future behaviour ("I was imposing, now
    # I'll quit"); "no reply needed" releases the reader from any obligation on THIS message
    # without promising future silence it can't keep on a multi-touch sequence — see
    # `cold-email-craft-evidence.md` §3.2 "easy out" / §6.3 (Singapore: silence must be a
    # costless, face-preserving answer). Reserved for the LAST touch, phrased as a genuine
    # one-time close ("this is my last note"), not reused on earlier touches where a real
    # follow-up is still coming.
    "likely",
)

#: Cues that count as *near-duplicate phrasing* for the homogeneity check, but NOT as
#: hedging a claim.
#:
#: "No reply needed" is an easy out: it releases the reader from replying. It does not
#: qualify the assertion it follows, so a body making a hard, unqualified claim about the
#: reader's business satisfied `hedge-missing` merely by appending a closing courtesy —
#: the gate passed while the defect it exists to catch went out. The distinction the two
#: tuples encode: `HEDGE_CUES` softens a CLAIM, this adds the phrasings that merely soften
#: the ASK. (The "last touch only" intent noted above was never enforceable here — the
#: linter sees one body at a time and cannot know which touch it is.)
HEDGE_CUES_HOMOGENEITY = HEDGE_CUES + ("no reply needed",)

STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "so",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "at",
    "by",
    "from",
    "as",
    "is",
    "are",
    "was",
    "be",
    "been",
    "it",
    "its",
    "that",
    "this",
    "those",
    "these",
    "your",
    "you",
    "their",
    "they",
    "them",
    "one",
    "no",
    "not",
    "can",
    "cant",
    "cannot",
    "do",
    "does",
    "did",
    "i",
    "my",
    "me",
    "we",
    "our",
    "us",
    "he",
    "she",
    "his",
    "her",
    "than",
    "then",
    "when",
    "once",
    "into",
    "over",
    "under",
    "out",
    "up",
    "down",
    "what",
    "which",
    "who",
    "whose",
    "how",
    "why",
    "where",
    "any",
    "all",
    "each",
    "every",
    "some",
    "most",
    "more",
    "less",
    "own",
    "same",
    "other",
    "hi",
    "henry",
    "want",
    "worth",
    "sending",
    "send",
    "plus",
    "short",
}


def _norm_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9$%.']+", text.lower())


def _content_words(text: str) -> set[str]:
    return {t for t in _norm_tokens(text) if t not in STOPWORDS and len(t) > 2}


def _ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _hedge_ngram_whitelist() -> set[tuple[str, ...]]:
    wl: set[tuple[str, ...]] = set()
    for cue in HEDGE_CUES_HOMOGENEITY:
        toks = _norm_tokens(cue)
        if len(toks) >= NGRAM_N:
            wl |= _ngrams(toks, NGRAM_N)
    return wl


def _sentences(text: str) -> list[str]:
    """Split a body into sentences.

    One implementation on purpose. `sentence-length`, `question-count` and the derivation
    rules all reason about "a sentence", and three inline regexes would eventually disagree
    about one — at which point two rules would be gating different objects while reporting the
    same word.
    """
    flat = re.sub(r"\s+", " ", text).strip()
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", flat) if x.strip()]


@cache
def _cue_re(cue: str) -> re.Pattern[str]:
    """Compile one title cue as a WORD-BOUNDED match.

    Cues used to be tested with plain ``in``, and three separate workarounds above record
    what that cost: bare ``cro``, ``coo`` and ``cio`` all had to be deleted from the
    vocabulary because they are substrings of ordinary words. The workaround was applied
    one cue at a time and missed the one that mattered most — **``cto`` is a substring of
    ``director``**, so every title carrying that word resolved to the CTO persona. Measured
    on the 1,193-row pool the day it was found (2026-09-08): 146 titles contain "director"
    and 128 of them were seated as CTO, including a Director of Information Security (a
    CISO), a Managing Director (a CEO), a Director of Product Development (a CPO), and a
    Director of Talent Acquisition (nobody). Nothing reported it, because a confidently
    wrong seat and a correct one look identical downstream.

    Boundaries are applied only where the cue's own edge is alphanumeric, so cues that end
    in punctuation (``chief a.i.``) still match the text that follows them.
    """
    left = r"\b" if cue[:1].isalnum() else ""
    right = r"\b" if cue[-1:].isalnum() else ""
    return re.compile(left + re.escape(cue) + right)


def _matches(cues: tuple[str, ...], low: str) -> bool:
    return any(_cue_re(c).search(low) for c in cues)
