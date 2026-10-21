"""Integrations API for BYOK third-party credentials.

Allows the mobile app to securely supply API keys for external services.
Keys are encrypted at rest using envelope encryption (V003__credential_vault.sql).
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from limits import parse as _parse_rate_limit
from pydantic import BaseModel, Field

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..ratelimit import limiter
from ..schemas import ERROR_RESPONSES
from ..services.integrations import upsert_credential
from ..services.integrations_verify import verify_provider_credential
from ..vault import decrypt, get_kek

router = APIRouter(prefix="/integrations", tags=["integrations"], responses=ERROR_RESPONSES)

_VERIFY_RATE_ITEM = _parse_rate_limit("10/minute")


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


@router.post("/{provider}/verify", status_code=status.HTTP_200_OK)
async def verify_integration(
    provider: ProviderType,
    body: IntegrationPayload,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict[str, Any]:
    """Verify an external integration credential without saving it."""
    if limiter.enabled:
        key = f"ws:{ws.workspace_id}:verify"
        if not limiter.limiter.hit(_VERIFY_RATE_ITEM, key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "rate_limit_exceeded",
                    "message": "Too many verification requests for this workspace. Limit is 10 per minute.",
                    "next_step": "Wait 60 seconds before testing credentials again.",
                },
            )
    res = await verify_provider_credential(provider, body.api_key)
    if res.status == "valid":
        out: dict[str, Any] = {"valid": True, "provider": provider}
        if res.credits_remaining is not None:
            out["credits_remaining"] = res.credits_remaining
        if res.plan:
            out["plan"] = res.plan
        return out
    elif res.status == "credits_exhausted":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "provider_quota_exhausted",
                "message": res.reason or "Account has 0 lookup credits remaining",
                "credits_remaining": 0,
                "next_step": "Check your credit balance in your provider dashboard and top up credits.",
            },
        )
    elif res.status == "invalid_key":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_provider_key",
                "message": res.reason or f"Invalid API key for {provider}",
                "next_step": "Check the API key in your provider dashboard and paste a valid active key.",
            },
        )
    else:  # provider_unreachable
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "provider_unreachable",
                "message": res.reason or f"Unable to reach {provider} API",
                "next_step": "Check your network or provider status, or save without validation if you wish to proceed.",
            },
        )


@router.put("/{provider}", status_code=status.HTTP_200_OK)
async def set_integration(
    provider: ProviderType,
    body: IntegrationPayload,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    verify: bool = Query(True),
) -> dict[str, Any]:
    """Configure or update a third-party integration credential."""
    warning = None
    if verify:
        res = await verify_provider_credential(provider, body.api_key)
        if res.status == "credits_exhausted":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "provider_quota_exhausted",
                    "message": res.reason or "Account has 0 lookup credits remaining",
                    "credits_remaining": 0,
                    "next_step": "Check your credit balance in your provider dashboard and top up credits.",
                },
            )
        elif res.status == "invalid_key":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_provider_key",
                    "message": res.reason or f"Invalid API key for {provider}",
                    "next_step": "Check the API key in your provider dashboard and paste a valid active key.",
                },
            )
        elif res.status == "provider_unreachable":
            warning = "provider_unreachable"

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
        await request.app.state.sessions.evict_workspace(str(ws.workspace_id), close_in_use=False)
    from ..broker import publish_workspace_evict

    await publish_workspace_evict(str(ws.workspace_id), reason="credentials_rotated")

    resp: dict[str, Any] = {"status": "configured", "provider": provider}
    if warning:
        resp["warning"] = warning
    return resp


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
        await request.app.state.sessions.evict_workspace(str(ws.workspace_id), close_in_use=False)
    from ..broker import publish_workspace_evict

    await publish_workspace_evict(str(ws.workspace_id), reason="credentials_rotated")

    return {"status": "deleted"}
