"""Integrations API for BYOK third-party credentials.

Allows the mobile app to securely supply API keys for external services.
Keys are encrypted at rest using envelope encryption (V003__credential_vault.sql).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import ERROR_RESPONSES
from ..services.integrations import upsert_credential
from ..vault import decrypt, get_kek

router = APIRouter(prefix="/integrations", tags=["integrations"], responses=ERROR_RESPONSES)


class IntegrationPayload(BaseModel):
    api_key: str = Field(..., min_length=1)

    model_config = {"str_strip_whitespace": True}


# Supported BYOK providers
ProviderType = Literal["saleshandy", "apollo", "rocketreach", "syften"]


@router.get("", status_code=status.HTTP_200_OK)
async def list_integrations(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> list[dict]:
    """List all configured integrations.

    Never returns the plaintext keys — only metadata and configuration status.
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        rows = await conn.fetch(
            """
            SELECT provider, account_ref, created_at, rotated_at, encrypted_data, wrapped_dek, iv, tag, key_version
            FROM encrypted_credentials
            WHERE workspace_id = $1::uuid
            ORDER BY created_at DESC
            """,
            ws.workspace_id,
        )

    kek = get_kek()
    results = []
    for r in rows:
        status = "configured"
        if not kek:
            status = "unreadable"
        else:
            try:
                decrypt(dict(r), kek)
            except Exception:
                status = "unreadable"

        results.append(
            {
                "provider": r["provider"],
                "account_ref": r["account_ref"],
                "created_at": r["created_at"].isoformat(),
                "rotated_at": r["rotated_at"].isoformat() if r["rotated_at"] else None,
                "status": status,
            }
        )
    return results


@router.put("/{provider}", status_code=status.HTTP_200_OK)
async def set_integration(
    provider: ProviderType,
    body: IntegrationPayload,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, str]:
    """Configure or update a third-party integration credential."""
    pool = request.app.state.pool

    try:
        await upsert_credential(
            pool,
            str(ws.workspace_id),
            provider,
            "default",
            body.model_dump(),
        )
    except ValueError as e:
        # e.g., VAULT_KEK is not configured
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e

    # Cache Invalidation: Evict any warm agent sessions so the next run uses the new keys
    if hasattr(request.app.state, "sessions"):
        await request.app.state.sessions.evict_workspace(str(ws.workspace_id))

    return {"status": "configured", "provider": provider}


@router.delete("/{provider}", status_code=status.HTTP_200_OK)
async def delete_integration(
    provider: ProviderType,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, str]:
    """Remove a configured integration."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        result = await conn.execute(
            "DELETE FROM encrypted_credentials WHERE workspace_id = $1::uuid AND provider = $2",
            ws.workspace_id,
            provider,
        )

    if result == "DELETE 0":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "integration_not_found", "message": "Integration not found"},
        )

    # Cache Invalidation: Evict warm agent sessions
    if hasattr(request.app.state, "sessions"):
        await request.app.state.sessions.evict_workspace(str(ws.workspace_id))

    return {"status": "deleted"}
