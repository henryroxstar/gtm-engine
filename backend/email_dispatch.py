"""Backend Gate-2 dispatch for email enrollment (A11).

The backend's half of the enrollment gate: when an operator approves a run's
`sequence` node (a pack node declaring `external_effect = "email_enroll"`), the
*exact approved enrollment request* is handed to the same shared helper the VPS CLI
uses (``agent.email_dispatch``), which resolves the workspace's Saleshandy key, makes
the call, and writes the audit event. Nothing here decides *what* to enroll — the
request was already fixed and shown to the operator at approval time.

Invariants this module preserves:
  - No per-workspace "destination" settings table is needed the way LinkedIn publish
    needs one (``workspace_publish_settings``): Saleshandy is BYOK per workspace
    (``backend/session.py``'s ``_workspace_scoped_config``), so `cfg.saleshandy_api_key`
    is already the correct, tenant-scoped credential — reusing a second table here
    would just be a second config path for the same fact.
  - ``dry_run`` never dispatches (enforced in the shared helper).
  - reject / cancel / gate timeout never dispatch — they never reach this call.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def email_enroll_gated_node(graph: Any, node_id: str) -> bool:
    """True if ``node_id`` declares ``external_effect == "email_enroll"`` in this run's
    engine graph. Mirrors ``backend.publish_dispatch.publish_gated_node`` but checks the
    specific effect VALUE, not just truthiness — the two dispatch-only effect kinds route
    to different Python dispatchers, so a generic truthy check is not enough to pick one."""
    for node in getattr(graph, "nodes", ()):
        if node.id == node_id:
            return getattr(node, "external_effect", None) == "email_enroll"
    return False


async def dispatch_backend_email_enroll(
    cfg: Any,
    profile: str,
    *,
    pool: Any,  # noqa: ARG001 — accepted for signature parity with dispatch_backend_publish;
    # no workspace-scoped destination lookup is needed here (see module docstring).
    workspace_id: str,
    draft: dict,
    dry_run: bool = False,
) -> Any:
    """Enroll the approved leads/prospects for a backend run. Never raises.

    Returns the shared helper's :class:`~agent.email_dispatch.EnrollDispatchOutcome`, or
    ``None`` if this workspace has no Saleshandy key configured (unconfigured ⇒ leave the
    run's status alone, fail-closed — no shared-account fallback, matching
    ``dispatch_backend_publish``'s own posture).
    """
    try:
        from agent.email_dispatch import dispatch_approved_enrollment
        from agent.ledgers import Ledgers

        outcome = await dispatch_approved_enrollment(
            cfg, Ledgers(cfg, profile), draft=draft, dry_run=dry_run
        )
        if outcome.status == "not_configured":
            log.info(
                "backend email enroll: no Saleshandy key for workspace=%s profile=%s — "
                "not enrolling",
                workspace_id,
                profile,
            )
            return None
        return outcome
    except Exception:  # noqa: BLE001 — a dispatch failure must not crash the run task
        log.exception("backend email-enroll dispatch failed for profile=%s", profile)
        return None
