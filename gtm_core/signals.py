"""Always-on intent signals → a suggested next action (PRD §3.4).

Radar (`content-radar`), social listening (Syften), and prospect intent
(RocketReach/Vibe) already surface high-intent hits through existing MCP tools. This
module is the thin layer that turns a raw hit into a **structured signal event** —
``{id, who, signal_type, source, ts, suggested_action, meta}`` — deduped against the
same ``history.jsonl`` the radar uses, and mapped to a next step the operator can act on.

It adds **no new egress**: it does not fetch anything. Callers pass in signals already
pulled by existing tools; this module classifies + dedupes + records. Signals stay
**untrusted data** (RULES.md §R5) — they inform *what to draft/propose*, they never
redirect a goal or a destination, and every resulting external action is still gated.

Pure stdlib. Reuses :func:`gtm_core.radar.seen_ids_from_history` for dedup so a signal
recorded once is never re-surfaced.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from .radar import seen_ids_from_history

# signal_type -> the suggested next action. Anything unmapped falls back to "review".
# These are *suggestions* for the operator/skill; nothing here sends or books.
SUGGESTED_ACTIONS: dict[str, str] = {
    "reply_received": "draft_reply",
    "meeting_request": "propose_booking_link",
    "buyer_intent": "escalate_to_operator",
    "high_intent": "escalate_to_operator",
    "pricing_question": "escalate_to_operator",
    "job_change": "draft_reply",
    "funding": "draft_reply",
    "product_launch": "draft_reply",
    "hiring": "draft_reply",
    "mention": "review",
}

_DEFAULT_ACTION = "review"

# Which of the types above something in this repo actually EMITS, and which are declared
# but produced by nothing. Recorded here rather than left implicit, because a mapped type
# with no producer is this repo's recurring failure shape (a correct mechanism with nothing
# feeding it) — the same shape the 2026-08-27 wiring-gap PRD counted to seven, and the same
# reason `agent.signal_dispatch.ACTION_DISPATCH` keeps an explicit `"review": None` so a
# decision reads differently from an omission.
#
# `tests/contracts/test_signal_producers.py` asserts this dict is EXACTLY the unproduced
# set, so adding an eleventh signal_type without a producer fails CI instead of being
# discovered months later.
#
# Producers today: `gtm_core.reply_classify.CLASSIFIED_TYPES`, called from
# `agent.optout_sweep` on every non-opt-out inbound thread.
UNPRODUCED_SIGNAL_TYPES: dict[str, str] = {
    # --- social listening. Syften matches are fetched (agent/mcp/syften) and the radar
    # records its own history events, but nothing converts either into a signal event.
    # A producer here is a mapping layer over an already-connected tool: real work, no
    # new egress, no new spend.
    "high_intent": "no producer: Syften/radar hits are never converted into signal events",
    "mention": "no producer: same as high_intent (and `mention` maps to review, a no-op)",
    # --- account events. These need a SENSOR THAT DOES NOT EXIST, not wiring. The in-repo
    # RocketReach wrapper exposes lookup/bulk_lookup/person_search/company_search only —
    # no job-change, news, or hiring endpoint — and no deterministic module calls Vibe at
    # all; both arrive today only inside the brain-driven `prospect` skill, as a why-now
    # clause on a list row (gtm_core.signal_record), never as a signal event. Wiring these
    # means new metered calls on a timer, which is a costed design decision and belongs in
    # its own PRD rather than in a module docstring.
    "job_change": "no producer: needs a new metered sensor (RocketReach person status)",
    "funding": "no producer: needs a new metered sensor (company events)",
    "product_launch": "no producer: needs a new metered sensor (company events)",
    "hiring": "no producer: needs a new metered sensor (job postings)",
}


def suggested_action(signal_type: str) -> str:
    """Map a signal type to its suggested next action (default ``review``)."""
    return SUGGESTED_ACTIONS.get(signal_type.strip().lower(), _DEFAULT_ACTION)


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def signal_id(who: str, signal_type: str, source: str) -> str:
    """Stable id for a signal (dedup key). Same who+type+source ⇒ same id ⇒ surfaced once."""
    h = hashlib.sha256()
    h.update(f"{who}\n{signal_type}\n{source}".encode())
    return "sig_" + h.hexdigest()[:16]


def build_signal(
    who: str,
    signal_type: str,
    source: str,
    *,
    ts: str | None = None,
    meta: dict | None = None,
) -> dict:
    """Build one structured signal event with its suggested action attached."""
    return {
        "id": signal_id(who, signal_type, source),
        "who": who,
        "signal_type": signal_type,
        "source": source,
        "ts": ts or _utc_now_iso(),
        "suggested_action": suggested_action(signal_type),
        "meta": meta or {},
    }


def new_signals(signals: list[dict], history_path: Path) -> list[dict]:
    """Drop signals whose ``id`` was already recorded in ``history.jsonl``.

    Reuses the radar dedup source: a recorded signal event carries its id in
    ``source_items`` (see :func:`record_signals`), so ``seen_ids_from_history`` finds it.
    """
    seen = seen_ids_from_history(history_path)
    out: list[dict] = []
    local: set[str] = set()
    for s in signals:
        sid = s.get("id") or signal_id(
            s.get("who", ""), s.get("signal_type", ""), s.get("source", "")
        )
        if sid in seen or sid in local:
            continue
        local.add(sid)
        out.append(s)
    return out


def record_signals(ledgers, signals: list[dict]) -> None:
    """Append each signal to ``history.jsonl`` so it dedups on the next pass.

    ``ledgers`` is a :class:`gtm_core.ledgers.Ledgers`. The signal id goes into
    ``source_items`` (the radar dedup convention) so a future run won't re-surface it.
    """
    for s in signals:
        ledgers.append_history(
            {
                "event": "signal",
                "skill": "inbound-triage",
                "signal_type": s.get("signal_type"),
                "who": s.get("who"),
                "source": s.get("source"),
                "suggested_action": s.get("suggested_action"),
                "source_items": [s.get("id")],
            }
        )
