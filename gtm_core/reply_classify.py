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

    pricing_question > buyer_intent > meeting_request > reply_received

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
    "DEFAULT_TYPE",
    "classify_reply",
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

# Ordered. The first bucket whose pattern matches wins; see the precedence note above.
_ORDERED_BUCKETS: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("pricing_question", tuple(re.compile(p, re.IGNORECASE) for p in _PRICING_PATTERNS)),
    ("buyer_intent", tuple(re.compile(p, re.IGNORECASE) for p in _BUYER_INTENT_PATTERNS)),
    ("meeting_request", tuple(re.compile(p, re.IGNORECASE) for p in _MEETING_PATTERNS)),
)

#: Every type this module can return. The producer-coverage contract
#: (``tests/contracts/test_signal_producers.py``) reads this rather than AST-scanning for
#: ``build_signal`` calls, because the call site passes a variable, not a literal.
CLASSIFIED_TYPES = frozenset({name for name, _ in _ORDERED_BUCKETS} | {DEFAULT_TYPE})


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
