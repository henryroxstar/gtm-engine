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
from pydantic import BaseModel, Field

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import (
    ERROR_RESPONSES,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyResponse,
)
from ..types import UuidStr

router = APIRouter(prefix="/api-keys", tags=["api-keys"], responses=ERROR_RESPONSES)

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
    """Generate a new API key. The raw key is returned exactly once — store it now.

    Fleet Phase A (V026): an optional `agent_id` binds the key to a gtm-engine agent —
    a run made with it becomes a "service" Principal (backend/callers/), narrowed to
    that agent's own pack subset/budget/cap. Still `require_auth`-only: a human mints
    keys, never a machine. The named agent must exist, be in THIS workspace (fetch_agent
    already filters on it — same pattern resolve_acting_agent uses), and be `active` or
    `paused` (a paused agent may still be bound ahead of being resumed) — NOT `archived`.
    """
    pool = request.app.state.pool

    if body.agent_id is not None:
        from ..agents import fetch_agent

        async with workspace_scope(pool, ws.workspace_id) as conn:
            agent_row = await fetch_agent(conn, ws.workspace_id, body.agent_id)
        if agent_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
        if agent_row["status"] not in ("active", "paused"):
            raise HTTPException(status.HTTP_409_CONFLICT, {"code": "agent_archived"})

    raw_key = "sk-" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    prefix = raw_key[:8]
    effective_entitlement = _cap_entitlement(body.entitlement, ws.entitlement.value)

    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """INSERT INTO api_keys(workspace_id, key_hash, prefix, label, entitlement, agent_id)
               VALUES($1::uuid, $2, $3, $4, $5, $6::uuid)
               RETURNING id::text, prefix, label, entitlement,
                         last_used_at, created_at, revoked_at, agent_id::text AS agent_id""",
            ws.workspace_id,
            key_hash,
            prefix,
            body.label,
            effective_entitlement,
            body.agent_id,
        )

    return ApiKeyCreateResponse(
        id=row["id"],
        prefix=row["prefix"],
        label=row["label"],
        entitlement=row["entitlement"],
        last_used_at=row["last_used_at"].isoformat() if row["last_used_at"] else None,
        created_at=row["created_at"].isoformat(),
        is_revoked=row["revoked_at"] is not None,
        agent_id=row.get("agent_id"),
        principal_kind="service" if row.get("agent_id") else "tool",
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
                      last_used_at, created_at, revoked_at, agent_id::text AS agent_id
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
            agent_id=r.get("agent_id"),
            principal_kind="service" if r.get("agent_id") else "tool",
        )
        for r in rows
    ]


@router.delete("/{key_id}", status_code=status.HTTP_200_OK)
async def revoke_api_key(
    key_id: UuidStr,
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
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            {"code": "api_key_not_found", "message": "API key not found"},
        )
    if row["revoked_at"] is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "api_key_already_revoked", "message": "API key is already revoked"},
        )

    async with workspace_scope(pool, ws.workspace_id) as conn:
        await conn.execute(
            "UPDATE api_keys SET revoked_at = now() "
            "WHERE id = $1::uuid AND workspace_id = $2::uuid",
            key_id,
            ws.workspace_id,
        )
    return {"key_id": key_id, "status": "revoked"}


class ApiKeyVerifyRequest(BaseModel):
    api_key: str | None = Field(default=None, description="Raw API key (sk-...) to verify")


class ApiKeyVerifyResponse(BaseModel):
    valid: bool
    workspace_id: str
    tier: str
    active: bool = True
    expires_at: str | None = None
    has_credits: bool = False
    balance_usd: float | None = None
    balance_credits: float | None = None


@router.post("/verify", response_model=ApiKeyVerifyResponse, status_code=status.HTTP_200_OK)
async def verify_api_key(
    request: Request,
    body: ApiKeyVerifyRequest | None = None,
) -> ApiKeyVerifyResponse:
    """Verify an API key and return its workspace identity and active subscription tier.

    Used by the Edge MCP Gateway to check key validity and populate edge KV cache.
    Accepts Bearer authorization header or JSON body {api_key: 'sk-...'}.
    """
    auth_header = request.headers.get("authorization", "")
    raw_key = ""
    if auth_header.lower().startswith("bearer "):
        raw_key = auth_header[7:].strip()
    elif body and body.api_key:
        raw_key = body.api_key.strip()

    if not raw_key or not raw_key.startswith("sk-"):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "invalid_key", "message": "Missing or invalid API key format"},
        )

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    pool = request.app.state.pool

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM resolve_api_key($1)", key_hash)

    if row is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "invalid_key", "message": "API key is invalid, expired, or revoked"},
        )

    ws_id = str(row["workspace_id"])
    tier = str(row["entitlement"])

    # Verify workspace subscription and wallet status
    async with workspace_scope(pool, ws_id) as conn:
        sub_row = await conn.fetchrow(
            "SELECT status, current_period_end, entitlement FROM subscriptions WHERE workspace_id = $1::uuid",
            ws_id,
        )
        wallet_row = await conn.fetchrow(
            "SELECT balance_usd, balance_credits FROM workspace_wallets WHERE workspace_id = $1::uuid",
            ws_id,
        )

    active = tier not in ("free", "")
    expires_at = None
    if sub_row:
        sub_tier = str(sub_row.get("entitlement") or tier)
        active = sub_row["status"] in ("active", "trialing") and sub_tier not in ("free", "")
        if sub_row["current_period_end"]:
            expires_at = sub_row["current_period_end"].isoformat()

    balance_usd: float | None = None
    balance_credits: float | None = None
    if wallet_row is not None:
        if wallet_row.get("balance_usd") is not None:
            balance_usd = float(wallet_row["balance_usd"])
        if wallet_row.get("balance_credits") is not None:
            balance_credits = float(wallet_row["balance_credits"])

    has_credits = (balance_usd is not None and balance_usd > 0.0) or (
        balance_credits is not None and balance_credits > 0.0
    )

    return ApiKeyVerifyResponse(
        valid=True,
        workspace_id=ws_id,
        tier=tier,
        active=active,
        expires_at=expires_at,
        has_credits=has_credits,
        balance_usd=balance_usd,
        balance_credits=balance_credits,
    )
