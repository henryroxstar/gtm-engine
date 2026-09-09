#!/usr/bin/env python3
"""Outreach pack linter — deterministic gate for 1:1 cold-email packs (Tier-A manual
sends and sequencer step-1 bodies). Stdlib-only, CLI + importable, mirroring
content_linter conventions (ERROR blocks / WARN advises, --selftest, --ban-file).

Born from the 2026-07-16 Tier-A pack review: the drafting spec already mandated
hedged gaps, gift CTAs, and per-seat pain — and the pack violated all three while
passing the word-level lint. These checks are the mechanical subset with teeth:

  Pack-wide
    - Rules-Version header present and == RULES_VERSION (staleness gate: packs
      drafted under older rules fail closed and must be regenerated/re-reviewed)
    - template-share ceiling: no normalized 6-gram may appear in > MAX_NGRAM_EMAILS
      distinct emails (hedge-cue phrases whitelisted)
    - same-company divergence: recipients sharing a To: domain must differ in
      subject and keep pairwise content-word Jaccard <= JACCARD_MAX
    - no duplicate To: addresses
  Per-email
    - subject 1-4 words, lowercase, no placeholders/merge tags
    - greeting `Hi <First>,` matching the block header's first name
    - body ends with the bare sign-off line (default "Alex" — always pass --signoff
      with the real operator's name; the default is a generic placeholder, not a
      recommendation)
    - word count (hard 50-99, advisory 70-95)
    - no links, no em-dash, no spintax braces, no [INSERT-style placeholders
    - banned fluff words (built-in + optional --ban-file voice-bans.txt)
    - no antithesis constructions ("not X, it's Y")
    - banned stems (from --stem-file): a pack's signature mail-merge sentences may not
      recur; empty unless the operator supplies a stem file
    - named case studies (from --case-study-file): proof is company-TYPE, never the
      case-study company name (voice.md "Common mistakes"; an unknown logo confuses more
      than it credits, and Early-Access claims invite verification they may not survive;
      empty unless the operator supplies a case-study file). Exception lives
      in voice.md's FORMAL register (SG regulated, logo naming with permission) —
      that register is not linter-encoded; override manually for those sends. Note:
      the subject-lowercase rule below likewise assumes the informal/standard
      registers; formal-register SG sends (proper-cased subjects) need manual review.
    - hedge cue required (the "tell me if you've got this covered" family)
    - CTA: ends on a question, offers one artifact the profile can produce
      (--artifact-file), names what it does for them, shares vocabulary with the
      problem the body named (`cta-omits-gap`), never a time-ask
    - credit: the opener observes what their move signals; it never grades the
      decision (`credit-is-verdict`)
    - specificity anchors: >= MIN_ANCHORS digit-bearing or mid-sentence proper
      tokens (the mechanical proxy for "used real dossier facts")

Pack format (tier-a-manual-pack-*.md):
    Rules-Version: 2026-07-16          <- anywhere in the pack header
    ### 1. First Last · Title, Company
    **To:** a@b.com
    **Subject:** two words

    Hi First,
    ...body...
    Alex
    ---
"""

from __future__ import annotations

import argparse
import csv as _csv
import re
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# Same prologue `merge_render_linter.py` uses, and for the same reason: this file is run
# as a script from tests/linter/, so gtm_core is not importable without it.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gtm_core.finding_budget import WARN_BUDGET, budget_verdict, render_budget  # noqa: E402

RULES_VERSION = "2026-09-04"

# Under 100 words, hard. The old 110 ceiling predates the six-beat shape and let drafts sprawl to
# 104 once stakes and frontier beats were added; the fix is denser beats, not a wider ceiling.
WORDS_HARD_MIN, WORDS_HARD_MAX = 50, 99
# Soft band widened 2026-08-21 from 70-95. Measured against the copy the band is meant to
# describe: the H4 rewrite pins touch 1 at base + clause under a hard cap of 99, and the four
# re-cut specs render 69-96. The old band therefore fired at BOTH ends by exactly one word —
# 26 exec + 48 builder T3 renders at 69, three security T1 renders at 96 — none of which is a
# defect anyone would act on. A band that disagrees with the copy it governs is the band's bug,
# and every one of those 77 warnings was budget spent on nothing. 65 keeps a real floor (a
# truncated 30-word body still fires). The ceiling stays strictly BELOW WORDS_HARD_MAX on
# purpose: setting them equal would empty the warning shoulder entirely, so a body would go
# from "fine" to ERROR with no "approaching the cap" step — silently deleting a signal rather
# than recalibrating it. 97 clears the observed 96 and leaves 98-99 as that shoulder.
WORDS_SOFT_MIN, WORDS_SOFT_MAX = 65, 97

# Sentence-level readability. Boomerang's 5.3M-message analysis found third-grade-level copy
# replied ~36% better than college-level (~17% better than high-school), and Lavender scores cold
# openers to grade 3-5. Neither is gated here as a Flesch-Kincaid score, and that omission is
# deliberate rather than lazy: FK's syllable term is dominated by this ICP's unavoidable
# vocabulary (attribution, authorisation, compliance) and — worse for a MERGE linter — by the
# rendered company name, so one template would score a different grade row by row for reasons no
# writer can act on. Sentence length is the half of the formula the writer controls, so it is the
# half that is gated. Evidence: docs/cold-email-craft-evidence.md 5.5.
#
# Calibrated 2026-08-26 against 103 real first touches parsed out of content/ (40-130 words):
# mean sentence length p50 18.8 / p75 20.8 / p90 24.2 / max 31.7; longest sentence p50 33 /
# p75 37 / p90 39 / max 61. The corpus runs LONG — a 33-word sentence inside a 95-word body is a
# third of the email in one breath. Thresholds catch the TAIL, not the ideal: setting them at the
# evidence-ideal (mean ~14, longest ~25) would fail ~95% of existing copy, and a gate nobody can
# ship through has the same effect as a gate that never ran.
#
# The thresholds are ALSO set clear of merge-driven noise, which is what decided both numbers.
# Measured across 69 real touch templates rendered against a live CSV: the merged fields move a
# body's MEAN sentence length by 0.0-2.2 words row to row and its LONGEST by 0-6, purely from how
# long the company name and signal clause happen to be. A threshold inside that band fires on a
# subset of rows for a reason the writer cannot act on — the exact failure the WORDS_SOFT comment
# above documents (77 warnings spent on nothing). The observed data has a natural gap between a
# 39-word longest and the next template at 42, so:
#
#   HARD_MAX 40   sits in that gap. Fires on 8% of packs and on 2 of 69 templates (longest 42-45
#                 and 47-50) — both on EVERY row, so they collapse to one template-wide line.
#                 This is the live rule, and its message is directly actionable.
#   SOFT_MEAN 26  clears the entire observed template range (max mean 24.0) plus the maximum
#                 observed spread. It therefore fires on NOTHING today and is a regression guard,
#                 not a catch — stated plainly rather than implied to be finding something. It
#                 earns its place by covering a failure HARD_MAX cannot see: uniformly long-winded
#                 prose with no single wall. An earlier draft put it at 22, which straddled three
#                 live templates (21.8-22.5, 22.5-23.2, 22.5-24.0) and was pure noise.
#
# Ratchet both down as the corpus improves; the p-values above are the baseline to measure against.
SENTENCE_HARD_MAX = 40
SENTENCE_SOFT_MEAN = 26

# One CTA means one question. Boomerang put the useful range at 1-3 questions (~50% response
# lift); past that the reader must decide WHICH to answer, the same failure `cta-bundled` already
# gates for artifacts. On the same 103-email corpus this ceiling is PROPHYLACTIC, not remedial:
# the observed distribution is 0 questions x5, 1 x90, 2 x8 — nothing reaches 3, so this rule
# catches no present defect and is registered honestly as a regression guard. It earns its place
# because the failure it prevents is silent: extra questions read as thoroughness while splitting
# the reply, and no existing rule counts them (`cta-question` only inspects the LAST sentence).
QUESTIONS_MAX = 3
SUBJECT_MAX_WORDS = 4
MAX_NGRAM_EMAILS = 3  # a 6-gram may appear in at most this many emails
NGRAM_N = 6
JACCARD_MAX = 0.45
MIN_ANCHORS = 2
# Recalibrated 2026-08-21 from 4, per the rule-lifecycle band "fires on >40% of a live list =
# describes the list, doesn't screen it". At 4 this fired on 162 of 213 exec renders (76%) and
# 27 of 54 architect (50%) — saturated, so nobody read it. At 3 it SEPARATES: architect's touch 1
# carries 3 anchors and clears; exec's carries 2 and still fires. Exec is genuinely the thin one,
# which is the operator's own note 11 ("zero specific facts, could be sent to literally anyone")
# restated by a rule that had been firing all along and was unreadable.
SOFT_ANCHORS = 3
MAX_TOUCHES = 4  # voice.md's gift ladder caps at 4; a 5th correlates with rising spam/unsub
WORD_COUNT_TOLERANCE = 5  # a self-reported count may differ from the real one by this much

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
#: The one correct opening for a role inbox — voice.md §5b, and draft-outreach's body template
#: ("use `Hi team,` for a role inbox"). NOT an exemption from `greeting`: the rule still asserts
#: an exact opening, it just knows which one this pack owes.
ROLE_INBOX_GREETING = "Hi team,"

