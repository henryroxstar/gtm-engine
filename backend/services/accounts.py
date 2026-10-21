"""Database service for tenant accounts."""

from __future__ import annotations

import json
from typing import Any

from ..database import workspace_scope


async def get_account(pool: Any, workspace_id: str, account_id: str) -> dict[str, Any] | None:
    """Fetch a single account by account_id within a workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT id, workspace_id, account_id, domain, company_name, slug,
                   status, tier, score, priority, owner, segment, why_now,
                   signals, notes, source_run, last_touched, created_at, updated_at
            FROM tenant_accounts
            WHERE workspace_id = $1::uuid AND account_id = $2
            """,
            workspace_id,
            account_id,
        )
    return dict(row) if row else None


async def get_account_by_slug(pool: Any, workspace_id: str, slug: str) -> dict[str, Any] | None:
    """Fetch an account by company slug within a workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT id, workspace_id, account_id, domain, company_name, slug,
                   status, tier, score, priority, owner, segment, why_now,
                   signals, notes, source_run, last_touched, created_at, updated_at
            FROM tenant_accounts
            WHERE workspace_id = $1::uuid AND slug = $2
            """,
            workspace_id,
            slug,
        )
    return dict(row) if row else None


async def list_accounts(
    pool: Any,
    workspace_id: str,
    *,
    status: str | None = None,
    tier: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List accounts in a workspace with optional status and tier filters."""
    query = """
        SELECT id, workspace_id, account_id, domain, company_name, slug,
               status, tier, score, priority, owner, segment, why_now,
               signals, notes, source_run, last_touched, created_at, updated_at
        FROM tenant_accounts
        WHERE workspace_id = $1::uuid
    """
    params: list[Any] = [workspace_id]

    if status:
        params.append(status)
        query += f" AND status = ${len(params)}"
    if tier:
        params.append(tier)
        query += f" AND tier = ${len(params)}"

    query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
    params.extend([limit, offset])

    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


async def upsert_account(pool: Any, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Upsert a single account record.

    Preserves sticky fields (status, priority, notes, owner) on conflict unless explicitly provided.
    """
    signals_json = json.dumps(data.get("signals") or {})
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tenant_accounts (
                workspace_id, account_id, domain, company_name, slug,
                status, tier, score, priority, owner, segment, why_now,
                signals, notes, source_run, last_touched, updated_at
            ) VALUES (
                $1::uuid, $2, $3, $4, $5,
                COALESCE($6, 'new'), $7, $8, $9, $10, $11, $12,
                $13::jsonb, $14, $15, COALESCE($16, now()), now()
            )
            ON CONFLICT (workspace_id, account_id) DO UPDATE SET
                domain = COALESCE(EXCLUDED.domain, tenant_accounts.domain),
                company_name = COALESCE(EXCLUDED.company_name, tenant_accounts.company_name),
                slug = COALESCE(EXCLUDED.slug, tenant_accounts.slug),
                status = CASE
                    WHEN EXCLUDED.status IS NOT NULL AND EXCLUDED.status != 'new' THEN EXCLUDED.status
                    ELSE tenant_accounts.status
                END,
                tier = COALESCE(EXCLUDED.tier, tenant_accounts.tier),
                score = COALESCE(EXCLUDED.score, tenant_accounts.score),
                priority = COALESCE(EXCLUDED.priority, tenant_accounts.priority),
                owner = COALESCE(EXCLUDED.owner, tenant_accounts.owner),
                segment = COALESCE(EXCLUDED.segment, tenant_accounts.segment),
                why_now = COALESCE(EXCLUDED.why_now, tenant_accounts.why_now),
                signals = tenant_accounts.signals || EXCLUDED.signals,
                notes = COALESCE(EXCLUDED.notes, tenant_accounts.notes),
                source_run = COALESCE(EXCLUDED.source_run, tenant_accounts.source_run),
                last_touched = now(),
                updated_at = now()
            RETURNING *
            """,
            workspace_id,
            data["account_id"],
            data.get("domain"),
            data["company_name"],
            data["slug"],
            data.get("status"),
            data.get("tier"),
            data.get("score"),
            data.get("priority"),
            data.get("owner"),
            data.get("segment"),
            data.get("why_now"),
            signals_json,
            data.get("notes"),
            data.get("source_run"),
            data.get("last_touched"),
        )
    if not row:
        raise RuntimeError(f"Failed to upsert account {data.get('account_id')}")
    return dict(row)


async def set_account_status(
    pool: Any,
    workspace_id: str,
    account_id: str,
    status: str,
    *,
    notes: str | None = None,
) -> dict[str, Any] | None:
    """Explicitly transition an account's status."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE tenant_accounts
            SET status = $3,
                notes = COALESCE($4, notes),
                last_touched = now(),
                updated_at = now()
            WHERE workspace_id = $1::uuid AND account_id = $2
            RETURNING *
            """,
            workspace_id,
            account_id,
            status,
            notes,
        )
    return dict(row) if row else None
