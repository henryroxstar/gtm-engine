"""Service-to-service entitlement sync (the billing service → gtm-engine).

the billing service owns billing — RevenueCat integration, pricing bands, reconcile. gtm-engine
no longer integrates with any billing vendor. When a workspace's entitlement changes,
the billing service PUTs the resolved state here; gtm-engine caches it in ``subscriptions`` and its
cost guard (``acheck_budget``) enforces the cap. This replaces the former in-repo
RevenueCat webhook (see the billing-boundary design doc).

Auth: a shared service secret (``BILLING_SYNC_SECRET``), NOT a user JWT — see
``require_service_auth``. This route GRANTS entitlement, so accepting a user token
would be a free self-upgrade to pro_plus. That is the single highest-risk property here.

Idempotency + ordering: each sync carries an opaque ``sync_id`` (dedup) and a monotonic
``version`` (an older sync is ignored). The record + apply run in ONE transaction under
a ``FOR UPDATE`` lock on the subscription row, so a redelivered or reordered sync can't
flip a live entitlement — the same guarantees the RC webhook had, provider-agnostic now.

Cap sourcing: the dollar cap comes over the wire from the billing service's pricing (``cap_usd``);
gtm-engine stores + enforces the number, it does not decide it. The one exception is the
FREE invariant — a free plan is clamped to a 0 cap (no paid spend), which is an
enforcement floor, not a price.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..database import workspace_scope
from ..deps import require_service_auth
from ..schemas import EntitlementSyncRequest, EntitlementSyncResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/entitlement", tags=["entitlement"])

# workspace_id is a UUID; reject anything else before it reaches the DB.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


async def _apply_sync(
    request: Request,
    workspace_id: str,
    entitlement: str,
    cap_usd: float,
    status_val: str | None,
    sync_id: str,
    version: int | None,
) -> str:
    """Record + apply one entitlement sync in ONE transaction.

    Returns 'new' | 'duplicate' | 'stale' | 'unknown'. Dedup (INSERT ON CONFLICT on the
    unique ``sync_id``), the out-of-order guard (a later ``version`` already recorded for
    this workspace), AND the subscription UPDATE all run inside a single
    ``workspace_scope`` transaction holding a ``FOR UPDATE`` lock — so concurrent syncs
    for one workspace are serialized and a stale sync can't overwrite a newer plan. A
    duplicate/stale sync does no UPDATE; a missing workspace/subscription row → 'unknown'.
    """
    pool: Any = request.app.state.pool
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            # Serialize concurrent syncs for this workspace on its subscription row.
            locked = await conn.fetchrow(
                "SELECT 1 FROM subscriptions WHERE workspace_id = $1 FOR UPDATE",
                workspace_id,
            )
            inserted = await conn.execute(
                "INSERT INTO entitlement_sync_events(sync_id, workspace_id, entitlement, version) "
                "VALUES($1, $2, $3, $4) ON CONFLICT (sync_id) DO NOTHING",
                sync_id,
                workspace_id,
                entitlement,
                version,
            )
            if inserted == "INSERT 0 0":
                return "duplicate"
            if version is not None:
                newer = await conn.fetchval(
                    "SELECT count(*) FROM entitlement_sync_events "
                    "WHERE workspace_id = $1 AND version > $2 AND sync_id <> $3",
                    workspace_id,
                    version,
                    sync_id,
                )
                if newer:
                    return "stale"
            if locked is None:
                log.warning(
                    "entitlement sync for workspace %s with no subscription row — recorded, not applied",
                    workspace_id,
                )
                return "unknown"
            await conn.execute(
                """
                UPDATE subscriptions
                   SET entitlement          = $1,
                       monthly_cost_cap_usd  = $2,
                       status                = COALESCE($3, status)
                 WHERE workspace_id          = $4
                """,
                entitlement,
                cap_usd,
                status_val,
                workspace_id,
            )
    except asyncpg.ForeignKeyViolationError:
        # workspace_id does not exist — the sync targets a workspace that's gone.
        return "unknown"
    return "new"


@router.put("/{workspace_id}", status_code=status.HTTP_200_OK)
async def sync_entitlement(
    workspace_id: str,
    body: EntitlementSyncRequest,
    request: Request,
    _: Annotated[None, Depends(require_service_auth)],
) -> EntitlementSyncResponse:
    """Set a workspace's entitlement + spend cap. Service-authed; idempotent per sync_id."""
    if not _UUID_RE.match(workspace_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workspace_id must be a UUID")

    # FREE invariant: a free plan never carries paid budget. Clamp to 0 (enforcement
    # floor, not a pricing decision) so removing billing can never leave a free tenant
    # able to spend.
    cap_usd = body.cap_usd
    if body.entitlement == "free" and cap_usd != 0:
        log.warning(
            "entitlement sync: free plan for %s sent cap_usd=%s — clamped to 0",
            workspace_id,
            cap_usd,
        )
        cap_usd = 0.0

    outcome = await _apply_sync(
        request,
        workspace_id,
        body.entitlement,
        cap_usd,
        body.status,
        body.sync_id,
        body.version,
    )
    return EntitlementSyncResponse(applied=(outcome == "new"), outcome=outcome)