EM_DASH = "—"
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
SPINTAX_RE = re.compile(r"\{[^{}]*\|[^{}]*\}")
PLACEHOLDER_RE = re.compile(r"\[(INSERT|TBD|TODO|PLACEHOLDER|LINK|ONE LINK)", re.IGNORECASE)
# A plausible human first name: Unicode letters, optionally hyphenated/apostrophised ("Joaquín",
# "Josué", "Jean-Luc", "O'Brien"). Deliberately Unicode-aware — an ASCII-only rule would reject
# real names, which is how a "fix" for this class of bug usually breaks worse things. Bare
# placeholder words are rejected even when they are alphabetic.
#: Words that are never a given name, checked per TOKEN so "name unconfirmed" is refused
#: whole and "Hui Jie" is not.
_NON_NAMES = frozenset(
    {"name", "first", "firstname", "there", "team", "unconfirmed", "unknown", "tbd", "none"}
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
#: Valedictions that sit above the bare first name. Part of the sign-off block, never the body.
VALEDICTIONS = frozenset({"regards", "best", "best regards", "thanks", "cheers", "kind regards"})
MERGE_TAG_RE = re.compile(r"\{\{[^}]+\}\}")
ANTITHESIS_RES = (
    re.compile(r"\bnot just\b.{0,40}?\bbut\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bisn'?t\b.{0,40}?\bit'?s\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bit'?s not\b.{0,40}?\bit'?s\b", re.IGNORECASE | re.DOTALL),
)

BANNED_WORDS = (
    "excited to",
    "thrilled to",
    "reach out",
    "touch base",
    "synergy",
    "circle back",
    "i hope this finds you well",
    "i came across your profile",
    "i'd love to connect",
    "delve",
    "seamless",
    "robust",
    "elevate",
    "pivotal",
    "foster",
)
# "leverage" only as a verb-ish use; crude but effective: flag "leverage " + noun-phrase
LEVERAGE_RE = re.compile(r"\bleverag(e|ing|es|ed)\b", re.IGNORECASE)

# Mail-merge "stems" (a pack's signature sentences — recurrence means the draft fell back
# into the skeleton) and case-study company names are TENANT-SPECIFIC. They are supplied at
# call time via --stem-file / --case-study-file (one entry per line, `#` comments allowed;
# see _load_bans), so the shipped defaults are EMPTY and the linter enforces neither until an
# operator provides their own — mirroring content_linter's tenant denylist. A case study must
# be cited by company TYPE ("a regulated-FI compliance team"), never the logo (an unknown name
# confuses more than it credits) — the named-case-study rule flags any file-listed name.
DEFAULT_BANNED_STEMS: tuple[str, ...] = ()
DEFAULT_CASE_STUDY_NAMES: tuple[str, ...] = ()

# CLOSED VOCABULARY — deliberately, and the cost is real. `hedge-missing` matches a body against
# this tuple and nothing else, so a hedge that reads as plain English but is not listed FAILS.
# That bit on 2026-08-21: "If that is wrong, say so." is a perfectly good hedge and had to be
# rewritten to a sanctioned cue mid-repair. The alternative — inferring "is this sentence
# hedging?" — is a judgement call, and a fail-open one, which is worse than a list that surprises
# an author loudly. So: to hedge in a new shape, ADD IT HERE in the same commit as the copy, with
# a negative control. Do not widen it speculatively; every entry should be a phrase that shipped.
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
    # Added 2026-08-26, replacing "say so and I will stop" on the close touch of the three
    # live v2 specs (admission/containment/questionnaire). Operator's read, backed by exec-
    # comms craft: "I will stop" centers the sender's future behaviour ("I was imposing, now
    # I'll quit"); "no reply needed" releases the reader from any obligation on THIS message
    # without promising future silence it can't keep on a multi-touch sequence — see
    # `cold-email-craft-evidence.md` §3.2 "easy out" / §6.3 (Singapore: silence must be a
    # costless, face-preserving answer). Reserved for the LAST touch, phrased as a genuine
    # one-time close ("this is my last note"), not reused on earlier touches where a real
    # follow-up is still coming.
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

# --------------------------------------------------------------------------- CTA bundling
#
# "One CTA only" has always been the rule, but voice.md's own worked examples all bundled two
# deliverables into the ask ("want the one-pager + a short recorded demo?"), so 22 of 50 packs
# in the 2026-07-19 run shipped a compound question. The rule is one ARTIFACT, not one sentence.
#
# Multi-word artifact names must collapse to a single token BEFORE counting, or "a short demo
# recording" reads as two artifacts (demo + recording) and every clean CTA false-positives.
_ARTIFACT_PHRASE_RES = (
    re.compile(
        r"\b(?:an?|the)?\s*(?:short|brief|quick)?\s*(?:recorded\s+)?"
        r"(?:demo|demonstration)(?:\s+(?:recording|video|walkthrough))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:an?|the)?\s*(?:short|brief|quick)?\s*recorded\s+(?:walkthrough|video|recording)\b",
        re.IGNORECASE,
    ),
)
# An offer of WORK, not of an artifact: "want me to sketch…", "happy to map where that
# breaks". voice.md sanctions this as the default ask for builder/founder seats (see *The
# help offer*) precisely because it gates nothing — there is no asset to receive and no
# obligation created. `_OFFER_RE` cannot tell it apart from "want the one-pager?" (both are
# an offer verb plus a determiner), so without this exemption `cta-unstaged-artifact` would
# demand a gift-artifacts.txt entry for an ask that offers no gift. The other CTA rules
# still apply — it must be a question, and it may not overclaim.
# The verb slot excludes verbs of GIVING: "want me to send the benchmark report?" is an
# artifact offer wearing the help offer's grammar, and it is exactly the ask
# `cta-unstaged-artifact` exists to refuse. A help offer's verb governs an action the
# sender performs (sketch, map, look, walk through), never the handover of an asset.
#: Giving verbs, in every tense — "would it be helpful if I SENT you the one-pager" is an
#: artifact gate wearing the help-offer's clothes, and the present-tense-only list let it through.
_HELP_VERB = (
    r"(?!send\b|sent\b|share\b|shared\b|forward\b|forwarded\b|pass\b|passed\b|get\b|got\b"
    r"|shoot\b|drop\b|dropped\b|give\b|gave\b|to\b)[a-z]+"
)
#: "If useful, I can map where that comes apart." — an offer stated as a capability, which is
#: how this sender actually offers ("Do let me know ... I can adjust the data pipelines"). Added
#: 2026-09-04 when "happy to" was retired from the voice: without this the only sanctioned offer
#: shapes were the two the operator had just rejected.
_CAN_OFFER = rf"\b(?:i|we)\s+(?:can|could)\s+{_HELP_VERB}"
#: "Would it be helpful if I mapped X against §2.1.2?" — the sender's own phrasing, and a better
#: offer than a capability statement: it asks permission rather than announcing intent, and it
#: names a deliverable the reader can picture. Added 2026-09-04 when "I can / I could" was
#: retired alongside "happy to".
_WOULD_OFFER = rf"\bwould it be (?:helpful|useful)\b[^?]{{0,40}}\b(?:i|we)\s+{_HELP_VERB}"
CTA_HELP_OFFER_RE = re.compile(
    rf"\b(?:want|would you want|shall|should|happy)\b[^?]{{0,20}}\b(?:me|i|we)\b\s+"
    rf"(?:to\s+)?{_HELP_VERB}"
    rf"|\bhappy to\s+{_HELP_VERB}"
    rf"|{_CAN_OFFER}"
    rf"|{_WOULD_OFFER}",
    re.IGNORECASE,
)

_ARTIFACT_TOKEN = " ⟦artifact⟧ "
# An offer-shaped CTA: a verb of giving plus a determiner. Distinguishes "want the X?" from a
# pure interest ask ("is this on your radar this quarter?"), which gates nothing and is fine.
_OFFER_RE = re.compile(
    # `sent`/`sending` added 2026-08-19: the humbler ask shape ("would it help if I *sent*
    # the one-pager…") slipped past a `send`-only pattern, silently exempting it from
    # `cta-unstaged-artifact`.
    r"\b(want|send|sent|sending|share|sharing|worth|interested in)\b[^?]*\b(the|a|an)\b",
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- credit is a verdict
#
# voice.md has banned this since 2026-08-19 ("credit is an observation, never a verdict — you
# don't know them well enough to grade the decision") and nothing enforced it, so it kept
# shipping. On 2026-09-04 the operator read five packs that had each passed at zero errors and
# said they sounded salesy; the second sentence of each was a stranger grading a company's
# strategy. An unenforced rule in a voice file is a suggestion.
#
# Scoped to the OPENER (the first two sentences), where the credit beat lives. A verdict later
# in a body is usually quoting the reader or describing the case study, and flagging it there
# would convict correct copy — which is how a gate loses its authority.
#
# High-precision phrases only: this list convicts, so it holds no word that has an innocent
# reading in the credit position. "lands" is included ONLY in the 2026-07-03 stem shape
# ("<their thing> lands:"), never as a bare verb.
CREDIT_VERDICT_RE = re.compile(
    r"\b("
    r"smart move|the right call|the right (?:foundation|approach|bet|architecture|move)"
    r"|makes? (?:a lot of )?sense|impressive|nice work|great to see|love (?:what|how|that)"
    r"|well[- ]positioned|ahead of the curve|exactly right|spot on|bold (?:move|bet)"
    r"|kudos|congrats|congratulations|strong (?:move|play|bet)"
    r")\b"
    # COMPARATIVE GRADING — the shape the phrase list above misses, and the one that
    # actually shipped. All five packs reviewed 2026-09-04 passed every gate while
    # opening on "further into production than most agent platforms claim", "wider
    # channel coverage than most voice bots reach", "further than most agent platforms
    # get". None contains a banned phrase; each ranks the reader against a peer set,
    # which is a verdict with extra steps — and it is the specific thing the operator
    # named as a fake compliment. Requires an explicit comparison class ("than most",
    # "than any", "than typical") so an ordinary comparative ("faster than the last
    # release") does not convict.
    r"|\b(?:further|wider|deeper|faster|more|earlier|ahead of|beyond)\b[^.?!]{0,60}"
    r"\bthan (?:most|many|any|almost|nearly|typical|the average|other)\b"
    r"|\bthan (?:most|many|any) [a-z-]+ (?:platforms?|vendors?|teams?|companies|bots?|"
    r"startups?|builders?)\b"
    # Peer-set comparison with the "than" left implicit: "most platforms would still
    # route that to a human" ranks the reader above a class without ever saying "than".
    # Must be MID-CLAUSE (preceded by lowercase prose), which is what separates a
    # ranking from a mechanism claim: "…a contractual call most platforms would still
    # route to a human" grades the reader; "Most enterprise teams cannot answer that
    # question" is a fact about the field and belongs in the body.
    r"|[a-z,]\s+most [a-z-]* ?(?:platforms?|vendors?|teams?|companies|bots?|startups?|"
    r"builders?|agents?)\b[^.?!]{0,30}\b(?:would|still|do not|don't|can't|cannot|never|"
    r"rarely)\b"
    # The grading ADJECTIVE: "that's a real handoff", "that's a rare bet". Praise words
    # only — nothing that could be describing a problem ("a serious gap") — because this
    # is scoped to the opener, where the sentence is about THEM.
    r"|\bthat[''`]?s (?:a|an) (?:real|genuine|rare|remarkable|impressive|unusual|serious"
    r"ly good)\b"
    # the stem shape only: a determiner-led noun phrase graded by a bare "lands"
    r"|^[^.?!]{0,60}\blands\b\s*[:.]",
    re.IGNORECASE | re.MULTILINE,
)

#: How many leading sentences count as "the opener" for `credit-is-verdict`.
CREDIT_OPENER_SENTENCES = 2

# --------------------------------------------------------------------------- CTA overclaim
#
# The ask may name what the artifact CONTAINS; it may not promise what the artifact ACHIEVES
# inside the reader's own regulatory or procurement environment. "the one-pager on the
# per-agent trail an examiner accepts" tells a bank CISO — who has personally survived
# examinations — both that a one-pager settles his examiner and, by implication, that he did
# not know how. Flagged by the operator 2026-08-19 reading a regional bank's draft.
#
# Note this rule convicts voice.md's OWN worked examples ("how that clears the review",
# "turns those questionnaire items into a yes"); the guidance was generating the defect, and
# was rewritten in the same change. Content anchors satisfy `cta-unanchored` without claiming
# a result in someone else's world.
_CTA_AUTHORITY = (
    r"(review|reviews|examiner|examiners|auditor|auditors|regulator|regulators|audit"
    r"|procurement|assessment|questionnaire|committee|diligence)"
)
_CTA_OUTCOME_VERB = (
    r"(clears?|clearing|passes?|passing|satisf(?:y|ies|ying)|accepts?|approves?"
    r"|signs? off|survives?|gets? (?:you )?through|turns? [^?]{0,25} into a yes"
    # standalone too: in "turns those questionnaire items into a yes" the authority noun
    # sits INSIDE the verb phrase, so neither ordered branch matches without this.
    r"|into a (?:yes|pass))"
)
CTA_OVERCLAIM_RE = re.compile(
    rf"\b{_CTA_OUTCOME_VERB}\b[^?]{{0,40}}\b{_CTA_AUTHORITY}\b"
    rf"|\b{_CTA_AUTHORITY}\b[^?]{{0,25}}\b{_CTA_OUTCOME_VERB}\b",
    re.IGNORECASE,
)

_ARTIFACT_NOUNS = (
    "one-pager",
    "onepager",
    "one pager",
    "write-up",
    "writeup",
    "breakdown",
    "teardown",
    "walkthrough",
    "recording",
    "mockup",
    "deck",
    "audit",
    "teaser",
    "read",
)

# --------------------------------------------------------------------------- persona lead pain
#
# voice.md's persona-axis table says what each seat leads on. The recurring failure is firing the
# CISO's attribution pain at every seat, because attribution is what WE find interesting: a
# 2026-08-17 batch sent audit/examiner framing to eight CEOs and a revenue lead to a real CISO.
#
# Seat -> (title cues, stakes vocabulary that belongs to this seat).
# --- the persona axis (one vocabulary, two views) ------------------------------------
#
# ONE ordered title vocabulary resolves a recipient to a PERSONA; a small map folds those
# personas into the coarser SEAT that ``lint_persona_lead`` reasons about. Defining it once
# is the point: before 2026-08-20 this module had three seats and
# ``gtm_core.hook_coverage`` had its own ten-persona list, and two cue lists for one
# question drift apart — the finer one already knew titles the coarser one did not
# ("Chief AI Officer" resolved to a seat but to no persona, and vice versa).
#
# ORDER IS LOAD-BEARING, and every omission below carries its reason:
#   * ``data-compliance`` precedes ``compliance`` — "Data / Compliance Leader" and
#     "CRO / Compliance" both contain "compliance" and are different buyers.
#   * ``ciso`` precedes ``cloud-architect`` — "chief information security" must not be
#     claimed by "chief information".
#   * every seat-bearing persona precedes ``ceo`` — "Senior Vice President, Chief
#     Technology Officer" is a CTO, not an exec. 70 of the 123 live titles containing
#     "president" are VICE-presidents; ordering resolves the ones carrying a seat marker
#     and ``_ANTI_CUES`` catches the rest.
#   * bare "cro" is absent (Chief Revenue vs Chief Risk sit in opposite seats), bare "coo"
#     is absent (substring of "coordinator"), bare "cio" is absent (substring of ordinary
#     words) — the prefixed forms that actually occur are listed instead.

#: Titles that disqualify a persona even when one of its cues matched. "Executive Vice
#: President, Engineering" is not a CEO; without this, the bare "president" cue makes every
#: VP an exec — the same substring trap as coo/coordinator, found 2026-08-20 mis-seating 28
#: CTOs into the exec bucket.
_ANTI_CUES: dict[str, tuple[str, ...]] = {
    "ceo": ("vice president", "vice-president", "evp", "svp", "avp"),
}

_PERSONA_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "data-compliance",
        (
            "chief data",
            "data governance",
            "data protection officer",
            "data / compliance",
            "data and compliance",
            "data privacy",
            "head of data",
            "chief analytics",
            "data & analytics",
            "data and analytics",
            "dpo",
        ),
    ),
    (
        "ciso",
        (
            "ciso",
            "chief information security",
            "chief security",
            "head of security",
            "head of infosec",
            "security officer",
            "vp security",
            "director of security",
            # Recovered by the word-boundary fix: "Director of Information Security" used
            # to be claimed by the bare "cto" cue inside the word "director" before any
            # security cue was reached. It is a CISO and always was.
            "information security",
            "security engineering",
            "head of cyber",
            "cyber security",
            "cybersecurity",
        ),
    ),
    (
        "compliance",
        (
            "cro / compliance",
            "chief risk",
            "chief compliance",
            "head of compliance",
            "head of risk",
            "compliance officer",
            "risk officer",
            "regulatory affairs",
            "head of audit",
            "internal audit",
            "financial crimes",
            "compliance",
        ),
    ),
    (
        "ai-platform",
        (
            "chief ai",
            "chief a.i.",
            "head of ai platform",
            "ai platform",
            "head of ai",
            "head of applied ai",
            "ai engineering",
            "machine learning platform",
            "ml platform",
            "head of ml",
            "vp of ai",
            "vp ai",
            "genai",
            "generative ai",
        ),
    ),
    # Resolver-only personas: recognised so they are never invisible, but mapped to NO
    # seat below, so no copy is owed to them. Measured 2026-08-20 across the whole
    # 870-title pool: finops 0 recipients, partnership 1. Writing an argument for a
    # persona nobody holds is how a message portfolio gets padded instead of aimed.
    (
        "partnership",
        (
            "ecosystem / partnership",
            "head of partnerships",
            "head of partnership",
            "head of ecosystem",
            "partner platform",
            "platform bd",
            "business development",
            "alliances",
        ),
    ),
    (
        "finops",
        (
            "finops",
            "fin ops",
            "cloud economics",
            "cloud cost",
            "cost optimisation",
            "cost optimization",
        ),
    ),
    (
        "cloud-architect",
        (
            "cloud architect",
            "enterprise architect",
            "principal architect",
            "chief architect",
            "solutions architect",
            "solution architect",
            "head of architecture",
            "chief information officer",
            "chief information and digital",
            "chief digital",
            "group cio",
            # "chief information" is safe here only because ``ciso`` is evaluated FIRST
            # and claims "chief information security" — order is load-bearing.
            "chief information",
        ),
    ),
    (
        "cto",
        (
            "cto",
            "chief technology",
            "technology officer",
            "head of technology",
            "founding engineer",
            "head of platform",
            "head of engineering",
            "vp engineering",
            "vp of engineering",
            "director of engineering",
            "head of infrastructure",
        ),
    ),
    (
        "cpo",
        (
            "cpo",
            "chief product",
            "head of product",
            "vp product",
            "vp of product",
            "director of product",
            "product management",
            "product lead",
        ),
    ),
    (
        "ceo",
        (
            "ceo",
            "chief executive",
            "founder",
            "co-founder",
            "cofounder",
            "managing director",
            "chief operating",
            "chief operations",
            "president",
            "owner",
        ),
    ),
    # LAST on purpose, so it only ever catches what the named seats did not. This is the
    # DEFAULT seat for an owner-operator title, not a seat of its own: below a certain
    # headcount there is no functional split, and the person who signed the company up is
    # the buyer, the architect and the security reviewer at once. It maps to the ``ceo``
    # SEAT below, so it is owed no new copy — the hook matrix's CEO / Founder row is
    # already the right argument for this reader.
    #
    # It exists as a distinct PERSONA rather than as more ``ceo`` cues so that "resolved
    # because the title says CEO" and "resolved because nothing else fit and this is an
    # SME" stay countable apart. The first is a fact about the person; the second is an
    # inference about the company, and only one of them should survive contact with an
    # enterprise list.
    (
        "founder-operator",
        (
            "executive director",
            "managing partner",
            "founding partner",
            "senior partner",
            "managing principal",
            "proprietor",
            "business owner",
            "co-owner",
        ),
    ),
)
# A BARE "Director" is deliberately absent. It is the owner at a ten-person agency and a
# mid-level manager at a bank, and as a cue it matched every functional director in the
# pool — "Sales Director" and "Director of Talent Acquisition" both resolved here, which is
# the same over-reach the "cto"-in-"director" bug was made of, arriving from the other
# direction. An ambiguous title stays unrecognised and is handled by
# :data:`SEGMENT_DEFAULT_PERSONA` at routing time, where the caller knows the segment and
# this vocabulary does not.

