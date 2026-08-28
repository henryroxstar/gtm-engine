"""Push token registration endpoints.

The mobile client calls POST /v1/push-tokens after obtaining a device token from
FCM or APNs and storing it server-side so gate notifications can reach the device.
On logout the client calls DELETE /v1/push-tokens/{token} to deregister.

Each token is scoped to the authenticated workspace (RLS-enforced).
UNIQUE(workspace_id, token) means re-registering the same token is idempotent.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth

router = APIRouter(prefix="/push-tokens", tags=["push"])


class RegisterTokenRequest(BaseModel):
    token: str
    platform: Literal["apns", "fcm"]


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_token(
    body: RegisterTokenRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, str]:
    """Register a device push token for the authenticated workspace.

    Idempotent: registering the same token twice is a no-op (UPSERT).
    Call this on every app launch in case the OS rotated the token.
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        await conn.execute(
            """
            INSERT INTO push_tokens (workspace_id, user_id, token, platform)
            VALUES ($1::uuid, $2::uuid, $3, $4)
            ON CONFLICT (workspace_id, token) DO NOTHING
            """,
            ws.workspace_id,
            ws.user_id,
            body.token,
            body.platform,
        )
    return {"status": "registered", "platform": body.platform}


@router.get("", status_code=status.HTTP_200_OK)
async def list_tokens(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> list[dict]:
    """List all registered push tokens for the authenticated workspace."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        rows = await conn.fetch(
            """SELECT id::text, token, platform, created_at
               FROM push_tokens
               WHERE workspace_id = $1::uuid
               ORDER BY created_at DESC""",
            ws.workspace_id,
        )
    return [
        {
            "id": r["id"],
            "token": r["token"],
            "platform": r["platform"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.delete("/{token}", status_code=status.HTTP_200_OK)
async def deregister_token(
    token: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, str]:
    """Deregister a push token. Call on logout to stop receiving notifications."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        result = await conn.execute(
            "DELETE FROM push_tokens WHERE workspace_id = $1::uuid AND token = $2",
            ws.workspace_id,
            token,
        )
    if result == "DELETE 0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    return {"status": "deregistered"}
