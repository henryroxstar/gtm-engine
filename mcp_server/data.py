"""Data access tools for MCP clients to query PostgreSQL state directly."""

from __future__ import annotations

from typing import Any

from backend.services import accounts as accounts_svc
from backend.services import suppression as suppression_svc

from .auth import ApiKeyCtx


async def get_prospect_account_task(
    *,
    key_ctx: ApiKeyCtx,
    pool: Any,
    account_id: str | None = None,
    slug: str | None = None,
) -> dict:
    """Retrieve an account record from the workspace's PostgreSQL database."""
    if not account_id and not slug:
        raise ValueError("Either account_id or slug must be provided")

    if account_id:
        acc = await accounts_svc.get_account(pool, key_ctx.workspace_id, account_id)
    else:
        acc = await accounts_svc.get_account_by_slug(pool, key_ctx.workspace_id, slug or "")
    return acc or {}


async def list_prospect_accounts_task(
    *,
    key_ctx: ApiKeyCtx,
    pool: Any,
    status: str | None = None,
    tier: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """List prospect accounts in the workspace's PostgreSQL database."""
    return await accounts_svc.list_accounts(
        pool, key_ctx.workspace_id, status=status, tier=tier, limit=limit
    )


async def check_suppression_task(
    *,
    key_ctx: ApiKeyCtx,
    pool: Any,
    email: str | None = None,
    name: str | None = None,
    company_domain: str | None = None,
) -> dict:
    """Check if an email or person is on the workspace's suppression/DNC list."""
    suppressed, reason = await suppression_svc.is_suppressed(
        pool,
        key_ctx.workspace_id,
        email=email,
        name=name,
        company_domain=company_domain,
    )
    return {"suppressed": suppressed, "reason": reason}