#: Titles that are recognisably NOT a buyer for this product — see :func:`non_buyer_of`.
#: Kept small and unambiguous on purpose: the cost of a missing entry is one wasted send,
#: and the cost of an over-broad one is a dropped account that was never given a chance.
_NON_BUYER_CUES: tuple[str, ...] = (
    "administrative assistant",
    "executive assistant",
    "personal assistant",
    "office manager",
    "receptionist",
    "intern",
    "recruiter",
    "talent acquisition",
    "office administrator",
)

#: Where a recipient goes when :func:`persona_of` cannot see their seat. The point of a
#: default is that the None bucket stops being silently handed whatever persona a spec
#: declared: on the 2026-09-04 SG-builder roster, 10 of 18 merge-lane recipients held a
#: title the matrix had no row for, and each was sent an argument written for a seat
#: nobody had checked they held.
SEGMENT_DEFAULT_PERSONA = "founder-operator"

#: ``(seat, personas it covers, the seat's own stakes vocabulary)``.
#:
#: Six seats, sized to where recipients actually are (measured on the 397 live rows,
#: 2026-08-20): ceo 113 · cto 106 · security 97 · cloud-architect 30 · cpo 11 ·
#: ai-platform 3. The old three-seat split put 156 people in one "exec" bucket that held
#: CEOs, CPOs, data leaders and — through the bare "president" cue — 28 CTOs.
#:
#: ``finops`` and ``partnership`` resolve as personas but map to NO seat, so
#: ``lint_persona_lead`` stays silent on them: they have 0 and 1 recipients respectively
#: in the entire pool, and a seat with no recipients is a copy obligation with no reader.
_SEAT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "security",
        ("ciso", "compliance", "data-compliance"),
        (
            "auditor",
            "examiner",
            "regulator",
            "audit",
            "evidence",
            "policy",
            "breach",
            "incident",
            "control",
            "attestation",
        ),
    ),
    (
        "ceo",
        # `founder-operator` shares this seat deliberately: an SME owner reads the CEO /
        # Founder argument, so the default costs no new copy. The two stay distinct as
        # PERSONAS so the inference is countable; they are one SEAT because the stakes are.
        ("ceo", "founder-operator"),
        (
            "deal",
            "customer",
            "enterprise",
            "procurement",
            "buyer",
            "revenue",
            "review stalls",
            "security review",
            "sales",
            "contract",
            "adoption",
            "rollout",
        ),
    ),
    (
        "product",
        ("cpo",),
        (
            "roadmap",
            "feature",
            "ship",
            "backlog",
            "product",
            "launch",
            "customer",
            "adoption",
            "differentiat",
            "build",
        ),
    ),
    (
        "cto",
        ("cto",),
        (
            "engineer",
            "build",
            "rebuild",
            "integration",
            "framework",
            "stack",
            "per deployment",
            "per customer",
            "fragment",
            "maintain",
            "wire",
            "retrofit",
            "ship",
        ),
    ),
    (
        "architect",
        ("cloud-architect",),
        (
            "standard",
            "standardise",
            "standardize",
            "platform",
            "estate",
            "multi-cloud",
            "portable",
            "lock-in",
            "interoperab",
            "topology",
            "reference architecture",
            "stack",
        ),
    ),
    (
        "ai-platform",
        ("ai-platform",),
        (
            "model",
            "agent",
            "framework",
            "orchestrat",
            "production",
            "observability",
            "guardrail",
            "evaluation",
            "latency",
            "pipeline",
        ),
    ),
)

#: ``persona -> seat``, derived so the two views can never disagree.
_PERSONA_TO_SEAT: dict[str, str] = {
    persona: seat for seat, personas, _ in _SEAT_RULES for persona in personas
}

# Vocabulary that belongs to security and reads as borrowed in any other seat's email.
_SECURITY_ONLY = ("auditor", "examiner", "audit trail", "attribution", "attributable")

TIME_ASK_TOKENS = (
    "15 minutes",
    "20 minutes",
    "quick call",
    # A meeting ask wearing an adjective. "Worth a short call?" shipped in the 2026-07-19 run
    # because every listed token assumed the word "quick" or a calendar noun.
    "short call",
    "brief call",
    "intro call",
    "introductory call",
    "quick chat",
    "short chat",
    "hop on a call",
    "jump on a call",
    "calendly",
    "calendar link",
    "grab time",
    "find time",
    "book a",
    "schedule a",
    "meet next week",
    "chat this week",
    "minutes this week",
    "a call this",
)

# Word-boundary matched, not substring: a plain `t in low` check for "book a" matched
# inside "excess and surplus book and workbench..." (found 2026-08-12 on an insurer's
# row) because "book a" is literally a substring of "book and". `\bTOKEN\b`
# requires a non-word character (or string edge) on both sides, so "book a" no longer
# matches when the very next character is "n" — a real time-ask still matches, since
# it is followed by whitespace/punctuation, not glued to another word.
_TIME_ASK_RE = tuple((t, re.compile(r"\b" + re.escape(t) + r"\b")) for t in TIME_ASK_TOKENS)

# Tokens that never count as specificity anchors even when capitalized mid-sentence.
ANCHOR_STOPLIST = {
    "Hi",
    "Alex",
    "I",
    "AI",
    "IAM",
    "MCP",
    "PHI",
    "HIPAA",
    "API",
    "APIs",
    "IT",
    "OK",
    "CTA",
    "CEO",
    "CTO",
    "CISO",
    "CFO",
    "CMO",
    "GC",
    "SVP",
    "EVP",
    "VP",
    "The",
    "A",
    "An",
    "And",
    "But",
    "So",
    "My",
    "Your",
    "Their",
    "That",
    "This",
    "Want",
    "Worth",
    "When",
    "Once",
    "With",
    "For",
    "If",
    "It",
    "No",
    "Not",
}

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


@dataclass
class Violation:
    level: str  # "ERROR" | "WARN"
    email: str  # recipient label or "PACK"
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.email}: {self.rule} — {self.detail}"


@dataclass
class EmailBlock:
    index: int
    header: str  # "First Last · Title, Company"
    first: str
    company: str
    to: str
    subject: str
    body: str  # plain text, ends with sign-off line

    @property
    def label(self) -> str:
        return f"#{self.index} {self.to}"


@dataclass
class PackMeta:
    """Format-specific fields that live OUTSIDE the email body.

    The per-email rules in `lint_email` judge the text that gets sent. These are the things a
    reader of the body alone cannot see — the attachment the header promises, the word count the
    drafter claimed, the sequence shape planned underneath — and every one of them was a real
    2026-08-17 audit finding that no body-level rule could have caught.
    """

    fmt: str  # "tier-a-manual" | "draft-outreach" | "prospect-pack"
    label: str = "PACK"
    attach: str | None = None  # an Attach:/Gift-artifact header field, if the format has one
    stated_words: int | None = None  # the drafter's self-reported count
    declared_touches: int | None = None  # what the sequence header claims
    enumerated_touches: int = 0  # how many touches are actually written out
    touch_channels: tuple[str, ...] = ()
    has_linkedin_dm: bool = False


def _artifact_count(sentence: str) -> int:
    """Distinct gift artifacts named in one sentence (multi-word names collapsed first)."""
    s = sentence
    for rx in _ARTIFACT_PHRASE_RES:
        s = rx.sub(_ARTIFACT_TOKEN, s)
    low = s.lower()
    found = {"demo"} if _ARTIFACT_TOKEN.strip() in s else set()
    for noun in _ARTIFACT_NOUNS:
        if re.search(r"(?<!\w)" + re.escape(noun) + r"(?!\w)", low):
            found.add(noun)
    return len(found)


_BLOCK_RE = re.compile(
    r"^###\s+(?P<idx>\d+)\.\s+(?P<header>.+?)\s*\n"
    r"\*\*To:\*\*\s*(?P<to>\S+)\s*\n"
    r"\*\*Subject:\*\*\s*(?P<subject>.+?)\s*\n"
    r"(?P<body>.*?)(?=\n---\s*\n|\n###\s+\d+\.|\Z)",
    re.DOTALL | re.MULTILINE,
)


def parse_pack(text: str) -> tuple[str | None, list[EmailBlock]]:
    """Return (rules_version, blocks)."""
    mv = re.search(r"^Rules-Version:\s*(\S+)", text, re.MULTILINE)
    version = mv.group(1) if mv else None
    blocks: list[EmailBlock] = []
    for m in _BLOCK_RE.finditer(text):
        header = m.group("header").strip()
        first = header.split()[0] if header.split() else ""
        company = header.split(",")[-1].strip() if "," in header else ""
        blocks.append(
            EmailBlock(
                index=int(m.group("idx")),
                header=header,
                first=first,
                company=company,
                to=m.group("to").strip().lower(),
                subject=m.group("subject").strip(),
                body=m.group("body").strip(),
            )
        )
    return version, blocks


# Two written forms of the same ladder line are in the corpus and both are correct markdown:
#     **Touch 2 — Day 2 — email:** ...
#     **Touch 2 (Day 2, email):** ...
# Matching only the dash form made every pack written in the paren form parse as ZERO touches,
# so `touch-count` and `channel-order` were inert on them — a silent blind spot rather than a
# reported one, which is the same shape as the `## Email (touch 1)` heading fix above.
_TOUCH_RE = re.compile(
    r"^[-*\s]*\*\*Touch\s+(?P<n>\d+)\s*(?:[—–-]\s*Day\s+(?P<day>\d+)\s*[—–-]\s*"
    r"|\(\s*Day\s+(?P<day2>\d+)\s*,\s*)(?P<channel>\w+)",
    re.MULTILINE,
)
# `## Sequence: 4 touches / 2 channels over ~12 days` and `## Sequence — 4 touches` both
# declare the same count; so does `## Sequence: manual, 2 touches — NOT sequencer-eligible`.
_SEQ_HEADER_RE = re.compile(r"^##\s*Sequence\s*[:—–-][^\n]*?(?P<n>\d+)\s+touches", re.MULTILINE)
_STATED_WORDS_RES = (
    re.compile(r"\*\*Word count:\*\*\s*~?\s*(\d+)"),
    re.compile(r"\((\d+)\s*words?\)"),
)

# The touch-1 heading is written several ways across the corpus:
#   ## Email (touch 1)
#   ## Email (touch 1, to Jordan Vance)        <- supplement packs name the recipient
#   ## Email (touch 1) — formal/standard register  <- register annotation after the paren
# Matching the bare literal `## Email (touch 1)` made two real packs unparseable — and therefore
# silently UNLINTED, reported only as a `parse` error that read like a malformed file rather than
# a linter blind spot. Both the in-paren suffix and any trailing annotation must be tolerated;
# anchoring the end of the line instead was a stricter rule than the literal it replaced.
_EMAIL_HEADING_RE = re.compile(r"^##[ \t]*Email[ \t]*\(touch[ \t]*1[^)\n]*\)[^\n]*$", re.MULTILINE)


