"""Backend half of the SC9 do-not-contact gate.

When an operator approves a run's ``dnc-add`` node (a pack node declaring
``external_effect = "dnc_add"``), the *exact approved address list* is handed to the same
shared helper the VPS CLI uses (:mod:`agent.dnc_dispatch`), which narrows it against the
profile's own opt-out ledger, resolves the DNC list id, writes inside the narrow
``dnc_context()`` window, reads back, and only then records the audit event.

Nothing here decides *who* to suppress — the addresses were fixed and shown to the operator
at approval time, and the dispatcher refuses any that lack an open ``optout_detected`` /
``optout_unreadable`` row regardless.

Invariants this module preserves:
  - No per-workspace destination table: Saleshandy is BYOK per workspace
    (``backend/session.py``'s ``_workspace_scoped_config``), so ``cfg.saleshandy_api_key``
    is already the correct, tenant-scoped credential.
  - ``GTM_DNC_ADD_ENABLED`` is closed by default; an unset switch dispatches nothing.
  - ``dry_run`` never dispatches (enforced in the shared helper).
  - reject / cancel / gate timeout never dispatch — they never reach this call.
  - ADD ONLY. There is no removal path here or in the shared helper.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def dnc_gated_node(graph: Any, node_id: str) -> bool:
    """True if ``node_id`` declares ``external_effect == "dnc_add"`` in this run's engine
    graph. Mirrors :func:`backend.email_dispatch.email_enroll_gated_node` and checks the
    specific effect VALUE: the three dispatch-only effects route to three different Python
    dispatchers, so a generic truthy check cannot pick the right one."""
    for node in getattr(graph, "nodes", ()):
        if node.id == node_id:
            return getattr(node, "external_effect", None) == "dnc_add"
    return False


async def dispatch_backend_dnc_add(
    cfg: Any,
    profile: str,
    *,
    pool: Any = None,  # noqa: ARG001 — signature parity with the other two dispatchers
    workspace_id: str,
    draft: dict,
    dry_run: bool = False,
) -> Any:
    """Add the approved addresses to this workspace's provider DNC list. Never raises.

    Returns the shared helper's :class:`~agent.dnc_dispatch.DncDispatchOutcome`, or
    ``None`` when this workspace has no Saleshandy key and nothing could be attempted —
    fail-closed, with no shared-account fallback, matching the other two dispatchers.
    """
    try:
        from agent.dnc_dispatch import dispatch_approved_dnc_add
        from agent.ledgers import Ledgers

        outcome = await dispatch_approved_dnc_add(
            cfg, Ledgers(cfg, profile), draft=draft, dry_run=dry_run
        )
        if outcome.status == "not_configured":
            log.info(
                "backend dnc add: no Saleshandy key for workspace=%s profile=%s — not writing",
                workspace_id,
                profile,
            )
            return None
        return outcome
    except Exception:  # noqa: BLE001 — a dispatch failure must not crash the run task
        log.exception("backend dnc-add dispatch failed for profile=%s", profile)
        return None
