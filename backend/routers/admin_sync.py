"""API router for safe, authenticated admin state synchronization."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import ERROR_RESPONSES
from ..services import (
    accounts as accounts_svc,
)
from ..services import (
    contacts as contacts_svc,
)
from ..services import (
    content_items as content_svc,
)
from ..services import (
    outcomes as outcomes_svc,
)
from ..services import (
    people as people_svc,
)
from ..services import (
    suppression as suppression_svc,
)

router = APIRouter(prefix="/admin", tags=["admin"], responses=ERROR_RESPONSES)


class AdminSyncPayload(BaseModel):
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    suppressions: list[dict[str, Any]] = Field(default_factory=list)
    people: list[dict[str, Any]] = Field(default_factory=list)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)
    content_items: list[dict[str, Any]] = Field(default_factory=list)


class AdminSyncResponse(BaseModel):
    status: str
    accounts_synced: int
    contacts_synced: int
    suppressions_synced: int
    people_synced: int
    outcomes_synced: int
    content_items_synced: int


@router.post("/sync", response_model=AdminSyncResponse)
async def sync_admin_state(
    payload: AdminSyncPayload,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> AdminSyncResponse:
    """Safely upsert local admin/operator state into the workspace's PostgreSQL database."""
    pool = request.app.state.pool

    # Verify admin or owner role in the workspace
    async with workspace_scope(pool, ws.workspace_id) as conn:
        role_row = await conn.fetchrow(
            """
            SELECT role FROM workspace_members
            WHERE workspace_id = $1::uuid AND user_id = $2::uuid
            """,
            ws.workspace_id,
            ws.user_id,
        )
    if not role_row or role_row["role"] not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace owners or admins can perform state sync",
        )

    acc_cnt = 0
    for acc in payload.accounts:
        await accounts_svc.upsert_account(pool, ws.workspace_id, acc)
        acc_cnt += 1

    con_cnt = 0
    for con in payload.contacts:
        await contacts_svc.upsert_contact(pool, ws.workspace_id, con)
        con_cnt += 1

    sup_cnt = 0
    for sup in payload.suppressions:
        reason = sup.get("reason")
        if reason:
            await suppression_svc.add_suppression(
                pool,
                ws.workspace_id,
                email=sup.get("email"),
                name=sup.get("name"),
                company_domain=sup.get("company_domain"),
                reason=reason,
                date_str=sup.get("date"),
                note=sup.get("note"),
            )
            sup_cnt += 1

    peo_cnt = 0
    for peo in payload.people:
        await people_svc.upsert_person(pool, ws.workspace_id, peo)
        peo_cnt += 1

    out_cnt = 0
    for out in payload.outcomes:
        await outcomes_svc.append_outcome(pool, ws.workspace_id, out)
        out_cnt += 1

    cnt_cnt = 0
    for it in payload.content_items:
        await content_svc.upsert_content_item(pool, ws.workspace_id, it)
        cnt_cnt += 1

    return AdminSyncResponse(
        status="ok",
        accounts_synced=acc_cnt,
        contacts_synced=con_cnt,
        suppressions_synced=sup_cnt,
        people_synced=peo_cnt,
        outcomes_synced=out_cnt,
        content_items_synced=cnt_cnt,
    )