def _strip_quote(text: str) -> str:
    """Drop one level of markdown blockquote markers."""
    return "\n".join(re.sub(r"^\s*>\s?", "", ln) for ln in text.splitlines()).strip()


def _quoted_body(text: str) -> str:
    """The contiguous blockquote that is the email itself.

    Stops at the first non-quoted line so trailing annotations — `(88 words)`,
    `**Word count:** 90 / 100` — stay out of the body they are reporting on.
    """
    lines = text.splitlines()
    out: list[str] = []
    started = False
    for ln in lines:
        if ln.lstrip().startswith(">"):
            started = True
            out.append(ln)
        elif started and not ln.strip():
            out.append("")
        elif started:
            break
    return _strip_quote("\n".join(out))


def _strip_meta_lines(body: str) -> str:
    """Drop drafter annotations that trail the body but are not part of the sent email."""
    body = re.split(r"\n\s*\*\*Word count:\*\*", body)[0]
    return re.sub(r"\n\s*\(\s*\d+\s*(?:words?|characters?)\s*\)\s*$", "", body).strip()


# Honorifics are not first names: "Dr Meilin Zhao" greets Meilin, not "Dr".
_HONORIFICS = {"dr", "mr", "mrs", "ms", "mx", "prof", "professor", "sir", "dame"}


def _first_name(raw: str) -> str:
    """First name from a persona/recipient field, or the sentinel when unresolved."""
    raw = raw.strip()
    if UNRESOLVED_SENTINEL in raw:
        return UNRESOLVED_SENTINEL
    if ROLE_INBOX_SENTINEL in raw:
        return ROLE_INBOX_SENTINEL
    head = re.split(r"\s*[—–,|(]", raw, maxsplit=1)[0].strip()
    toks = head.split()
    while toks and toks[0].lower().rstrip(".") in _HONORIFICS:
        toks.pop(0)
    return toks[0] if toks else ""


def parse_ladder(text: str) -> list[tuple[int, int, str]]:
    """``(touch number, day, channel)`` for every touch a pack enumerates, in touch order.

    The one home for reading a pack's ladder. The status page needs the *days* to say when a
    hand-sent 1:1 arc actually finishes, and a second regex for that would be a second answer
    to "how many emails does this pack send, and over how long" — the failure the pack/spec
    parsers are deliberately shared to avoid.
    """
    out = [
        (int(m.group("n")), int(m.group("day") or m.group("day2") or 0), m.group("channel"))
        for m in _TOUCH_RE.finditer(text)
    ]
    out.sort(key=lambda p: p[0])
    return out


def _parse_sequence(text: str) -> tuple[int | None, int, tuple[str, ...]]:
    """(declared touches, enumerated touches, channel per touch in order)."""
    m = _SEQ_HEADER_RE.search(text)
    declared = int(m.group("n")) if m else None
    touches = parse_ladder(text)
    return declared, len(touches), tuple(c for _, _d, c in touches)


def _stated_words(text: str) -> int | None:
    for rx in _STATED_WORDS_RES:
        m = rx.search(text)
        if m:
            return int(m.group(1))
    return None


def parse_draft_outreach_pack(text: str) -> tuple[str | None, list[EmailBlock], PackMeta]:
    """Parse the single-email `email-<account>-<date>.md` shape (draft-outreach output).

        # Cold email — Acme — 2026-07-03
        - **To:** Dana Rivera, Head of Platform — dana@acme.dev
        - **Why-now:** ...
        ---
        **Subject:** agent audit trail
        Hi Dana,
        ...
        Alex
        ---
        **Word count:** 85 · **Voice check:** ...

    Only touch 1 is linted; the `## Follow-up` section below it is a later touch and the
    first-touch rules (no attachment, no time-ask) do not apply to it.
    """
    version = (lambda m: m.group(1) if m else None)(
        re.search(r"^Rules-Version:\s*(\S+)", text, re.MULTILINE)
    )
    head = text.split("**Subject:**", 1)[0]
    to_raw = (lambda m: m.group(1) if m else "")(re.search(r"\*\*To:\*\*\s*(.+)", head))
    attach = (lambda m: m.group(1).strip() if m else None)(
        re.search(r"\*\*(?:Attach|Attachment|Gift artifact):\*\*\s*(.+)", head)
    )
    company = (lambda m: m.group(1).strip() if m else "")(
        re.search(
            r"^#\s*Cold email\s*[—–-]\s*(.+?)\s*[—–-]\s*\d{4}-\d{2}-\d{2}", text, re.MULTILINE
        )
    )
    email = (lambda m: m.group(0).lower() if m else "")(
        re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", to_raw)
    )

    m = re.search(
        r"\*\*Subject:\*\*\s*(?P<subject>.+?)\s*\n(?P<body>.*?)(?=\n---|\Z)", text, re.DOTALL
    )
    blocks: list[EmailBlock] = []
    if m:
        blocks.append(
            EmailBlock(
                index=1,
                header=to_raw.strip(),
                first=_first_name(to_raw),
                company=company,
                to=email or company.lower(),
                subject=m.group("subject").strip(),
                body=_strip_meta_lines(_strip_quote(m.group("body"))),
            )
        )
    meta = PackMeta(
        fmt="draft-outreach",
        label=blocks[0].label if blocks else "PACK",
        attach=attach,
        stated_words=_stated_words(text.split("## Follow-up", 1)[0]),
    )
    return version, blocks, meta


#: The Tier-A pack heading, in BOTH shapes it has been authored in: `# Outreach Pack — Acme
#: — 2026-07-19` (the template's) and `# Outreach Pack: Acme (2026-09-04)`. A parser that
#: accepts only one does not fail — it yields an empty company, which reaches the judge as
#: missing context rather than as an error. On 2026-09-04 all six packs of one campaign
#: parsed with `company == ""` for exactly that reason.
_PACK_HEADING_RE = re.compile(
    r"^#\s*Outreach Pack\s*[—–:-]\s*(?P<company>.+?)\s*(?:[—–-]|\()\s*\d{4}-\d{2}-\d{2}\)?\s*$",
    re.MULTILINE,
)


def parse_prospect_pack(text: str) -> tuple[str | None, list[EmailBlock], PackMeta]:
    """Parse the `prospects-<date>-outreach-<account>.md` shape (prospect skill Tier-A pack).

    # Outreach Pack — Acme — 2026-07-19
    **Tier:** A | **Score:** 6/12 | ...
    **Primary persona:** Dana Rivera — Head of Platform
    **Gift artifact:** teaser one-pager
    ## LinkedIn DM (≤ 280 chars)
    > ...
    ## Email (touch 1)
    **Subject:** agent audit trail
    > Hi Dana,
    > ...
    > Alex
    (88 words)
    ## Sequence — 4 touches / 2 channels over ~12 days
    - **Touch 1 — Day 0 — LinkedIn:** ...
    """
    version = (lambda m: m.group(1) if m else None)(
        re.search(r"^Rules-Version:\s*(\S+)", text, re.MULTILINE)
    )
    # Supplement packs write `**Persona:**`; without the fallback they parse with an empty name
    # and the greeting check silently passes on nothing.
    persona = (lambda m: m.group(1) if m else "")(
        re.search(r"\*\*(?:Primary persona|Persona):\*\*\s*(.+)", text)
    )
    company = (lambda m: m.group("company").strip() if m else "")(_PACK_HEADING_RE.search(text))
    # The sendable address, as a FIELD rather than prose. Without one the judge's record has
    # no join key and `to` falls back to the company slug, which is a folder name, not a
    # recipient. Read only from the front block (above the first `## `) so a mailbox quoted
    # in a later notes section can never be mistaken for the addressee.
    front = text.split("\n## ", 1)[0]
    contact_raw = (lambda m: m.group(1) if m else front)(
        re.search(r"\*\*Contact:\*\*\s*(.+)", front)
    )
    contact = (lambda m: m.group(0).lower() if m else "")(
        re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", contact_raw)
    )
    # NOTE: `**Gift artifact:**` is deliberately NOT read as an attachment. In this template it
    # specifies the asset touch 1 *offers* and touch 2 delivers — describing it is the rule being
    # followed. Only a literal `Attach:` field (the draft-outreach shape) ships a payload.
    section = _EMAIL_HEADING_RE.split(text, maxsplit=1)
    blocks: list[EmailBlock] = []
    stated = None
    if len(section) == 2:
        after = section[1].split("\n## ", 1)[0]
        m = re.search(
            r"\*\*Subject:\*\*\s*(?P<subject>.+?)\s*\n(?P<body>.*?)(?=\n---|\Z)", after, re.DOTALL
        )
        if m:
            blocks.append(
                EmailBlock(
                    index=1,
                    header=persona.strip(),
                    first=_first_name(persona),
                    company=company,
                    to=contact or company.lower(),
                    subject=m.group("subject").strip(),
                    body=_quoted_body(m.group("body")),
                )
            )
        stated = _stated_words(after)

    declared, enumerated, channels = _parse_sequence(text)
    meta = PackMeta(
        fmt="prospect-pack",
        label=blocks[0].label if blocks else "PACK",
        stated_words=stated,
        declared_touches=declared,
        enumerated_touches=enumerated,
        touch_channels=channels,
        has_linkedin_dm="## LinkedIn DM" in text,
    )
    return version, blocks, meta


def parse_followup(text: str) -> tuple[str, str] | None:
    """(subject, body) of a `## Follow-up` section, when the pack carries one.

    Follow-ups are not linted as first touches — different rules apply (no greeting, it is a
    reply). But they are still text that ships, and the 2026-07-03 batch reused one follow-up
    skeleton across 17 files while every touch-1 body was already differentiated. A batch check
    that reads only touch 1 cannot see that.
    """
    m = re.search(r"##\s*Follow-up[^\n]*\n(.*)", text, re.DOTALL)
    if not m:
        return None
    section = m.group(1)
    sm = re.search(r"\*\*Subject:\*\*\s*(.+?)\s*\n(.*)", section, re.DOTALL)
    if not sm:
        return None
    return sm.group(1).strip(), _strip_meta_lines(_strip_quote(sm.group(2))).strip()


def detect_format(text: str) -> str:
    if re.search(r"^###\s+\d+\.", text, re.MULTILINE):
        return "tier-a-manual"
    if _EMAIL_HEADING_RE.search(text):
        return "prospect-pack"
    return "draft-outreach"


def _norm_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9$%.']+", text.lower())


def _content_words(text: str) -> set[str]:
    return {t for t in _norm_tokens(text) if t not in STOPWORDS and len(t) > 2}


#: A CTA that points back at the body with a pronoun rather than repeating its nouns.
#: Exempt from `cta-omits-gap` — anaphora IS the reference, and demanding a repeated noun
#: instead would push copy toward the clumsier sentence.
CTA_ANAPHORA_RE = re.compile(
    r"\b(that|those|this|these|it|their|there|the same|the above)\b", re.IGNORECASE
)


