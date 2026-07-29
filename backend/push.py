"""Push notification abstraction for gate approval events.

When a pipeline run hits a gate (⟦GATE:plan⟧ or ⟦GATE:publish⟧), the mobile
client needs to know immediately so the operator can approve from their phone.

This module fetches all push tokens for the workspace from the DB and dispatches
a notification. The actual transport (FCM / APNs) is selected by the PUSH_PROVIDER
env var. If PUSH_PROVIDER is unset, the call is a no-op — the infrastructure is
in place and the client falls back to polling GET /v1/runs/{id}.

Transport config (all from Doppler / environment):
  PUSH_PROVIDER          = "fcm" | "apns" | unset (no-op)
  PUSH_FCM_SERVER_KEY    = Firebase Cloud Messaging server key (FCM legacy v1)
                           Get from Firebase Console → Project Settings → Cloud Messaging
  PUSH_APNS_KEY_ID       = APNs key ID (not yet implemented; reserved for Phase F+)
  PUSH_APNS_TEAM_ID      = Apple Team ID (reserved)
  PUSH_APNS_KEY_PATH     = Path to .p8 file (reserved)
  PUSH_APNS_BUNDLE_ID    = com.example.sample-app (replace with your app's bundle ID)

Adding a new provider: implement _send_fcm() or _send_apns() and dispatch in
send_gate_push(). The DB query and error handling are shared.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

from .database import workspace_scope

log = logging.getLogger(__name__)

_PROVIDER = os.getenv("PUSH_PROVIDER", "").lower()


async def send_gate_push(
    pool: asyncpg.Pool,
    workspace_id: str,
    run_id: str,
    gate_type: str,
) -> int:
    """Notify all registered devices for a workspace that a gate needs approval.

    Returns the number of tokens the notification was dispatched to (0 if no
    tokens are registered or PUSH_PROVIDER is unset).

    Called from runs.py when status transitions to 'awaiting_approval'.
    Safe to call and ignore the result — a push failure never blocks the gate.
    """
    try:
        tokens = await _fetch_tokens(pool, workspace_id)
    except Exception:
        log.exception(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "push: failed to fetch tokens for workspace %s", workspace_id
        )
        return 0

    if not tokens:
        log.debug(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "push: no tokens registered for workspace %s", workspace_id
        )
        return 0

    payload = _build_payload(run_id, gate_type)

    if not _PROVIDER:
        log.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            "push: PUSH_PROVIDER not set — would notify %d token(s) for %s gate on run %s",
            len(tokens),
            gate_type,
            run_id,
        )
        return 0

    dispatched = 0
    for token, platform in tokens:
        try:
            if platform == "fcm" and _PROVIDER == "fcm":
                await _send_fcm(token, payload)
                dispatched += 1
            elif platform == "apns" and _PROVIDER == "apns":
                await _send_apns(token, payload)
                dispatched += 1
            else:
                log.debug("push: token platform %r not handled by provider %r", platform, _PROVIDER)
        except Exception:
            log.exception(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                "push: dispatch failed for token platform=%s run=%s", platform, run_id
            )

    log.info(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        "push: dispatched to %d/%d tokens for run %s", dispatched, len(tokens), run_id
    )
    return dispatched


async def _fetch_tokens(pool: asyncpg.Pool, workspace_id: str) -> list[tuple[str, str]]:
    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(
            "SELECT token, platform FROM push_tokens WHERE workspace_id = $1::uuid",
            workspace_id,
        )
    return [(r["token"], r["platform"]) for r in rows]


def _build_payload(run_id: str, gate_type: str) -> dict:
    label = "Plan ready for review" if gate_type == "⟦GATE:plan⟧" else "Post ready to approve"
    return {
        "title": "GTM — action needed",
        "body": label,
        "data": {"run_id": run_id, "gate": gate_type},
    }


async def _send_fcm(token: str, payload: dict) -> None:
    """FCM HTTP v1 via server key. Requires PUSH_FCM_SERVER_KEY."""
    import httpx

    server_key = os.environ.get("PUSH_FCM_SERVER_KEY")
    if not server_key:
        raise RuntimeError("PUSH_FCM_SERVER_KEY not set")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://fcm.googleapis.com/fcm/send",
            headers={"Authorization": f"key={server_key}", "Content-Type": "application/json"},
            json={
                "to": token,
                "notification": {
                    "title": payload["title"],
                    "body": payload["body"],
                },
                "data": payload["data"],
                "priority": "high",
            },
            timeout=10,
        )
        resp.raise_for_status()


async def _send_apns(token: str, payload: dict) -> None:
    # APNs HTTP/2 requires a JWT signed with the .p8 key — reserved for Phase F+
    raise NotImplementedError("APNs not yet implemented — set PUSH_PROVIDER=fcm")
