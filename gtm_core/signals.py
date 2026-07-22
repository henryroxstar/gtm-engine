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