def _depossess(words: set[str]) -> set[str]:
    """``{"cascade's"}`` -> ``{"cascade"}``. A possessive is the same word."""
    return {w[:-2] if w.endswith("'s") else w.rstrip("'") for w in words}


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

    One implementation on purpose. `cta-question`, `sentence-length` and `question-count` all
    reason about "a sentence", and three inline regexes would eventually disagree about one —
    at which point the CTA rule and the readability rule would be gating different objects while
    reporting the same word.
    """
    flat = re.sub(r"\s+", " ", text).strip()
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", flat) if x.strip()]


def _anchors(body: str) -> set[str]:
    """Digit-bearing tokens + mid-sentence capitalized tokens (proxy for dossier facts)."""
    found: set[str] = set()
    for tok in re.findall(r"[\w$%.']+", body):
        if any(ch.isdigit() for ch in tok):
            found.add(tok)
    for m in re.finditer(r"(?<![.!?]\s)(?<!^)(?<!\n)\b([A-Z][A-Za-z0-9'-]+)", body):
        tok = m.group(1)
        if tok in ANCHOR_STOPLIST:
            continue
        # Short ALLCAPS tokens used to be excluded wholesale, which made three- and
        # four-letter brands invisible as named facts, and failed renders whose only
        # "flaw" was a short brand name (2026-08-19). The stoplist above already carries the
        # acronym junk (AI, IAM, MCP, CEO...) — anything ALLCAPS that survives it is far
        # more likely a brand than noise.
        if tok.isupper() and len(tok) < 2:
            continue
        found.add(tok)
    return found


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


def persona_of(header: str) -> str | None:
    """Which matrix persona this recipient's title names, or None if unrecognised.

    The finer of the two views. Fail-quiet: an unrecognised title says nothing about the
    person, and guessing is how a cost argument reaches a security reviewer. Callers are
    expected to COUNT and NAME the None bucket rather than hide it — "this list has no
    FinOps leads" and "we cannot see the FinOps leads in this list" are different facts
    and only one is good news.

    Fail-quiet is about what this function ASSERTS, not about what the caller may then do.
    A ``None`` here is an unknown seat; a caller choosing what to send such a person should
    route it to :data:`SEGMENT_DEFAULT_PERSONA` rather than honouring whatever persona a
    spec happened to declare, which is how a body written for a CISO reached an SME owner.
    """
    low = (header or "").lower()
    for persona, cues in _PERSONA_RULES:
        if not _matches(cues, low):
            continue
        if _matches(_ANTI_CUES.get(persona, ()), low):
            continue
        return persona
    return None


def non_buyer_of(header: str) -> str | None:
    """The cue that marks this title as someone who cannot act on a governance pitch.

    Deliberately SEPARATE from :func:`persona_of`, and deliberately small. ``persona_of``
    returning None means "we cannot see this seat"; this returning a cue means "we can see
    it, and it is not a buyer" — two facts that look identical in a None bucket and lead to
    opposite actions (resolve a better contact vs. do not contact this company at all).

    Only titles that are unambiguously not a decision-maker for THIS product are listed. A
    role that is a support function at a bank but the owner at a ten-person agency (an
    operations manager, a digital-transformation lead) is NOT here: it stays unrecognised,
    because asserting non-buyer on it would drop real SME buyers. Under-claiming here is
    cheap; over-claiming deletes accounts.

    Found by the 2026-09-06 judge pass, which flagged an Administrative Assistant and an
    Executive Assistant as recipients of a runtime-governance argument. Nothing deterministic
    was checking, because ``lint_persona_lead`` cannot fire on a title it does not recognise.
    """
    low = (header or "").lower()
    for cue in _NON_BUYER_CUES:
        if _cue_re(cue).search(low):
            return cue
    return None


def seat_of(header: str) -> str | None:
    """Which seat's copy this recipient should receive, or None if unrecognised.

    The coarser view, derived from :func:`persona_of` so the two cannot drift. Returns
    None for a persona that has no seat (``finops``, ``partnership``) exactly as it does
    for an unrecognised title — in both cases nothing is owed and nothing is claimed.
    """
    persona = persona_of(header)
    return _PERSONA_TO_SEAT.get(persona) if persona else None


def lint_persona_lead(b: EmailBlock) -> list[Violation]:
    """Fail an email that leads on a different seat's pain than its recipient holds.

    Only fires when the seat is recognised AND the body carries security-seat stakes vocabulary
    AND carries none of its own seat's. An unrecognised title says nothing — silence is the safe
    default, exactly as with the city gazetteer in email_compliance.
    """
    seat = seat_of(b.header)
    if seat is None or seat == "security":
        return []
    low = b.body.lower()
    borrowed = [t for t in _SECURITY_ONLY if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low)]
    if not borrowed:
        return []
    own = next(words for name, _, words in _SEAT_RULES if name == seat)
    if any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low) for w in own):
        return []
    return [
        Violation(
            "ERROR",
            b.label,
            "persona-lead-mismatch",
            f"{seat} seat led on security-seat pain ({', '.join(borrowed)}) with none of its own "
            f"— see voice.md persona-axis table",
        )
    ]


#: An offer whose object sits INSIDE the reader's own system. "I can map where that comes apart
#: across your four channels" offers labour they can do faster themselves, and implies they had
#: not noticed — which is why it reads as presumptuous. The asymmetry we actually hold is
#: cross-market: what other platforms did, what their buyers ask, what the clock is.
_OFFER_INWARD_RE = re.compile(
    r"\b(?:your|the)\s+[a-z-]+(?:\s+[a-z-]+){0,2}\s+you\s+(?:run|have|already|built|use)\b"
    r"|\byour\s+(?:stack|setup|system|platform|flow|pipeline|dashboard|intake|dispatch|install)\b"
    r"|\bon\s+the\s+[a-z-]+\s+you\b",
    re.IGNORECASE,
)


#: The capability taxonomy and the regulators that matter are the TENANT's, so both are loaded
#: from the active profile (`--capability-file` -> `knowledge/capability-argument.toml`) rather
#: than hardcoded here — the same rule that keeps `voice-bans.txt` and `shared-phrases.txt` out
#: of this cross-tenant gate. A profile shipping no file disables these checks rather than
#: guessing at another tenant's vocabulary.
CapabilityRules = dict[str, dict]


def load_capability_rules(path: str | None) -> CapabilityRules:
    """Parse the profile's `capability-argument.toml`. Absent file -> checks disabled."""
    if not path or not Path(path).exists():
        return {}
    import tomllib

    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _anchor_re(rules: CapabilityRules) -> re.Pattern | None:
    pats = (rules.get("_anchors") or {}).get("patterns") or []
    return re.compile("|".join(pats), re.IGNORECASE) if pats else None


#: What the offer DELIVERS: a view of how a class of problem is solved, not an inspection of
#: their build. "what runtime governance looks like for B2B order capture" is a solution
#: overview; "your dispatch path" is an audit.
_SOLUTION_SHAPE_RE = re.compile(
    r"\bwhat\s+[a-z- ]{3,40}\s+looks like\b|\bhow\s+[a-z- ]{3,40}\s+(?:is|are|gets?)\b"
    r"|\b(?:an?|the)\s+(?:approach|model|overview|shape|pattern|blueprint|reference)\b"
    r"|\bruntime governance\b|\bguardrails?\b|\bgovernance model\b",
    re.IGNORECASE,
)


def lint_offer_scope(
    b: EmailBlock, sentences: list[str], rules: CapabilityRules | None = None
) -> list[Violation]:
    """The ask must trade on what we can see across the market, not labour inside their build.

    Added 2026-09-04. The operator's read on a batch that passed every other gate: *"a very
    specific offer on a problem comes across like they don't know how to solve it themself."*
    Structural rather than a wording slip — an offer to go and look inside the reader's own
    system is worth less than an hour of their own engineer, and presumes they had not looked.

    One exemption: naming their system is fine when the offer measures it against a PUBLIC
    anchor the tenant declares. "Map your dispatch path against §2.1.2" is asymmetric knowledge;
    "look at your dispatch path" is unpaid labour.
    """
    if not sentences:
        return []
    last = sentences[-1]
    if not CTA_HELP_OFFER_RE.search(last):
        return []
    m = _OFFER_INWARD_RE.search(last)
    if not m:
        return []
    # An external anchor supports the offer; it does not license auditing their system. The
    # exemption added 2026-09-04 was too wide — it re-admitted "map YOUR dispatch path against
    # §2.1.2", which the operator read (correctly) as offering to fix their thing. What earns a
    # founder's reply is a SOLUTION OVERVIEW for their use case: "what runtime governance looks
    # like for WhatsApp order capture". So the anchor clears the offer only when the offer's own
    # object is an approach, not their artifact.
    anchors = _anchor_re(rules or {})
    if anchors and anchors.search(b.body) and _SOLUTION_SHAPE_RE.search(last):
        return []
    return [
        Violation(
            "ERROR",
            b.label,
            "offer-does-their-work",
            f"the offer points inside their own system ({m.group(0)!r}) — offer what we can see "
            "across the market, or measure their setup against a public anchor. See voice.md "
            "'The offer trades on asymmetry'",
        )
    ]


#: Markers that turn a claim about the reader's build into a claim about a CLASS of company.
#: voice.md rule 9 has said "claim the CATEGORY, not their build" since 2026-09-04 and nothing
#: checked it, so rule 10 (agent as grammatical subject) quietly pulled the copy back to
#: assertion: "The agent holds a credential into their CRM and keeps it after the job closes."
#: An uncalibrated judge independently rejected five of six packs for exactly that.
_GENERALISED_RE = re.compile(
    r"\btypically\b|\busually\b|\btend(?:s)? to\b|\boften\b|\bcommonly\b|\bmost\b"
    r"|\bin that position\b|\bat that scale\b|\bnormally\b|\bgenerally\b|\brarely\b"
    r"|\bfor companies\b|\bplatforms (?:that|in|at)\b"
    # A CONDITIONAL frame generalises as well as an adverb: "when an agent first acts on
    # regulated data" describes a class of moment, not this reader's build. Added 2026-09-05
    # after the rule false-positived on the merge-render suite's own clean fixture, which uses
    # exactly that shape ("Teams we work with hit this when an agent first acts...").
    r"|\bwhen\s+an?\b|\bonce\s+agents?\b|\bthe moment\b|\bteams\s+we\b"
    r"|\bwhere this\b|\bat the point\b|\bif\s+an?\b",
    re.IGNORECASE,
)
#: A flat present-tense assertion about their internals — an agent or a system of theirs DOING
#: something, stated as fact. Paired with the absence of a generalisation marker, this is the
#: "high chance I could be completely off" shape.
_ASSERTS_INTERNALS_RE = re.compile(
    # an agent of theirs, doing something, in the present tense
    r"\b(?:the|that|your|one)?\s*agent\b[^.?!]{0,40}?\b(?:holds|keeps|acts|applies|enforces|"
    r"reaches|crosses|carries|runs|decided|decides|books|commits|writes|dispatches)\b"
    # ...or a system of theirs, asserted
    r"|\byour\s+[a-z-]+\s+(?:is|are|lives|sits|holds|runs|carries)\b"
    # ...or a flat negative about their build ("nothing in that path caps ...")
    r"|\bnothing\b[^.?!]{0,30}?\b(?:scopes|caps|stops|reconciles|prevents|limits)\b"
    r"|\bthe rule\b[^.?!]{0,40}?\bis (?:configured|set)\b",
    re.IGNORECASE,
)


def lint_problem_is_generalised(b: EmailBlock, sentences: list[str]) -> list[Violation]:
    """The problem must be stated as a pattern in a CLASS, never asserted about this reader.

    Added 2026-09-05 on the operator's third statement of the same point: *"never assume they
    have it but say typically for companies that [characteristic] then explain the typical
    problem."* It was written into voice.md as rule 9 twice and did not survive contact with
    rule 10, because nothing failed a body that ignored it.

    The test is cheap and the failure mode is expensive: an assertion about an architecture we
    inferred from a website is wrong at whatever rate our inference is wrong, and it tells the
    reader we guessed. A generalisation is never wrong about them — they place themselves
    against it.
    """
    # Checked PER SENTENCE. A first version scanned the whole body and exempted it if a
    # generalisation marker appeared anywhere — so four of six packs asserting an architecture
    # flat-out were cleared by an unrelated "usually" three sentences away. The marker has to
    # qualify the claim it sits with.
    m = None
    for s in sentences[1:-1] if len(sentences) > 2 else sentences:
        hit = _ASSERTS_INTERNALS_RE.search(s)
        if hit and not _GENERALISED_RE.search(s):
            m = hit
            break
    if not m:
        return []
    return [
        Violation(
            "ERROR",
            b.label,
            "problem-asserts-internals",
            f"states {m.group(0)!r} as fact about this reader with no generalisation marker — "
            "write it as what happens to companies in their position ('platforms at that scale "
            "typically...'), so they place themselves against it. See voice.md rule 9",
        )
    ]


def lint_offer_is_strategic(b: EmailBlock, sentences: list[str]) -> list[Violation]:
    """The offer must deliver a SOLUTION OVERVIEW, not an inspection of their build.

    Added 2026-09-05, the operator's third pass on the same point: *"the offer sounds like you
    are willing to fix the solution, but it's more of giving a solution overview of how to solve
    something strategic like runtime governance for their use case."*

    `offer-does-their-work` was the negative half — it fails an offer POINTING at their system.
    It cannot catch an offer that points nowhere in particular ("compared order capture against
    that"), which reads as an audit without ever saying "your". This is the positive half: name
    what arrives, and let it be an approach a class of company can use, not a report on this one.

    ✅ "what runtime governance looks like for B2B order capture"
    ✅ "the reference shape for agent guardrails on a dispatch flow"
    ❌ "mapped your dispatch path against §2.1.2"
    """
    if not sentences:
        return []
    last = sentences[-1]
    if not CTA_HELP_OFFER_RE.search(last):
        return []
    if _SOLUTION_SHAPE_RE.search(last):
        return []
    return [
        Violation(
            "ERROR",
            b.label,
            "offer-not-a-solution-overview",
            "the offer names no deliverable an exec would want — say what ARRIVES and make it an "
            "approach ('what runtime governance looks like for <their use case>'), not an "
            "inspection of their build. See voice.md rule 11",
        )
    ]


def lint_declared_capability(
    b: EmailBlock, capability: str, rules: CapabilityRules | None = None
) -> list[Violation]:
    """Does the body argue the capability its front block DECLARES?

    Merged 2026-09-04 from two rules that were one idea wearing two names. `hook_coverage`
    checks the SPREAD of declarations across a campaign and never once checks that a body argues
    its own: three of six packs declared identity / protocol-proxy / security-policy and every
    one argued **observability** — "can the buyer check the record afterwards".

    That drift is not cosmetic. Evidence language is about the PAST, and a past-tense problem
    sounds like audit logging, which a spreadsheet solves. The product governs the action at
    runtime. An email that never says what the agent may do, under whose authority, is not about
    agentic governance however well it reads.
    """
    group = (rules or {}).get((capability or "").strip().lower())
    want = tuple((group or {}).get("words") or ())
    if not want:
        return []
    low = b.body.lower()
    own = sum(low.count(w) for w in want)
    if not own:
        return [
            Violation(
                "ERROR",
                b.label,
                "capability-unargued",
                f"declares capability {capability!r} but the body carries none of its vocabulary "
                f"({', '.join(want[:5])}...) — a declaration the copy does not argue",
            )
        ]
    # Presence is not enough: a body can name its own group once and spend the rest of the
    # paragraph on evidence. Observability is a real group and may be declared; it may not be
    # the argument every other group quietly collapses into.
    if (capability or "").strip().lower() == "observability":
        return []
    obs = tuple((rules or {}).get("observability", {}).get("words") or ())
    if not obs:
        return []
    hits = sum(low.count(w) for w in obs)
    if hits <= own:
        return []
    return [
        Violation(
            "ERROR",
            b.label,
            "capability-unargued",
            f"declares {capability!r} but argues observability ({hits} evidence-words vs {own} of "
            "its own) — the record is the past; this product governs the action. Name what the "
            "AGENT may do and under whose authority",
        )
    ]


