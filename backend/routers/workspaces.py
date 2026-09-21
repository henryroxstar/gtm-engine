"""Workspace info and subscription endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from gtm_core.tiers import Tier

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth, require_tier
from ..schemas import (
    ERROR_RESPONSES,
    PatchCostCapRequest,
    PatchWorkspaceRequest,
    SubscriptionResponse,
    WorkspaceResponse,
)

router = APIRouter(prefix="/workspace", tags=["workspace"], responses=ERROR_RESPONSES)


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
                      s.entitlement, s.monthly_cost_cap_usd,
                      p.enabled AS publish_enabled,
                      p.schedule_enabled
               FROM workspaces w
               JOIN subscriptions s ON s.workspace_id = w.id
               LEFT JOIN workspace_publish_settings p ON p.workspace_id = w.id
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
        publish_enabled=bool(row["publish_enabled"])
        if row.get("publish_enabled") is not None
        else False,
        schedule_enabled=bool(row["schedule_enabled"])
        if row.get("schedule_enabled") is not None
        else False,
    )


@router.patch("", response_model=WorkspaceResponse)
async def patch_workspace(
    body: PatchWorkspaceRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> WorkspaceResponse:
    """Update the workspace display name."""
    display = body.display_name.strip()
    if not display:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="display_name cannot be empty",
        )
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """UPDATE workspaces
               SET display_name = $1
               WHERE id = $2::uuid
               RETURNING id::text, slug, display_name""",
            display,
            ws.workspace_id,
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
        sub = await conn.fetchrow(
            """SELECT s.entitlement, s.monthly_cost_cap_usd,
                      p.enabled AS publish_enabled,
                      p.schedule_enabled
               FROM subscriptions s
               LEFT JOIN workspace_publish_settings p ON p.workspace_id = s.workspace_id
               WHERE s.workspace_id = $1::uuid""",
            ws.workspace_id,
        )
    return WorkspaceResponse(
        id=row["id"],
        slug=row["slug"],
        display_name=row["display_name"],
        entitlement=sub["entitlement"] if sub else "free",
        monthly_cost_cap_usd=float(sub["monthly_cost_cap_usd"]) if sub else 0.0,
        publish_enabled=bool(sub["publish_enabled"])
        if sub and sub.get("publish_enabled") is not None
        else False,
        schedule_enabled=bool(sub["schedule_enabled"])
        if sub and sub.get("schedule_enabled") is not None
        else False,
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
        sub = await conn.fetchrow(
            """SELECT plan_cost_cap_usd FROM subscriptions
               WHERE workspace_id = $1::uuid""",
            ws.workspace_id,
        )
        if sub is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")

        plan_cap_val = sub["plan_cost_cap_usd"] if "plan_cost_cap_usd" in sub else None
        if plan_cap_val is not None:
            plan_cap = float(plan_cap_val)
            if plan_cap > 0 and body.monthly_cost_cap_usd > plan_cap:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {
                        "code": "cap_exceeds_plan",
                        "message": (
                            f"Requested cost cap ${body.monthly_cost_cap_usd:.2f} "
                            f"exceeds plan cap ${plan_cap:.2f}"
                        ),
                    },
                )

        result = await conn.execute(
            """UPDATE subscriptions SET monthly_cost_cap_usd = $1
               WHERE workspace_id = $2::uuid""",
            body.monthly_cost_cap_usd,
            ws.workspace_id,
        )
    if result == "UPDATE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    return {"monthly_cost_cap_usd": body.monthly_cost_cap_usd}
