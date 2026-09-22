"""Classify one inbound reply into a signal type — deterministically, no model.

:mod:`gtm_core.signals` maps ten ``signal_type`` values to a suggested action, and
``agent.signal_dispatch`` gives every one of those actions a target. Between those two
facts sat a single producer: :mod:`agent.optout_sweep` recorded *every* non-opt-out reply
as ``reply_received``. So a pricing question and a "sure, Tuesday works" arrived as the
same event, took the same route, and produced the same timer-drafted reply.

This module is the missing classifier. It is the same shape as
:mod:`gtm_core.optout_watch`: a keyword match over the message text, biased toward
**recall**, never a model's judgment on untrusted inbound text (RULES.md §R5). The bias is
affordable for exactly the reason it is affordable there — **every branch ends at a human**.
``escalate_to_operator`` wakes the operator on Telegram; ``propose_booking_link`` and
``draft_reply`` both end at ``⟦GATE:reply⟧``. A false positive costs one glance; a miss
costs a warm reply answered four hours late, or a pricing question answered by a timer.

**Exactly one type per thread.** The classification is a total function returning a single
value, not a set of tags. Two signals for one thread would dispatch the same pack twice on
the same reply — ``signal_id`` keys on ``(who, type, source)``, so differing types do NOT
dedup against each other. Precedence is therefore load-bearing, and it is ordered by what
is worst to get wrong rather than by what is most specific:

    pricing_question > buyer_intent > not_now > meeting_request > reply_received

A commercial question auto-answered on a timer is the worst outcome available here, so it
sorts first and escalates. A meeting request carrying buying language escalates too — the
operator still sees the meeting, and nothing is lost by waking them. A plain "let's meet
Tuesday" carries no buying language, stays ``meeting_request``, and is drafted with the
booking link that ``inbound-triage`` already knows to offer.

Reading the body is deliberate and already the sanctioned pattern:
``optout_watch.is_optout`` matches over message text too. Matching a regex over untrusted
text is reasoning over data; §R5 forbids *following instructions* found there, which no
branch of this module does. Nothing here fetches, sends, books, or writes.

Pure stdlib.
"""

from __future__ import annotations

import re

__all__ = [
    "CLASSIFIED_TYPES",
    "CONSERVATISM",
    "DEFAULT_TYPE",
    "CATEGORY_CONTRIBUTION",
    "classify_reply",
    "merge_route",
]

#: The default when nothing more specific matches — the behaviour this module replaces.
DEFAULT_TYPE = "reply_received"

# A commercial question. Answering one with a timer-drafted reply is the single worst
# outcome this classifier can produce, so it sorts first. `\bcost\b` will occasionally
# catch "at no cost to you" — an escalation the operator dismisses with one glance, which
# is the cheap side of this trade.
_PRICING_PATTERNS = (
    r"pricing",
    r"price",
    r"how much",
    r"\bcost\b",
    r"\bquote\b",
    r"budget",
    r"licen[cs]e fee",
    r"per[- ]seat",
    r"rate card",
    r"commercials",
)

# Active evaluation. Not "interested" — *procuring*: the vocabulary of a buying process
# that has already started. These escalate because a drafted reply cannot know what was
# promised in the last three emails, and a procurement thread is where that matters most.
_BUYER_INTENT_PATTERNS = (
    r"\brfp\b",
    r"\brfi\b",
    r"procurement",
    r"shortlist",
    r"proof of concept",
    r"\bpoc\b",
    r"\bpilot\b",
    r"evaluat(?:e|ing|ion)",
    r"security review",
    r"vendor (?:review|assessment)",
    r"business case",
    r"due diligence",
)

# A request to talk. Ends at the reply gate with the booking link `inbound-triage` already
# reads from PROFILE.md — this changes which verb is recorded, not what is reachable.
_MEETING_PATTERNS = (
    r"calendl?y",
    r"\bcalendar\b",
    r"book a (?:call|time|meeting|slot|demo)",
    r"schedule a (?:call|time|meeting|demo|chat)",
    r"set up a (?:call|time|meeting|chat)",
    r"let'?s (?:meet|talk|chat|connect|sync)",
    r"happy to (?:meet|talk|chat|connect|jump on)",
    r"jump on a (?:call|quick call)",
    r"available (?:this|next) week",
    r"what times",
    r"\bdemo\b",
)

# SC12: a soft no. Split out of `optout_watch`'s opt-out patterns on 2026-09-21, because
# "not interested" is not a suppression request — it carries no legal deadline, it does not
# belong on a Do Not Contact list, and routing it through the opt-out path spent the
# same-day compliance alert on a reply that has none. It also permanently removed people
# who had only said "not right now".
#
# Recorded, not drafted, nobody woken (`SUGGESTED_ACTIONS["not_now"] == "review"`), with a
# `reshow_after` date the sweep stamps so the account resurfaces instead of vanishing.
#
# It sorts BELOW the two escalate buckets and ABOVE `meeting_request`: "not interested, but
# ask me in Q3" is a soft no that happens to mention a future, not a meeting to book — and
# offering a booking link to someone who just declined is the exact wrong reply. A reply
# that also asks to be REMOVED still matched `optout_watch` upstream and never reaches here.
_NOT_NOW_PATTERNS = (
    r"not interested",
    r"no longer interested",
    r"no thanks",
    r"no thank you",
    r"not a (?:fit|priority|match)",
    r"not (?:the )?right time",
    r"not right now",
    r"maybe (?:later|next (?:year|quarter))",
    r"circle back (?:in|next|later)",
    r"check back (?:in|next|later)",
    r"revisit (?:in|next)",
    r"we(?:'re| are) all set",
    r"already have (?:a|one)",
)
# Deliberately NOT here: "no budget" / "budget freeze". Both contain `budget`, which the
# pricing bucket matches first, so a pattern for them here could never win — a rule that
# cannot fire is worse than no rule, because it reads as coverage. The routing it would
# have produced (escalate, a human reads it) is the conservative one anyway.