def lint_seat_stakes(b: EmailBlock) -> list[Violation]:
    """The body must speak in ITS OWN seat's stakes, not at mechanism altitude.

    `persona-lead-mismatch` is fail-open by construction: it only fires when a body BORROWS
    another seat's vocabulary. A body written purely at mechanism level borrows nothing, carries
    nothing, and sails through — which is exactly what happened on 2026-09-04, when six emails to
    technical founders all led on attribution mechanics and passed. voice.md has said since
    2026-07 that "a founder doesn't lose sleep over attribution, they lose sleep over the
    enterprise deal it stalled"; nothing checked it.

    This is the positive half: name the consequence in the seat's own words, or say nothing to
    that seat at all.
    """
    seat = seat_of(b.header)
    if seat is None:
        return []
    own = next((w for name, _, w in _SEAT_RULES if name == seat), ())
    low = b.body.lower()
    if any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low) for w in own):
        return []
    return [
        Violation(
            "ERROR",
            b.label,
            "seat-stakes-missing",
            f"nothing in this body lands at the {seat} seat's altitude (none of: "
            f"{', '.join(sorted(own)[:6])}...) — the problem is stated as a mechanism, which "
            "belongs to no seat. See voice.md persona-axis table",
        )
    ]


def lint_email(
    b: EmailBlock,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Alex",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
    gift_artifacts: tuple[str, ...] = (),
    capability: str = "",
    capability_rules: CapabilityRules | None = None,
) -> list[Violation]:
    v: list[Violation] = []
    low = b.body.lower()
    plain = re.sub(r"\s+", " ", b.body)

    # An unresolved contact suppresses the NAME rules only — never the content rules. Returning
    # early here (the original shape) meant 105 of 390 packs in the 2026-07-19 run received a
    # single warning and skipped every check on the copy itself: word count, CTA, banned words,
    # persona lead, specificity. That is fail-open — the defects were still there, waiting to
    # surface the moment someone filled the name in, which is exactly when nobody re-lints.
    unresolved = b.first == UNRESOLVED_SENTINEL or UNRESOLVED_SENTINEL in b.body
    if unresolved:
        v.append(
            Violation(
                # ERROR since 2026-08-27. The message already said "before this pack can
                # send" and the rule below calls the sentinel the way to keep a pack
                # "blocked from sending" — but as a WARN it exited 0, so a pack still
                # carrying the sentinel in its greeting linted green and read as sendable.
                # A gate whose own text says "blocked" must actually block.
                "ERROR",
                b.label,
                "unresolved-contact",
                f"{UNRESOLVED_SENTINEL} in greeting — resolve the name before this pack can send",
            )
        )

    # Subject
    subj_words = b.subject.split()
    if not (1 <= len(subj_words) <= SUBJECT_MAX_WORDS):
        v.append(
            Violation("ERROR", b.label, "subject-length", f"{len(subj_words)} words: {b.subject!r}")
        )
    if b.subject != b.subject.lower():
        v.append(Violation("ERROR", b.label, "subject-lowercase", repr(b.subject)))
    if MERGE_TAG_RE.search(b.subject) or PLACEHOLDER_RE.search(b.subject):
        v.append(Violation("ERROR", b.label, "subject-placeholder", repr(b.subject)))

    # Greeting / sign-off. The greeting and name-shape rules are the only ones an unresolved
    # contact legitimately suppresses — the sentinel IS the correct greeting until a name lands.
    # A role inbox is the other half of that: not a name we are missing, but a name that does
    # not exist. It takes its own expected greeting rather than a suppression — the rule still
    # asserts an exact opening, so a borrowed name in a role-inbox pack is still an error.
    role_inbox = b.first == ROLE_INBOX_SENTINEL
    if unresolved:
        pass
    elif role_inbox and not b.body.startswith(ROLE_INBOX_GREETING):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "greeting",
                f"role-inbox pack must start {ROLE_INBOX_GREETING!r} — there is no name to greet",
            )
        )
    elif role_inbox:
        pass
    elif not b.body.startswith(f"Hi {b.first},"):
        v.append(Violation("ERROR", b.label, "greeting", f"must start 'Hi {b.first},'"))
    # ...and the name itself must be a NAME. The check above compares the greeting against the
    # persona field it was derived from, so a broken persona validates its own broken greeting:
    # a field of "*name unconfirmed to publish*" yields first="*name" and "Hi *name," passes.
    # Thirteen packs in the 2026-07-19 run were one send away from "Hi *unresolved,". A gate that
    # reads only its own field is not a gate; this is the independent witness.
    elif b.first != UNRESOLVED_SENTINEL and not _NAME_RE.match(b.first):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "greeting-not-a-name",
                f"greeting name {b.first!r} is not a person's name — resolve it, or write "
                f"{UNRESOLVED_SENTINEL} so the pack is blocked from sending",
            )
        )
    lines = [ln.strip() for ln in b.body.splitlines() if ln.strip()]
    # Hoisted above the sign-off check because three rules downstream need it: the CTA rule reads
    # the last sentence, and `sentence-length` / `question-count` read all of them. The sign-off
    # is excluded from every one of them — a bare name is not a sentence, and counting it drags
    # the mean down by ~4 words on a 5-sentence body.
    # The sign-off is a BLOCK, not a line: "Regards" above the name is part of it. Stripping
    # only the name left "Regards" standing as the body's last sentence, so `cta-question`
    # read the valediction as the ask and failed every body that used one.
    _sig = lines[:-1] if lines and lines[-1] == signoff else lines
    if _sig and _sig[-1].rstrip(",").strip().lower() in VALEDICTIONS:
        _sig = _sig[:-1]
    body_wo_signoff = "\n".join(_sig) if lines and lines[-1] == signoff else b.body
    sentences = _sentences(body_wo_signoff)
    if not lines or lines[-1] != signoff:
        v.append(Violation("ERROR", b.label, "sign-off", f"last line must be bare {signoff!r}"))

    # Length
    wc = len(plain.split())
    if not (WORDS_HARD_MIN <= wc <= WORDS_HARD_MAX):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "word-count",
                f"{wc} words (hard {WORDS_HARD_MIN}-{WORDS_HARD_MAX})",
            )
        )
    elif not (WORDS_SOFT_MIN <= wc <= WORDS_SOFT_MAX):
        v.append(
            Violation(
                "WARN",
                b.label,
                "word-count",
                f"{wc} words (target {WORDS_SOFT_MIN}-{WORDS_SOFT_MAX})",
            )
        )

    # Readability. Word count says how long the email is; this says whether it can be SKIMMED,
    # which is the property that actually matters against a ~9-second executive read. Graded like
    # word-count: a hard cap on the worst single sentence, a soft cap on the mean.
    sent_lens = [len(x.split()) for x in sentences]
    if sent_lens:
        longest = max(sent_lens)
        mean_len = sum(sent_lens) / len(sent_lens)
        if longest > SENTENCE_HARD_MAX:
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "sentence-length",
                    f"longest sentence is {longest} words (hard max {SENTENCE_HARD_MAX}) — "
                    f"{longest * 100 // max(wc, 1)}% of the body in one breath; split it",
                )
            )
        elif mean_len > SENTENCE_SOFT_MEAN:
            v.append(
                Violation(
                    "WARN",
                    b.label,
                    "sentence-length",
                    f"mean sentence {mean_len:.1f} words across {len(sent_lens)} "
                    f"(target under {SENTENCE_SOFT_MEAN}); longest {longest}",
                )
            )

    # Mechanical hygiene
    if URL_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "no-links", "first touch is link-free"))
    if EM_DASH in b.body:
        v.append(Violation("ERROR", b.label, "em-dash", "em dash present"))
    if SPINTAX_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "spintax", SPINTAX_RE.search(b.body).group(0)))
    if PLACEHOLDER_RE.search(b.body) or MERGE_TAG_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "placeholder", "unresolved placeholder/merge tag"))

    # Voice bans
    for ban in BANNED_WORDS + tuple(x.lower() for x in extra_bans):
        if ban and re.search(r"(?<!\w)" + re.escape(ban) + r"(?!\w)", low):
            v.append(Violation("ERROR", b.label, "banned-word", ban))
    if LEVERAGE_RE.search(b.body):
        v.append(Violation("ERROR", b.label, "banned-word", "leverage (verb)"))
    for rx in ANTITHESIS_RES:
        if rx.search(plain):
            v.append(Violation("ERROR", b.label, "antithesis", rx.pattern))

    # Mail-merge stems
    for stem in banned_stems:
        if stem in low:
            v.append(Violation("ERROR", b.label, "banned-stem", stem))

    # Named case studies (proof by company type, never the logo)
    for name in case_studies:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", low):
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "named-case-study",
                    f"{name}: name the company type + outcome, not the case-study logo",
                )
            )

    # Hedge.
    #
    # Matched against whitespace-COLLAPSED text, not `low`. A spec wraps its bodies at ~90
    # chars for authoring, and that wrap does not survive staging — the sequencer stores one
    # unbroken line per paragraph, which is why `build_eval_sheet` de-wraps before showing a
    # body to a labeler. Substring-matching the wrapped form means a cue is found or missed
    # according to where the author happened to break the line: on 2026-08-29 the live
    # admission spec's touch 3 carried the sanctioned cue "if that is wrong" and failed
    # `hedge-missing`, purely because it rendered as "If that is\nwrong for {{Company}}".
    # False ERRORs on correctly-hedged copy are how a gate loses its authority.
    flat = " ".join(low.split())
    if not any(c in flat for c in HEDGE_CUES):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "hedge-missing",
                'gap must be hedged (e.g. "my hunch, tell me if you\'ve got this covered")',
            )
        )

    # Credit that grades rather than observes.
    #
    # Scoped to the opener, where the credit beat lives — see CREDIT_VERDICT_RE.
    opener = " ".join(sentences[:CREDIT_OPENER_SENTENCES])
    cm = CREDIT_VERDICT_RE.search(opener)
    if cm:
        v.append(
            Violation(
                "ERROR",
                b.label,
                "credit-is-verdict",
                f"opener grades their decision ({cm.group(0)!r}) — credit is an observation "
                f"of what the move signals, never a verdict; you do not know them well "
                f"enough to grade it, and the credit clause is optional (voice.md)",
            )
        )

    # CTA
    last = sentences[-1] if sentences else ""
    # An offer can be a statement. "Happy to take a look at X, if useful." concedes more than
    # "Want me to look at X?" — it asks nothing and leaves silence costless — and it is the
    # shape the sender actually uses. Requiring a question mark forced every body into the
    # interrogative-offer frame that ran in 6 of 6 drafts he rejected.
    if not last.endswith("?") and not CTA_HELP_OFFER_RE.search(last):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "cta-question",
                f"last sentence must be the offer question: {last!r}",
            )
        )
    # ...and it must be the ONLY question, near enough. `cta-question` checks the last sentence is
    # a question; nothing checked how many others were. Extra questions read as thoroughness and
    # behave like `cta-bundled` — the reader has to pick one, so they answer none.
    n_questions = sum(1 for x in sentences if x.endswith("?"))
    if n_questions > QUESTIONS_MAX:
        v.append(
            Violation(
                "ERROR",
                b.label,
                "question-count",
                f"{n_questions} questions (max {QUESTIONS_MAX}) — the ask has to be the only "
                f"thing they must decide",
            )
        )
    # The ask may only offer an artifact this profile can actually produce inside the reply window.
    # Replaces a keyword ban on the single word "teardown", which failed both ways: it rejected
    # "the one-page teardown of how that clears the review" (specific, producible) while waving
    # through "the crosswalk" and any other artifact nobody had committed to making.
    # Checked as "does the offer name something on the list", not "does it name something off
    # it" — an unlisted artifact is usually a noun the linter has never heard of ("the benchmark
    # report"), so enumerating bad names can never work. A pure interest CTA with no offer verb
    # ("is this on your radar this quarter?") is legitimate and skipped.
    # A help offer is exempt only when it names NO artifact at all: "should I send the
    # write-up on how they did it?" is phrased like an offer of work and is still an
    # offer of an asset, so it stays on the artifact list's hook.
    _is_help_offer = CTA_HELP_OFFER_RE.search(last) and _artifact_count(last) == 0
    if gift_artifacts and _OFFER_RE.search(last) and not _is_help_offer:
        if not any(
            re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", last, re.IGNORECASE)
            for a in gift_artifacts
        ):
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "cta-unstaged-artifact",
                    f"offer names no artifact this profile can produce: {last!r} "
                    f"— add it to gift-artifacts.txt if it is producible, or offer one that is",
                )
            )
    for t, pat in _TIME_ASK_RE:
        if pat.search(low):
            v.append(Violation("ERROR", b.label, "time-ask", t))

    # One CTA means one ARTIFACT — "the one-pager and a short recorded demo" is two asks in
    # one question, and a second ask measurably cuts replies (voice.md "One CTA only").
    if _artifact_count(last) > 1:
        v.append(
            Violation(
                "ERROR",
                b.label,
                "cta-bundled",
                f"CTA offers more than one artifact — pick one: {last!r}",
            )
        )

    # The ask must carry the outcome, not just name the artifact. "Want the one-pager?" makes the
    # reader reconstruct the value from the paragraph above; "the one-pager on containing the
    # agent, not just tuning it" is the whole ask in one clause. Promoted from WARN 2026-08-17
    # after 9 of 30 re-drafted CTAs came back as bare artifact names.
    # Separator-tolerant since 2026-08-19: "write up", "one pager" and "the two page version"
    # sailed past the hyphen-only pattern, so the rule never once evaluated a shipped CTA.
    _CTA_NOUN = (
        r"\b(one[-\s]?pager|write[-\s]?up|demo|walkthrough|teardown|crosswalk|read"
        r"|note|deck|version)\b"
    )
    if re.search(
        _CTA_NOUN,
        last,
        re.IGNORECASE,
    ):
        tail = re.sub(
            r"^.*?" + _CTA_NOUN,
            "",
            last,
            flags=re.IGNORECASE,
        )
        # A content clause is prose after the artifact noun ("on X", "of how Z did Y").
        if len(_norm_tokens(tail)) < 3 and not _anchors(last):
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "cta-unanchored",
                    f"ask names the artifact but not what is in it: {last!r}",
                )
            )

    # The ask must be about the gap this email just named.
    #
    # `cta-unanchored` asks whether the ask has a content clause AT ALL; this asks whether
    # that clause is about THIS email. The two failures look identical in a report and are
    # different defects: "want the one-pager?" is unanchored, while "want the one-pager on
    # agent identity?" under a body arguing audit evidence is anchored to the wrong thing —
    # a house CTA that survived the account it was written for. Four of the five packs
    # reviewed 2026-09-04 passed `cta-unanchored` and read as boilerplate for this reason.
    #
    # Two exemptions, both learned from a false positive on the merge-render fixture, and
    # both required or the rule convicts correct copy:
    #
    #   * ANAPHORA. "Want that mapped to {{Company}}'s stack?" refers back with a pronoun
    #     rather than a noun, which is good writing, not a disconnected ask. A CTA carrying
    #     a back-reference has pointed at the body by construction.
    #   * POSSESSIVES. `_content_words` keeps the clitic, so "cascade's" and "cascade" are
    #     two tokens — the shared word most likely to appear in a CTA is the company name,
    #     and it is the one most likely to be possessive there.
    #
    # The test is deliberately weak — ONE shared content word — because it must never
    # convict a correct ask that paraphrases. It is a floor against a CTA that shares no
    # vocabulary at all with the body above it, not a similarity score.
    if len(sentences) > 1 and not CTA_ANAPHORA_RE.search(last):
        body_words = _depossess(_content_words(" ".join(sentences[:-1])))
        cta_words = _depossess(_content_words(last))
        if body_words and cta_words and not (body_words & cta_words):
            v.append(
                Violation(
                    "ERROR",
                    b.label,
                    "cta-omits-gap",
                    f"ask shares no vocabulary with the problem this email named and "
                    f"carries no back-reference — it would read the same under any "
                    f"body: {last!r}",
                )
            )

    if CTA_OVERCLAIM_RE.search(last):
        v.append(
            Violation(
                "ERROR",
                b.label,
                "cta-overclaim",
                f"ask promises a result in THEIR review/audit environment — describe what the "
                f"artifact contains, not what it will achieve for them: {last!r}",
            )
        )

    # Persona lead pain: the seat sets what the email leads on (voice.md persona-axis table).
    v.extend(lint_persona_lead(b))
    v.extend(lint_seat_stakes(b))
    v.extend(lint_declared_capability(b, capability, capability_rules))
    v.extend(lint_offer_scope(b, sentences, capability_rules))
    v.extend(lint_problem_is_generalised(b, sentences))
    v.extend(lint_offer_is_strategic(b, sentences))

    # Specificity anchors (dossier-fact proxy)
    anchors = _anchors(b.body)
    if len(anchors) < MIN_ANCHORS:
        v.append(
            Violation(
                "ERROR",
                b.label,
                "specificity",
                f"only {len(anchors)} anchor(s) {sorted(anchors)}; need >= {MIN_ANCHORS} dated/named facts",
            )
        )
    elif len(anchors) < SOFT_ANCHORS:
        v.append(
            Violation(
                "WARN", b.label, "specificity", f"{len(anchors)} anchors; aim >= {SOFT_ANCHORS}"
            )
        )

    return v


