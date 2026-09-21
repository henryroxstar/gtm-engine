"""Database service for tenant suppression ledger."""

from __future__ import annotations

from typing import Any

from ..database import workspace_scope


async def is_suppressed(
    pool: Any,
    workspace_id: str,
    *,
    email: str | None = None,
    name: str | None = None,
    company_domain: str | None = None,
) -> tuple[bool, str | None]:
    """Check if an email or person (name + domain) is suppressed.

    Returns (True, reason) if suppressed, else (False, None).
    """
    conditions: list[str] = []
    params: list[Any] = [workspace_id]

    if email and email.strip():
        params.append(email.strip().lower())
        conditions.append(f"lower(email) = ${len(params)}")

    if name and company_domain and name.strip() and company_domain.strip():
        params.append(name.strip().lower())
        params.append(company_domain.strip().lower())
        conditions.append(
            f"(lower(name) = ${len(params) - 1} AND lower(company_domain) = ${len(params)})"
        )

    if not conditions:
        return False, None

    query = f"""  # nosec B608
        SELECT reason FROM tenant_suppression
        WHERE workspace_id = $1::uuid AND ({" OR ".join(conditions)})
        LIMIT 1  # nosec B608
    """
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(query, *params)

    if row:
        return True, row["reason"]
    return False, None


async def add_suppression(
    pool: Any,
    workspace_id: str,
    *,
    email: str | None = None,
    name: str | None = None,
    company_domain: str | None = None,
    reason: str,
    date_str: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Add an entry to the suppression list."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tenant_suppression (
                workspace_id, email, name, company_domain, reason, date, note
            ) VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7
            )
            RETURNING *
            """,
            workspace_id,
            email.strip().lower() if email else None,
            name.strip() if name else None,
            company_domain.strip().lower() if company_domain else None,
            reason,
            date_str,
            note,
        )
    if not row:
        raise RuntimeError("Failed to add suppression entry")
    return dict(row)


async def list_suppressions(
    pool: Any,
    workspace_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List suppression entries for a workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM tenant_suppression
            WHERE workspace_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3
            """,
            workspace_id,
            limit,
            offset,
        )
    return [dict(r) for r in rows]
