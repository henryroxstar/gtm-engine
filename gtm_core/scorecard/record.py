"""The agent-evidence word a row may be scored on, derived from its own signal record (PH15).

THE DEFECT THIS CLOSES
----------------------
``agent_evidence`` used to arrive as a free-standing input, decided somewhere upstream and never
checked against the ``signal_agent_kind`` the same row carried. The 2026-09-23 rescore therefore
wrote "company-specific agent evidence (30 of 30)" on rows whose record said the clause involved
no agents, and "no agent evidence found (4 of 30)" on rows whose record said ``ai`` — two claims
about one account on one row, contradicting each other, with nothing to notice.

THE RULE
--------
The record decides; a caller may only agree with it or refine it.

* :data:`KIND_TO_EVIDENCE` maps every member of :class:`gtm_core.signal_record.AgentKind` to the
  word it supports. It is exhaustive on purpose: a kind outside it is an **error**, never a
  default, because a default is how an unanticipated value becomes a grant.
* ``unclear`` and a missing/blank kind are **not assessed**. The row is categorised with the
  card's "agent activity not assessed" category — the sufficiency gate's answer to a missing
  input — and never given a number, high or low. Scoring it ``absent`` would assert "researched
  and found nothing", which the record does not say either.
* A caller-supplied ``agent_evidence`` that the kind does not admit (:data:`COMPATIBLE`) raises,
  naming the row. Silently preferring either side would hide the disagreement that is the defect.
* A supplied word outside the evidence vocabulary still categorises, exactly as
  :func:`gtm_core.scorecard.evidence.classify` has always ruled.

Kinds are matched exactly, with no case folding or trimming: ``"AI"`` is not a kind this module
recognises, and it says so loudly rather than guessing. Ledger values are canonical lower-case
once ``prospects_import finalize`` has normalised them, so only a pre-finalize row can carry a
variant — and that row fails here by design, rather than being coerced into a grant. The vocabulary itself is not restated
here — it is :class:`AgentKind`, and the exhaustiveness test goes red if that grows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..signal_record import AgentKind
from .evidence import Evidence, classify
from .model import ScoreCardError

#: The card input this module governs, and the record field that governs it.
AGENT_EVIDENCE_INPUT = "agent_evidence"
AGENT_KIND_FIELD = "signal_agent_kind"

#: Every recorded kind -> the evidence word it supports on its own. Exhaustive over AgentKind.
KIND_TO_EVIDENCE: Mapping[str, str] = {
    AgentKind.AI: Evidence.PRESENT,
    # The account's "agents" are people (insurance, travel, recruiting): no AI-agent fact.
    AgentKind.HUMAN: Evidence.ABSENT,
    # The clause does not involve agents at all.
    AgentKind.NONE: Evidence.ABSENT,
    # The record itself says the kind was never resolved.
    AgentKind.UNCLEAR: Evidence.NOT_ASSESSED,
}

#: Kind -> every caller-supplied word that does not contradict it. ``industry_only`` refines a
#: kind that names no company-specific agent (sector activity found elsewhere), and without it
#: the card's ``industry_only`` weight would be unreachable from any record. It does NOT refine
#: ``ai``: the record already holds a company-specific AI-agent fact.
COMPATIBLE: Mapping[str, frozenset[str]] = {
    AgentKind.AI: frozenset({Evidence.PRESENT}),
    AgentKind.HUMAN: frozenset({Evidence.ABSENT, Evidence.INDUSTRY_ONLY}),
    AgentKind.NONE: frozenset({Evidence.ABSENT, Evidence.INDUSTRY_ONLY}),
    AgentKind.UNCLEAR: frozenset(),
}


def derived_inputs(card: Any) -> dict[str, dict[str, Any]]:
    """What ``explain`` tells a caller: the inputs this engine derives, and from which field.

    Empty for a card with no agent-evidence axis — the record is not that card's business.
    """
    if card.axis_reading(AGENT_EVIDENCE_INPUT) is None:
        return {}
    return {
        AGENT_EVIDENCE_INPUT: {
            "from": AGENT_KIND_FIELD,
            "mapping": dict(KIND_TO_EVIDENCE),
            # The only word a caller adds that the kind alone cannot say.
            "supply_only": [Evidence.INDUSTRY_ONLY],
        }
    }


def agent_evidence_from_record(row: Mapping[str, Any]) -> str:
    """The word this row's agent-evidence axis is scored on. Raises on an unknown kind or a
    supplied word the kind contradicts; returns ``not_assessed`` when the record has no kind."""
    who = row.get("id") or row.get("company") or "?"
    kind = row.get(AGENT_KIND_FIELD)
    if kind is None or kind == "":
        return Evidence.NOT_ASSESSED
    if not isinstance(kind, str) or kind not in KIND_TO_EVIDENCE:
        raise ScoreCardError(
            f"{who}: {AGENT_KIND_FIELD} {kind!r} is not one of {sorted(KIND_TO_EVIDENCE)} — "
            "an unrecognised kind is never read as a default"
        )
    derived = KIND_TO_EVIDENCE[kind]
    if derived == Evidence.NOT_ASSESSED:
        return derived

    supplied = row.get(AGENT_EVIDENCE_INPUT)
    if supplied is None or supplied == "":
        return derived
    word = classify(supplied)
    if word == Evidence.NOT_ASSESSED:
        return word  # an unrecognised word categorises, as it always has
    if word not in COMPATIBLE[kind]:
        raise ScoreCardError(
            f"{who}: {AGENT_EVIDENCE_INPUT} {word!r} contradicts {AGENT_KIND_FIELD} {kind!r}, "
            f"which supports {derived!r} — fix the record or drop the supplied word"
        )
    return word
