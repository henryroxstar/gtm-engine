"""Workspace info and subscription endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from gtm_core.tiers import Tier

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth, require_tier
from ..schemas import PatchCostCapRequest, SubscriptionResponse, WorkspaceResponse

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("", response_model=WorkspaceResponse)
async def get_workspace(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> WorkspaceResponse:
    """Return the workspace info and current subscription entitlement."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """SELECT w.id::text, w.slug, w.display_name,
                      s.entitlement, s.monthly_cost_cap_usd
               FROM workspaces w
               JOIN subscriptions s ON s.workspace_id = w.id
               WHERE w.id = $1::uuid""",
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")

    return WorkspaceResponse(
        id=row["id"],
        slug=row["slug"],
        display_name=row["display_name"],
        entitlement=row["entitlement"],
        monthly_cost_cap_usd=float(row["monthly_cost_cap_usd"]),
    )


@router.get("/subscriptions/me", response_model=SubscriptionResponse)
async def get_subscription(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> SubscriptionResponse:
    """Lightweight entitlement check for the mobile paywall screen.

    Returns only subscription state — no workspace metadata. the billing service calls this
    to gate Pro/Pro+ features without loading the full workspace response.
    Entitlement is always live from the DB so a a billing-service entitlement sync (PUT
    /v1/entitlement/{workspace_id}) takes effect on the next call without re-login.
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """SELECT entitlement, status, monthly_cost_cap_usd,
                      revenuecat_subscriber_id
               FROM subscriptions
               WHERE workspace_id = $1::uuid""",
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

    return SubscriptionResponse(
        entitlement=row["entitlement"],
        status=row["status"],
        monthly_cost_cap_usd=float(row["monthly_cost_cap_usd"]),
        revenuecat_subscriber_id=row["revenuecat_subscriber_id"],
    )


@router.patch("/cost-cap", status_code=status.HTTP_200_OK)
async def patch_cost_cap(
    body: PatchCostCapRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    _: None = Depends(require_tier(Tier.PIPELINE)),
) -> dict:
    """Update the monthly cost cap for the authenticated workspace.

    Only paid tiers (PRO / PRO_PLUS) may set a cap — the same tier gate that guards
    pipeline runs. This preserves the FREE invariant that a free workspace carries a
    $0 cap and no paid budget (``gtm_core.capabilities``): without this gate a FREE
    workspace could raise its own ``monthly_cost_cap_usd`` — the exact value the budget
    gate reads — and unblock paid runs. FREE / NONE get 403 from ``require_tier``.

    Capped at 500 USD to prevent runaway spend from a misconfigured client.
    Takes effect immediately — the next pipeline run reads the new value.
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        result = await conn.execute(
            """UPDATE subscriptions SET monthly_cost_cap_usd = $1
               WHERE workspace_id = $2::uuid""",
            body.monthly_cost_cap_usd,
            ws.workspace_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    return {"monthly_cost_cap_usd": body.monthly_cost_cap_usd}
