"""The sweep's handling of one detected opt-out — including the automatic DNC add.

Operator decision 2026-09-24: a CLEAR opt-out (``gtm_core.optout_watch.is_clear_optout`` —
a short typed "stop" / "unsubscribe" / "remove me", nothing negated or questioned, the
quoted original ignored) should not wait for a human tap. With the DNC kill switch on, it is
added here, by Python, through the same dispatcher a gate approval uses
(:func:`agent.dnc_dispatch.dispatch_approved_dnc_add`): the same switch, the same
intersection with open ledger rows, the same read-back. No model is involved.

Anything else, and any add that fails, keeps the gated path unchanged: alert, row, and a
``suppress_on_provider`` signal that opens the ``inbound/optout-suppress`` approval gate.
Add only, as everywhere in this vertical — nothing here can remove a DNC entry.

**The enrolled address, when the reply came from another one (SC9b item 4).** A prospect
enrolled as one address can reply from an alias. The automatic add above stays the reply's
own sender only; :func:`record_enrolled_alias` resolves the ENROLLED address from the thread
and, when it differs, records it as an open opt-out row routed to the gate — never added here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from gtm_core.optout_sets import optout_key
from gtm_core.optout_watch import OptOutMatch, message_sender, message_ts, record_optout_event
from gtm_core.signals import build_signal

logger = logging.getLogger("agent.optout_auto_add")

#: Recorded on the ``dnc_added`` row so the audit says which path ran.
APPROVED_BY = "auto:clear-optout"


async def handle_optout(
    cfg: Any,
    profile: str,
    ledgers: Any,
    match: OptOutMatch,
    escalate: Callable[..., Awaitable[bool]],
    *,
    enrolled: str | None = None,
) -> bool:
    """Record and alert one opt-out; True when it is fully handled and needs no gate.

    The ``optout_detected`` row is written BEFORE the add, because the dispatcher only adds
    an address that already has an open row; the alert goes AFTER, so it can say what
    actually happened. Its result is recorded on the ``optout_auto_add`` row.

    ``enrolled`` (a differing enrolled address, from :func:`record_enrolled_alias`) is only
    NAMED in the alert — it is never added here. Passed on only when set.
    """
    from agent.dnc_dispatch import enabled as dnc_enabled

    alias = {"enrolled": enrolled} if enrolled else {}
    if not (match.clear and match.email and dnc_enabled()):
        escalated = await escalate(cfg, profile, match, **alias)
        record_optout_event(ledgers, match, escalated=escalated)
        return False
    record_optout_event(ledgers, match, escalated=None)
    outcome = await _auto_add(cfg, ledgers, match.email)
    escalated = await escalate(cfg, profile, match, auto_outcome=outcome, **alias)
    ledgers.append_history(
        {
            "event": "optout_auto_add",
            "skill": "optout-watch",
            "email": match.email,
            "thread_id": match.thread_id,
            "status": outcome.status,
            "detail": outcome.detail[:200],
            "escalated": escalated,
        }
    )
    return outcome.ok and outcome.status == "added"


async def _auto_add(cfg: Any, ledgers: Any, email: str):
    """The dispatcher never raises by contract; this catch is for an import-time surprise."""
    from agent.dnc_dispatch import DncDispatchOutcome, dispatch_approved_dnc_add

    try:
        return await dispatch_approved_dnc_add(
            cfg, ledgers, draft={"addresses": [email]}, approved_by=APPROVED_BY
        )
    except Exception as exc:  # noqa: BLE001 — a failed add falls back to the human gate
        logger.warning("optout_auto_add: automatic DNC add failed for %s", email, exc_info=True)
        return DncDispatchOutcome(ok=False, status="dnc_add_failed", detail=type(exc).__name__)


def _to_addresses(msg: dict) -> set[str]:
    to = msg.get("to")
    to = [to] if isinstance(to, str) else to
    if not isinstance(to, list):
        return set()
    return {optout_key(a) for a in to if isinstance(a, str) and "@" in a}


def enrolled_address(thread: dict, match: OptOutMatch) -> str | None:
    """The address we emailed the prospect who sent ``match``, or ``None`` if not provable.

    Joined on the provider's own prospect id, in the LIVE ``get_thread`` shape (verified
    2026-09-22, pinned in ``tests/agent/test_saleshandy_live_shapes.py``): the prospect's
    reply carries ``fromProspectId``; a message WE sent carries ``fromProspectId: None``,
    ``toProspectId`` and ``to``. Envelope fields only — no body is read (§R5). Anything short
    of exactly one address — no id, no outbound message for it, two addresses — is ``None``:
    a guessed enrolled address would put a stranger in front of the approver.
    """
    messages = thread.get("messages") or []
    sender = optout_key(match.email)
    pids = {
        str(m["fromProspectId"])
        for m in messages
        if m.get("fromProspectId") is not None
        and optout_key(message_sender(m)) == sender
        and message_ts(m) == match.message_ts
    }
    if len(pids) != 1:
        return None
    (pid,) = pids
    found: set[str] = set()
    for m in messages:
        ours = "fromProspectId" in m and m.get("fromProspectId") is None
        if ours and m.get("toProspectId") is not None and str(m["toProspectId"]) == pid:
            found |= _to_addresses(m)
    return found.pop() if len(found) == 1 else None


def record_enrolled_alias(ledgers: Any, match: OptOutMatch, thread: dict) -> dict | None:
    """Gate the enrolled address when the opt-out came from a different one.

    Returns the ``optout_detected`` signal that opens the ``optout-suppress`` gate for it, or
    ``None`` when there is nothing extra to do (same address, or not resolvable — logged).
    The open row is what lets an APPROVED draft reach the address through the dispatcher's
    intersection; ``clear`` is False on it and it never reaches :func:`_auto_add`.
    """
    enrolled = enrolled_address(thread, match)
    sender = optout_key(match.email)
    if enrolled is None:
        logger.warning(
            "optout_auto_add: could not resolve the enrolled address for thread %s — "
            "only the replying address is handled",
            match.thread_id,
        )
        return None
    if not sender or enrolled == sender:
        return None
    ledgers.append_history(
        {
            "event": "optout_detected",
            "skill": "optout-watch",
            "thread_id": match.thread_id,
            "email": enrolled,
            "replied_from": sender,
            "enrolled_differs_from_sender": True,
            "subject": match.subject,
            "message_ts": match.message_ts,
            "snippet": match.snippet,
            "direction_known": match.direction_known,
            "clear": False,
            "escalated": False,
            "action_required": (
                f"the ENROLLED address {enrolled} did not write this reply — the opt-out came "
                f"from {sender} on the same thread. Approve adding {enrolled} to Global DNC only "
                "if you judge it is the same person; it is never added automatically"
            ),
        }
    )
    return build_signal(
        enrolled,
        "optout_detected",
        f"saleshandy-inbox:{match.thread_id}",
        ts=match.message_ts or None,
        meta={"thread_id": match.thread_id, "subject": match.subject, "replied_from": sender},
    )