_ATTACH_IN_BODY_RE = re.compile(r"\battach(?:ed|ment|ing)\b", re.IGNORECASE)
# "Attach: none — the demo is offered on reply" is the rule being followed, not broken.
_ATTACH_NONE_RE = re.compile(r"^\s*(none|n/?a|—|-)\b", re.IGNORECASE)


def lint_meta(meta: PackMeta, blocks: list[EmailBlock]) -> list[Violation]:
    """Rules about the pack around the email — invisible to any body-level check."""
    v: list[Violation] = []
    body = blocks[0].body if blocks else ""

    # The gift is the OFFER, not the payload: an attachment on a cold first touch degrades the
    # spam profile before a relationship exists, and enterprise filters quarantine it outright.
    if meta.attach and not _ATTACH_NONE_RE.match(meta.attach):
        v.append(
            Violation(
                "ERROR",
                meta.label,
                "attachment-on-touch1",
                f"touch 1 must describe the gift and gate it behind the reply, not ship it: {meta.attach!r}",
            )
        )
    if _ATTACH_IN_BODY_RE.search(body):
        v.append(
            Violation(
                "ERROR",
                meta.label,
                "attachment-on-touch1",
                'body says the asset is attached — offer it instead ("want the one-pager?")',
            )
        )

    # A self-reported count that disagrees with reality is worse than none: it reads as verified.
    if meta.stated_words is not None and blocks:
        actual = len(re.sub(r"\s+", " ", body).split())
        if abs(meta.stated_words - actual) > WORD_COUNT_TOLERANCE:
            v.append(
                Violation(
                    "ERROR",
                    meta.label,
                    "word-count-mismatch",
                    f"claims {meta.stated_words} words, body is {actual} — compute it, never estimate",
                )
            )

    if meta.declared_touches is not None:
        if meta.declared_touches > MAX_TOUCHES:
            v.append(
                Violation(
                    "ERROR",
                    meta.label,
                    "touch-count",
                    f"{meta.declared_touches} touches declared; the ladder caps at {MAX_TOUCHES}",
                )
            )
        if meta.enumerated_touches and meta.enumerated_touches != meta.declared_touches:
            v.append(
                Violation(
                    "ERROR",
                    meta.label,
                    "touch-count",
                    f"header declares {meta.declared_touches} touches, {meta.enumerated_touches} are written out",
                )
            )

    # LinkedIn first, then email: two cold emails followed by a connection request reads as
    # pressure, where a visit before the email reads as genuine.
    if meta.has_linkedin_dm and meta.touch_channels:
        if meta.touch_channels[0].lower().startswith("email"):
            # A role inbox has no profile to connect to, so "lead with LinkedIn" is advice the
            # pack cannot take — the DM is the part that does not belong, not the ladder. Same
            # rule, same severity: exempting the case would have left the pack carrying a
            # section nobody can send, reported by nothing.
            role_inbox = bool(blocks) and blocks[0].first == ROLE_INBOX_SENTINEL
            detail = (
                "a role inbox has no LinkedIn profile to DM — drop the LinkedIn DM section "
                "rather than reorder the ladder"
                if role_inbox
                else "touch 1 is email while a LinkedIn DM exists — lead with LinkedIn, then email"
            )
            v.append(Violation("WARN", meta.label, "channel-order", detail))
    return v


def _subject_shape(subject: str) -> str:
    """Collapse a subject to its reusable template, so `X's missing primitive` groups with `Y's`."""
    s = subject.strip().lower()
    s = re.sub(r"^\S+(?:'s|s')\s+", "", s)  # drop a leading possessive noun
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    s = re.sub(r"^(?:\S+\s+){0,2}?\S+(?:'s|s')\s+", "", s)  # multi-word possessive
    return re.sub(r"\s+", " ", s).strip()


def lint_subject_homogeneity(
    subjects: list[tuple[str, str]], ceiling: int = MAX_NGRAM_EMAILS
) -> list[Violation]:
    """Flag a subject template reused across more than `ceiling` files in a batch.

    `subjects` is (label, subject). This is the batch-level analogue of `template-share`: every
    other rule here is per-email, which is structurally blind to 25 individually-compliant
    subjects that happen to be the same mad-lib.
    """
    shapes: dict[str, list[str]] = {}
    for label, subject in subjects:
        shape = _subject_shape(subject)
        if shape:
            shapes.setdefault(shape, []).append(label)
    v: list[Violation] = []
    for shape, labels in sorted(shapes.items(), key=lambda kv: -len(kv[1])):
        if len(labels) > ceiling:
            v.append(
                Violation(
                    "ERROR",
                    "BATCH",
                    "subject-template-share",
                    f'"{shape}" is the subject shape in {len(labels)} files (max {ceiling}): '
                    f"{', '.join(sorted(labels)[:5])}{' …' if len(labels) > 5 else ''}",
                )
            )
    return v


def lint_body_homogeneity(
    bodies: list[tuple[str, str]],
    ceiling: int = MAX_NGRAM_EMAILS,
    shared_phrases: tuple[str, ...] = (),
) -> list[Violation]:
    """Flag a body phrase shared by more than `ceiling` files in a batch.

    The same 6-gram ceiling `lint_pack` applies *within* one multi-recipient pack, applied
    *across* the files of one run. Note the hedge whitelist is deliberately NOT used here: the
    2026-07-19 run opened 46 of 50 gaps with the same hedge wording, and whitelisting it is
    exactly what let that through. Hedging is mandatory; one scripted wording is not.

    `shared_phrases` is the ONE deliberate exemption, and it exists because this rule polices
    *personalisation*, not vocabulary. A batch describes one product and cites the same reference
    customers; "acme gateway gives each agent a verifiable identity" recurring across
    390 accounts is the pitch being consistent, not a template being pasted. Forcing 390 distinct
    phrasings of one product sentence makes the copy worse, not less robotic. Loaded per-profile
    (`--shared-phrase-file`) rather than hardcoded, so no tenant's product vocabulary lives in
    this cross-tenant linter.

    The exemption is deliberately narrow: a 6-gram is skipped only when it falls ENTIRELY inside
    a declared phrase. A template that merely quotes a product name in passing still trips, because
    its surrounding words are not in the phrase.
    """
    exempt: set[tuple[str, ...]] = set()
    for phrase in shared_phrases:
        toks = _norm_tokens(phrase)
        if len(toks) >= NGRAM_N:
            exempt |= _ngrams(toks, NGRAM_N)

    ngram_files: dict[tuple[str, ...], set[str]] = {}
    for label, body in bodies:
        for g in _ngrams(_norm_tokens(body), NGRAM_N):
            if g in exempt:
                continue
            ngram_files.setdefault(g, set()).add(label)
    flagged = {g: f for g, f in ngram_files.items() if len(f) > ceiling}
    v: list[Violation] = []
    for g in sorted(flagged, key=lambda g: -len(flagged[g]))[:5]:
        v.append(
            Violation(
                "ERROR",
                "BATCH",
                "body-template-share",
                f'"{" ".join(g)}" appears in {len(flagged[g])} files (max {ceiling})',
            )
        )
    return v


# --------------------------------------------------------------------------- hedge stem
#
# Hedging is MANDATORY per email (`hedge-missing`) and that is not in question. What this
# rule polices is the STEM: one scripted construction reused as the frame of every touch.
#
# Measured 2026-08-19 across the four Run-500 seat specs: 9 of 12 authored emails opened
# their gap with "My <read|hunch|bet>, and <hedge clause>:" — the same sentence shape three
# times per sequence with one word swapped. Rotating read -> hunch -> bet does not disguise
# the frame, it advertises it: a recipient who reads all three sees the machine. Nothing
# else catches this. `lint_email` sees one email at a time and is satisfied by any hedge;
# `lint_body_homogeneity` compares FILES across a run, not touches within one sequence.
#
# One per sequence is the cap, not zero: the construction is good copy the first time.
HEDGE_STEM_RE = re.compile(
    r"\bmy\s+(?:read|hunch|bet|guess|sense)\b[^.?!]{0,60}?:",
    re.IGNORECASE,
)
MAX_HEDGE_STEM = 1


def lint_hedge_stem(
    bodies: list[tuple[str, str]], ceiling: int = MAX_HEDGE_STEM
) -> list[Violation]:
    """Flag the same hedge CONSTRUCTION framing more than `ceiling` touches in a sequence.

    `bodies` is (label, body) per touch, in send order. Reported once for the sequence: the
    fix is to rewrite the surplus touches, not to annotate each one.
    """
    hits = [label for label, body in bodies if HEDGE_STEM_RE.search(body)]
    if len(hits) <= ceiling:
        return []
    return [
        Violation(
            "ERROR",
            "SPEC",
            "hedge-stem-repeat",
            f'"My read/hunch/bet, ...:" frames {len(hits)} touches ({", ".join(hits)}); '
            f"max {ceiling} per sequence — vary the construction, not just the noun",
        )
    ]


