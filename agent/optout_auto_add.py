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
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from gtm_core.optout_watch import OptOutMatch, record_optout_event

logger = logging.getLogger("agent.optout_auto_add")

#: Recorded on the ``dnc_added`` row so the audit says which path ran.
APPROVED_BY = "auto:clear-optout"


async def handle_optout(
    cfg: Any,
    profile: str,
    ledgers: Any,
    match: OptOutMatch,
    escalate: Callable[..., Awaitable[bool]],
) -> bool:
    """Record and alert one opt-out; True when it is fully handled and needs no gate.

    The ``optout_detected`` row is written BEFORE the add, because the dispatcher only adds
    an address that already has an open row; the alert goes AFTER, so it can say what
    actually happened. Its result is recorded on the ``optout_auto_add`` row.
    """
    from agent.dnc_dispatch import enabled as dnc_enabled

    if not (match.clear and match.email and dnc_enabled()):
        escalated = await escalate(cfg, profile, match)
        record_optout_event(ledgers, match, escalated=escalated)
        return False
    record_optout_event(ledgers, match, escalated=None)
    outcome = await _auto_add(cfg, ledgers, match.email)
    escalated = await escalate(cfg, profile, match, auto_outcome=outcome)
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
