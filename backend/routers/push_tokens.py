"""Push token registration endpoints.

The mobile client calls POST /v1/push-tokens after obtaining an FCM registration token
and storing it server-side so gate and run-completion notifications can reach the
device. On logout the client calls DELETE /v1/push-tokens/{token} to deregister.

FCM only (ST-13): 'platform: apns' is refused with a 422 — the delivery path
(backend/push.py) only ever sends to 'fcm' tokens, so an APNs registration was
previously a dead row. iOS registers its FCM token; Firebase Messaging handles APNs
delivery underneath.

Each token is scoped to the authenticated workspace (RLS-enforced).
UNIQUE(workspace_id, token) means re-registering the same token is idempotent.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/push-tokens", tags=["push"], responses=ERROR_RESPONSES)


class RegisterTokenRequest(BaseModel):
    token: str
    platform: Literal["fcm"]


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_token(
    body: RegisterTokenRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, str]:
    """Register a device push token for the authenticated workspace.

    Idempotent: registering the same token twice is a no-op (UPSERT).
    Call this on every app launch in case the OS rotated the token.

    Tokens are scoped to the authenticated workspace. There is no per-workspace limit.
    Invalid/dead tokens reported by FCM are automatically pruned server-side.

    Push notifications are purely hints and contain NO customer PII, prospect data, or draft text.
    They are sent when a run hits an approval gate, or finishes (ok/failed).
    The payload `data` object contains `run_id`, the terminal `status`, or the `gate` kind.
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
