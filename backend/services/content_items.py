"""Database service for tenant content pipeline items."""

from __future__ import annotations

import json
from typing import Any

from ..database import workspace_scope


async def get_content_item(pool: Any, workspace_id: str, item_id: str) -> dict[str, Any] | None:
    """Fetch a content item by item_id within a workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM tenant_content_items
            WHERE workspace_id = $1::uuid AND item_id = $2
            """,
            workspace_id,
            item_id,
        )
    return dict(row) if row else None


async def upsert_content_item(pool: Any, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Upsert a content item (plan item or drafted asset)."""
    brief_json = json.dumps(data.get("brief") or {})
    meta_json = json.dumps(data.get("meta") or {})
    asset_refs = data.get("asset_refs") or []

    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tenant_content_items (
                workspace_id, item_id, plan_id, pillar, story_id,
                platform, format, slot, status, hook_id, hook, body,
                brief, asset_refs, meta, updated_at
            ) VALUES (
                $1::uuid, $2, $3, $4, $5,
                $6, $7, $8, COALESCE($9, 'planned'), $10, $11, $12,
                $13::jsonb, $14, $15::jsonb, now()
            )
            ON CONFLICT (workspace_id, item_id) DO UPDATE SET
                plan_id = COALESCE(EXCLUDED.plan_id, tenant_content_items.plan_id),
                pillar = COALESCE(EXCLUDED.pillar, tenant_content_items.pillar),
                story_id = COALESCE(EXCLUDED.story_id, tenant_content_items.story_id),
                platform = COALESCE(EXCLUDED.platform, tenant_content_items.platform),
                format = COALESCE(EXCLUDED.format, tenant_content_items.format),
                slot = COALESCE(EXCLUDED.slot, tenant_content_items.slot),
                status = COALESCE(EXCLUDED.status, tenant_content_items.status),
                hook_id = COALESCE(EXCLUDED.hook_id, tenant_content_items.hook_id),
                hook = COALESCE(EXCLUDED.hook, tenant_content_items.hook),
                body = COALESCE(EXCLUDED.body, tenant_content_items.body),
                brief = tenant_content_items.brief || EXCLUDED.brief,
                asset_refs = ARRAY(SELECT DISTINCT unnest(tenant_content_items.asset_refs || EXCLUDED.asset_refs)),
                meta = tenant_content_items.meta || EXCLUDED.meta,
                updated_at = now()
            RETURNING *
            """,
            workspace_id,
            data["item_id"] or data["id"],
            data.get("plan_id"),
            data.get("pillar"),
            data.get("story_id"),
            data.get("platform", "generic"),
            data.get("format"),
            data.get("slot"),
            data.get("status"),
            data.get("hook_id"),
            data.get("hook"),
            data.get("body"),
            brief_json,
            asset_refs,
            meta_json,
        )
    if not row:
        raise RuntimeError(f"Failed to upsert content item {data.get('item_id')}")
    return dict(row)


async def list_content_items(
    pool: Any,
    workspace_id: str,
    *,
    plan_id: str | None = None,
    status: str | None = None,
    platform: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List content items with optional filters."""
    query = "SELECT * FROM tenant_content_items WHERE workspace_id = $1::uuid"
    params: list[Any] = [workspace_id]

    if plan_id:
        params.append(plan_id)
        query += f" AND plan_id = ${len(params)}"
    if status:
        params.append(status)
        query += f" AND status = ${len(params)}"
    if platform:
        params.append(platform)
        query += f" AND platform = ${len(params)}"

    query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
    params.extend([limit, offset])

    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


async def update_content_status(
    pool: Any,
    workspace_id: str,
    item_id: str,
    status: str,
) -> dict[str, Any] | None:
    """Update status of a content item."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            UPDATE tenant_content_items
            SET status = $3, updated_at = now()
            WHERE workspace_id = $1::uuid AND item_id = $2
            RETURNING *
            """,
            workspace_id,
            item_id,
            status,
        )
    return dict(row) if row else None
