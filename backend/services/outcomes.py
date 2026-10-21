"""Database service for tenant outcomes tracking and analytics."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..database import workspace_scope


async def append_outcome(pool: Any, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Append a single outcome record."""
    meta_json = json.dumps(data.get("meta") or {})
    tags = data.get("tags") or []
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tenant_outcomes (
                workspace_id, ts, channel, outcome, ref, account_slug, tags, value, meta
            ) VALUES (
                $1::uuid, COALESCE($2, now()), $3, $4, $5, $6, $7, COALESCE($8, 1), $9::jsonb
            )
            RETURNING *
            """,
            workspace_id,
            data.get("ts"),
            data["channel"],
            data["outcome"],
            data.get("ref"),
            data.get("account_slug") or data.get("account"),
            tags,
            data.get("value"),
            meta_json,
        )
    if not row:
        raise RuntimeError("Failed to append outcome")
    return dict(row)


async def list_outcomes(
    pool: Any,
    workspace_id: str,
    *,
    channel: str | None = None,
    outcome: str | None = None,
    ref: str | None = None,
    account_slug: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List outcomes with optional filtering."""
    query = "SELECT * FROM tenant_outcomes WHERE workspace_id = $1::uuid"
    params: list[Any] = [workspace_id]

    if channel:
        params.append(channel)
        query += f" AND channel = ${len(params)}"
    if outcome:
        params.append(outcome)
        query += f" AND outcome = ${len(params)}"
    if ref:
        params.append(ref)
        query += f" AND ref = ${len(params)}"
    if account_slug:
        params.append(account_slug)
        query += f" AND account_slug = ${len(params)}"

    query += f" ORDER BY ts DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
    params.extend([limit, offset])

    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


async def summarize_outcomes(
    pool: Any,
    workspace_id: str,
    *,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate outcomes by channel and outcome type."""
    query = """
        SELECT channel, outcome, SUM(value) as total, COUNT(*) as events
        FROM tenant_outcomes
        WHERE workspace_id = $1::uuid
    """
    params: list[Any] = [workspace_id]
    if since:
        params.append(since)
        query += f" AND ts >= ${len(params)}"

    query += " GROUP BY channel, outcome"

    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(query, *params)

    summary: dict[str, dict[str, float]] = {}
    for r in rows:
        channel = r["channel"]
        outcome = r["outcome"]
        total = float(r["total"] or 0)
        summary.setdefault(channel, {})[outcome] = total
    return summary