def lint_pack(
    text: str,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Alex",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
    gift_artifacts: tuple[str, ...] = (),
) -> list[Violation]:
    v: list[Violation] = []
    version, blocks = parse_pack(text)

    # Staleness gate
    if version is None:
        v.append(
            Violation(
                "ERROR",
                "PACK",
                "rules-version-missing",
                f"add 'Rules-Version: {RULES_VERSION}' to the pack header",
            )
        )
    elif version != RULES_VERSION:
        v.append(
            Violation(
                "ERROR",
                "PACK",
                "rules-version-stale",
                f"pack drafted under {version}, current is {RULES_VERSION} — regenerate/re-review before sending",
            )
        )

    if not blocks:
        v.append(
            Violation("ERROR", "PACK", "parse", "no email blocks found (### N. header format)")
        )
        return v

    # Per-email
    for b in blocks:
        v.extend(
            lint_email(
                b,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
                gift_artifacts=gift_artifacts,
            )
        )

    # Duplicate recipients
    seen: dict[str, int] = {}
    for b in blocks:
        if b.to in seen:
            v.append(Violation("ERROR", b.label, "duplicate-to", f"also block #{seen[b.to]}"))
        seen[b.to] = b.index

    # Template-share ceiling (6-grams across emails)
    wl = _hedge_ngram_whitelist()
    ngram_emails: dict[tuple[str, ...], set[int]] = {}
    for b in blocks:
        toks = _norm_tokens(b.body)
        for g in _ngrams(toks, NGRAM_N):
            if g in wl:
                continue
            ngram_emails.setdefault(g, set()).add(b.index)
    flagged: set[tuple[str, ...]] = {
        g for g, e in ngram_emails.items() if len(e) > MAX_NGRAM_EMAILS
    }
    # Report the longest offenders only (merge overlapping grams by picking top few)
    if flagged:
        samples = sorted(flagged, key=lambda g: -len(ngram_emails[g]))[:5]
        for g in samples:
            v.append(
                Violation(
                    "ERROR",
                    "PACK",
                    "template-share",
                    f'"{" ".join(g)}" appears in {len(ngram_emails[g])} emails (max {MAX_NGRAM_EMAILS})',
                )
            )

    # Same-company divergence
    by_domain: dict[str, list[EmailBlock]] = {}
    for b in blocks:
        dom = b.to.split("@")[-1]
        by_domain.setdefault(dom, []).append(b)
    for dom, group in by_domain.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, c = group[i], group[j]
                if a.subject.strip().lower() == c.subject.strip().lower():
                    v.append(
                        Violation(
                            "ERROR",
                            "PACK",
                            "same-company-subject",
                            f"{a.label} and {c.label} share subject {a.subject!r}",
                        )
                    )
                wa, wc_ = _content_words(a.body), _content_words(c.body)
                if wa and wc_:
                    jac = len(wa & wc_) / len(wa | wc_)
                    if jac > JACCARD_MAX:
                        v.append(
                            Violation(
                                "ERROR",
                                "PACK",
                                "same-company-overlap",
                                f"{a.label} vs {c.label}: Jaccard {jac:.2f} > {JACCARD_MAX} — diverge angle/signal",
                            )
                        )
    return v


_PARSERS = {
    "draft-outreach": parse_draft_outreach_pack,
    "prospect-pack": parse_prospect_pack,
}


def lint_formatted_pack(
    text: str,
    fmt: str,
    extra_bans: tuple[str, ...] = (),
    signoff: str = "Alex",
    case_studies: tuple[str, ...] = DEFAULT_CASE_STUDY_NAMES,
    banned_stems: tuple[str, ...] = DEFAULT_BANNED_STEMS,
    gift_artifacts: tuple[str, ...] = (),
    capability_rules: CapabilityRules | None = None,
) -> tuple[list[Violation], list[EmailBlock]]:
    """Lint one pack in any of the three known shapes. Returns (violations, blocks).

    The blocks come back so a batch run can pool subjects for `lint_subject_homogeneity`.
    """
    if fmt == "auto":
        fmt = detect_format(text)
    if fmt == "tier-a-manual":
        _, blocks = parse_pack(text)
        return (
            lint_pack(
                text,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
                gift_artifacts=gift_artifacts,
            ),
            blocks,
        )

    version, blocks, meta = _PARSERS[fmt](text)
    v: list[Violation] = []
    # The declared capability lives in the pack header, outside any single email, so it is read
    # here rather than in `lint_email` — same place `PackMeta`'s other outside-the-body fields
    # are handled. `hook_coverage` reads this same field for the campaign-wide spread; this is
    # the per-body half it never had.
    _cap_m = re.search(r"^\**[Cc]apability\**:\**\s*(?P<value>[a-z-]+)", text, re.MULTILINE)
    _capability = _cap_m.group("value") if _cap_m else ""
    if version is None:
        v.append(
            Violation(
                "WARN",
                meta.label,
                "rules-version-missing",
                f"add 'Rules-Version: {RULES_VERSION}' to the pack header",
            )
        )
    elif version != RULES_VERSION:
        v.append(
            Violation(
                "ERROR",
                meta.label,
                "rules-version-stale",
                f"drafted under {version}, current is {RULES_VERSION} — regenerate before sending",
            )
        )
    if not blocks:
        # A contact-resolution addendum (`-b.md`) carries no email at all — it exists to record a
        # resolved persona for a pack that lives in another file. That is not a malformed pack and
        # must not read as one. A document that DOES declare a touch-1 section but yields no block
        # is still a hard error: that is the linter failing to read copy that will actually ship.
        has_email_section = bool(_EMAIL_HEADING_RE.search(text)) or "**Subject:**" in text
        if fmt == "prospect-pack" and not has_email_section:
            v.append(
                Violation(
                    "WARN",
                    meta.label,
                    "not-an-outreach-pack",
                    "no touch-1 email section — treated as a contact-resolution addendum, not linted",
                )
            )
        else:
            v.append(
                Violation("ERROR", meta.label, "parse", f"no touch-1 email found ({fmt} format)")
            )
        return v, blocks

    for b in blocks:
        v.extend(
            lint_email(
                b,
                extra_bans=extra_bans,
                signoff=signoff,
                case_studies=case_studies,
                banned_stems=banned_stems,
                gift_artifacts=gift_artifacts,
                capability=_capability,
                capability_rules=capability_rules,
            )
        )
    v.extend(lint_meta(meta, blocks))
    return v, blocks


def lint_tracker_csv(path: Path) -> list[Violation]:
    """Staleness check for a tracker CSV: DRAFTED rows must carry the current rules_version."""
    v: list[Violation] = []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    if not rows:
        return [Violation("WARN", "CSV", "empty", str(path))]
    has_col = "rules_version" in rows[0]
    for i, r in enumerate(rows, start=2):
        status = (r.get("status") or "").upper()
        if status.startswith("DRAFTED"):
            rv = (r.get("rules_version") or "").strip() if has_col else ""
            if rv != RULES_VERSION:
                v.append(
                    Violation(
                        "ERROR",
                        f"csv row {i} ({r.get('email', '?')})",
                        "rules-version-stale",
                        f"drafted under {rv or 'unversioned'}; re-validate under {RULES_VERSION} before sending",
                    )
                )
    return v


def _load_bans(path: str | None) -> tuple[str, ...]:
    if not path:
        return ()
    p = Path(path)
    if not p.exists():
        return ()
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.lower())
    return tuple(out)


_GOOD = """Rules-Version: 2026-09-04

### 1. Dana Rivera · Head of Platform, Acme Robotics
**To:** dana@acmerobotics.dev
**Subject:** agent audit trail

Hi Dana,

Shipping autonomous agents into production right after the Series B puts 12 agents calling internal tools directly, with every run logged. My bet on the open piece: those logs prove what ran, not which agent held the credential once one hands off to another. EU AI Act Article 12 lands that on your customers by 2027, and today each of them rebuilds the answer per deployment. The primitives for that are already standardised, so it lands as configuration rather than a build. Want the one-pager on per-agent identity at the tool boundary?

Alex

---
"""

# Fixture tenant lists the selftest passes explicitly (the shipped defaults are empty).
_SELFTEST_CASE_STUDIES = ("exampleco",)
_SELFTEST_STEMS = ("service account with no per-call proof",)

_BAD = """Rules-Version: 2026-07-01

### 1. Jane Doe · CEO, Acme
**To:** jane@acme.com
**Subject:** Quick Question About Your Platform Strategy

Hey Jane,

I hope this finds you well. Your agents share a service account with no per-call proof, so no one can tell which agent acted. ExampleCo hit the same wall last year. Worth me sending the teardown?

Best,
Jane's Friend

---
"""


def _selftest() -> int:
    good = lint_pack(_GOOD)
    good_errors = [x for x in good if x.level == "ERROR"]
    bad = lint_pack(_BAD, case_studies=_SELFTEST_CASE_STUDIES, banned_stems=_SELFTEST_STEMS)
    bad_rules = {x.rule for x in bad}
    expect = {
        "rules-version-stale",
        "subject-length",
        "greeting",
        "sign-off",
        "banned-word",
        "banned-stem",
        "named-case-study",
        "hedge-missing",
    }
    ok = not good_errors and expect.issubset(bad_rules)
    print(
        f"selftest: good-pack errors={len(good_errors)} (want 0); bad-pack rules hit={sorted(bad_rules)}"
    )
    if not ok:
        for x in good_errors:
            print("  unexpected:", x)
        print("  missing:", expect - bad_rules)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=f"Outreach pack linter (rules {RULES_VERSION})")
    ap.add_argument("pack", nargs="?", help="path to the pack .md to lint")
    ap.add_argument("--csv", help="tracker CSV to staleness-check (rules_version column)")
    ap.add_argument("--ban-file", help="profile voice-bans.txt for extra banned words")
    ap.add_argument(
        "--capability-file",
        help="profile capability-argument.toml — what each capability group argues, and the "
        "public anchors an offer may point at. Omitted: both checks no-op.",
    )
    ap.add_argument(
        "--case-study-file",
        help="profile file of case-study company names to flag (one per line)",
    )
    ap.add_argument(
        "--stem-file",
        help="profile file of banned mail-merge stems (one per line)",
    )
    ap.add_argument(
        "--artifact-file",
        help="profile file of gift artifacts a CTA may offer (one per line); missing = check off",
    )
    ap.add_argument(
        "--shared-phrase-file",
        help="profile file of product/case-study phrases exempt from body-template-share "
        "(one per line) — the pitch is meant to be consistent; only personalisation must vary",
    )
    ap.add_argument(
        "--signoff", default="Alex", help="expected bare sign-off line (pass your real name)"
    )
    ap.add_argument(
        "--ack",
        action="append",
        default=[],
        metavar="RULE",
        help="acknowledge a WARN class by rule name; repeatable. An acknowledged class "
        "stops counting against the budget.",
    )
    ap.add_argument(
        "--budget",
        type=int,
        default=WARN_BUDGET,
        help=f"unacknowledged WARN classes tolerated before this blocks (default {WARN_BUDGET})",
    )
    ap.add_argument(
        "--format",
        default="auto",
        choices=["auto", "tier-a-manual", "draft-outreach", "prospect-pack"],
        help="pack shape (auto-detected by default)",
    )
    ap.add_argument(
        "--batch",
        nargs="+",
        metavar="PATH_OR_GLOB",
        help="packs to lint together (adds the cross-file subject/body template checks). "
        "Accepts globs and/or explicit paths; shell-expanded lists work too.",
    )
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.pack and not args.csv and not args.batch:
        ap.error("pack path, --batch or --csv required")

    kw = {
        "extra_bans": _load_bans(args.ban_file),
        "signoff": args.signoff,
        "case_studies": _load_bans(args.case_study_file),
        "banned_stems": _load_bans(args.stem_file),
        "gift_artifacts": _load_bans(args.artifact_file),
        "capability_rules": load_capability_rules(args.capability_file),
    }
    violations: list[Violation] = []
    if args.pack:
        violations += lint_formatted_pack(
            Path(args.pack).read_text(encoding="utf-8"), args.format, **kw
        )[0]
    if args.batch:
        # Each entry is a literal path or a glob. Python's glob has no brace expansion, so an
        # unexpanded "{a,b}" would silently match nothing — resolve explicit paths directly.
        seen: dict[str, Path] = {}
        for pat in args.batch:
            p = Path(pat)
            for hit in [p] if p.is_file() else sorted(Path().glob(pat)):
                seen[str(hit)] = hit
        paths = [seen[k] for k in sorted(seen)]
        if not paths:
            print(f"no files matched {args.batch!r} (brace globs are not expanded — pass paths)")
            return 1
        subjects: list[tuple[str, str]] = []
        bodies: list[tuple[str, str]] = []
        fu_subjects: list[tuple[str, str]] = []
        fu_bodies: list[tuple[str, str]] = []
        for p in paths:
            vs, blocks = lint_formatted_pack(p.read_text(encoding="utf-8"), args.format, **kw)
            violations += [Violation(x.level, f"{p.name}:{x.email}", x.rule, x.detail) for x in vs]
            subjects += [(p.name, b.subject) for b in blocks]
            bodies += [(p.name, b.body) for b in blocks]
            fu = parse_followup(p.read_text(encoding="utf-8"))
            if fu:
                fu_subjects.append((p.name, fu[0]))
                fu_bodies.append((p.name, fu[1]))
        shared = _load_bans(args.shared_phrase_file)
        violations += lint_subject_homogeneity(subjects)
        violations += lint_body_homogeneity(bodies, shared_phrases=shared)
        violations += [
            Violation(v.level, "BATCH-followup", v.rule, v.detail)
            for v in lint_subject_homogeneity(fu_subjects)
            + lint_body_homogeneity(fu_bodies, shared_phrases=shared)
        ]
        print(f"linted {len(paths)} pack(s) from {args.batch!r}\n")
    if args.csv:
        violations += lint_tracker_csv(Path(args.csv))

    errors = [x for x in violations if x.level == "ERROR"]
    warns = [x for x in violations if x.level != "ERROR"]

    # Errors always enumerate: they block, so every one has to be actionable.
    for x in errors:
        print(x)

    # Warnings are budgeted. Past the budget this prints rates and exemplars instead of a
    # wall, and BLOCKS — because a wall of warnings is read as "noisy but fine" and the
    # findings that mattered go out with the batch. 388 of them once did exactly that.
    verdict = budget_verdict(
        [f"{x.rule}: {x.detail}" for x in warns],
        denominator=max(len(warns), 1),
        acked=tuple(args.ack),
        budget=args.budget,
    )
    if verdict.enumerable:
        for x in warns:
            print(x)
    else:
        print(render_budget(verdict, unit="warning"))

    print(f"\n{len(errors)} error(s), {len(warns)} warning(s) — rules {RULES_VERSION}")
    return 1 if (errors or verdict.blocked) else 0


if __name__ == "__main__":
    sys.exit(main())