# Ordered. The first bucket whose pattern matches wins; see the precedence note above.
_ORDERED_BUCKETS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("pricing_question", tuple(re.compile(p, re.IGNORECASE) for p in _PRICING_PATTERNS)),
    ("buyer_intent", tuple(re.compile(p, re.IGNORECASE) for p in _BUYER_INTENT_PATTERNS)),
    ("not_now", tuple(re.compile(p, re.IGNORECASE) for p in _NOT_NOW_PATTERNS)),
    ("meeting_request", tuple(re.compile(p, re.IGNORECASE) for p in _MEETING_PATTERNS)),
)

#: Every type this module can return. The producer-coverage contract
#: (``tests/contracts/test_signal_producers.py``) reads this rather than AST-scanning for
#: ``build_signal`` calls, because the call site passes a variable, not a literal.
_TEXT_TYPES = frozenset({name for name, _ in _ORDERED_BUCKETS} | {DEFAULT_TYPE})


# ─────────────────────────────────────────────── SC10: the provider category, as a witness
#
# Saleshandy's unified inbox labels a reply with a category of its own. That label is a
# SECOND WITNESS to what this classifier already decided, and it is used in exactly one
# direction: it may make the route MORE conservative, never less.
#
# Why only one direction. The category is a vendor AI's label over text an outsider wrote,
# which makes it untrusted data twice over (§R5). Letting it relax a route would mean a
# crafted reply — or simply a mislabelled one — could turn a pricing question into a
# timer-drafted answer. Letting it tighten one costs an operator a glance. The measured
# case that settled it: a pricing question the vendor labels `interested` must still
# escalate, because our own matcher saw the commercial question and the vendor's label is
# not evidence that it did not.
#
# It is also NOT read from any message body. A thread's category is filter MEMBERSHIP —
# which filtered list call returned the thread id — so text inside a reply that says
# `"category": "do_not_contact"` is just text, reaching nothing.

#: Routes ordered least → most conservative. `merge_route` takes the max of the two
#: witnesses under this order, so the join can only ever move rightwards.
#:
#:   draft a reply  ›  no draft  ›  human read  ›  opt-out (our matcher only)
#:
#: `opt_out` is present as the ceiling of the order and is deliberately UNREACHABLE from a
#: category: suppression is our deterministic matcher's decision alone (`optout_watch`),
#: never a provider label's, so no entry in CATEGORY_CONTRIBUTION maps to it.
CONSERVATISM: tuple[str, ...] = (
    "meeting_request",  # drafted, with the booking link
    "reply_received",  # drafted
    "not_now",  # recorded, no draft
    "buyer_intent",  # a human reads it
    "pricing_question",  # a human reads it
    "opt_out",  # our matcher only — no category reaches this
)

#: Provider category key -> the route it argues for. A category absent from this map (an
#: unknown key, a tenant's custom category, or None) contributes NOTHING and the
#: classifier stands alone — an unrecognised label must never be read as permission, and
#: must never be read as an opt-out either.
CATEGORY_CONTRIBUTION: dict[str, str] = {
    # The vendor says a person is interested/procuring. Our classifier may have read the
    # same reply as a plain "thanks" — the conservative reading is that a human looks.
    "interested": "buyer_intent",
    "meeting_booked": "buyer_intent",
    # The vendor says this person does not want contact. We do NOT suppress on a vendor
    # label (that stays with our matcher), but we also do not draft them a reply.
    "do_not_contact": "not_now",
    "not_interested": "not_now",
    "not_now": "not_now",
    # An auto-reply. Drafting a response to a mail robot is noise; record it, draft nothing.
    "out_of_office": "not_now",
}


#: Every type this module can return, from EITHER witness. The producer-coverage contract
#: (``tests/contracts/test_signal_producers.py``) reads this rather than AST-scanning for
#: ``build_signal`` calls, because the call site passes a variable, not a literal. It
#: includes the category contributions because `merge_route` can return one of those
#: without any text pattern matching — a type reachable that way is produced, and the
#: contract must see it.
CLASSIFIED_TYPES = frozenset(_TEXT_TYPES | set(CATEGORY_CONTRIBUTION.values()))


def merge_route(classified_type: str, category_key: str | None) -> str:
    """Join our classifier's route with the provider category's, keeping the stricter.

    Pure and total. An unknown/custom/absent ``category_key``, or a classifier type this
    module does not rank, leaves the classifier's own answer untouched — the witness can
    only ever tighten, which is the property the injection case rests on.
    """
    if classified_type not in CONSERVATISM:
        return classified_type
    contributed = CATEGORY_CONTRIBUTION.get((category_key or "").strip().lower())
    if contributed is None or contributed not in CONSERVATISM:
        return classified_type
    return max((classified_type, contributed), key=CONSERVATISM.index)


def classify_reply(text: str) -> str:
    """Return the single ``signal_type`` for one inbound reply body.

    Total: an empty or unmatched body returns :data:`DEFAULT_TYPE`, which is what every
    non-opt-out reply was recorded as before this module existed. Callers are expected to
    have already excluded opt-outs (``optout_watch.find_optouts_in_thread``) — an opt-out
    is a compliance event, not a signal, and must never reach a drafting path.
    """
    if not text:
        return DEFAULT_TYPE
    for name, patterns in _ORDERED_BUCKETS:
        if any(p.search(text) for p in patterns):
            return name
    return DEFAULT_TYPE
