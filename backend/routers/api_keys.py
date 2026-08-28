"""API key CRUD — MCP server and direct backend access.

POST   /v1/api-keys          — create; raw key returned ONCE, never stored
GET    /v1/api-keys          — list (prefix + metadata; no hashes)
DELETE /v1/api-keys/{key_id} — revoke (sets revoked_at; row kept for audit trail)

Entitlement on the key is capped at the workspace's current subscription
entitlement so a key cannot grant more access than the workspace holds.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import ApiKeyCreateRequest, ApiKeyCreateResponse, ApiKeyResponse

router = APIRouter(prefix="/api-keys", tags=["api-keys"])

_ENTITLEMENT_ORDER: dict[str, int] = {"free": 0, "pro": 1, "pro_plus": 2}


def _cap_entitlement(requested: str, workspace_entitlement: str) -> str:
    """Return `requested` unless it exceeds `workspace_entitlement`."""
    if _ENTITLEMENT_ORDER.get(requested, 0) > _ENTITLEMENT_ORDER.get(workspace_entitlement, 0):
        return workspace_entitlement
    return requested


@router.post("", response_model=ApiKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreateRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> ApiKeyCreateResponse:
    """Generate a new API key. The raw key is returned exactly once — store it now."""
    pool = request.app.state.pool

    raw_key = "sk-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    prefix = raw_key[:8]
    effective_entitlement = _cap_entitlement(body.entitlement, ws.entitlement.value)

    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO api_keys(workspace_id, key_hash, prefix, label, entitlement)
               VALUES($1::uuid, $2, $3, $4, $5)
               RETURNING id::text, prefix, label, entitlement,
                         last_used_at, created_at, revoked_at""",
            ws.workspace_id,
            key_hash,
            prefix,
            body.label,
            effective_entitlement,
        )

    return ApiKeyCreateResponse(
        id=row["id"],
        prefix=row["prefix"],
        label=row["label"],
        entitlement=row["entitlement"],
        last_used_at=row["last_used_at"].isoformat() if row["last_used_at"] else None,
        created_at=row["created_at"].isoformat(),
        is_revoked=row["revoked_at"] is not None,
        raw_key=raw_key,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> list[ApiKeyResponse]:
    """List all API keys for the authenticated workspace. Hashes are never returned."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        rows = await conn.fetch(
            """SELECT id::text, prefix, label, entitlement,
                      last_used_at, created_at, revoked_at
               FROM api_keys
               WHERE workspace_id = $1::uuid
               ORDER BY created_at DESC""",
            ws.workspace_id,
        )
    return [
        ApiKeyResponse(
            id=r["id"],
            prefix=r["prefix"],
            label=r["label"],
            entitlement=r["entitlement"],
            last_used_at=r["last_used_at"].isoformat() if r["last_used_at"] else None,
            created_at=r["created_at"].isoformat(),
            is_revoked=r["revoked_at"] is not None,
        )
        for r in rows
    ]


@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
async def revoke_api_key(
    key_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Revoke an API key. Sets revoked_at; the row is kept for the audit trail."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT revoked_at FROM api_keys WHERE id = $1::uuid AND workspace_id = $2::uuid",
            key_id,
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    if row["revoked_at"] is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "API key is already revoked")

    async with workspace_scope(pool, ws.workspace_id) as conn:
        await conn.execute(
            "UPDATE api_keys SET revoked_at = now() "
            "WHERE id = $1::uuid AND workspace_id = $2::uuid",
            key_id,
            ws.workspace_id,
        )
    return {"key_id": key_id, "status": "revoked"}
