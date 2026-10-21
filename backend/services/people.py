"""Database service for tenant people & engagement tracking."""

from __future__ import annotations

import json
from typing import Any

from ..database import workspace_scope

_STATUS_RANK = {"lead": 0, "engaged": 1, "replied": 2, "opportunity": 3, "account": 4}


async def get_person(pool: Any, workspace_id: str, person_id: str) -> dict[str, Any] | None:
    """Fetch a person and their engagement list within a workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        person = await conn.fetchrow(
            """
            SELECT * FROM tenant_people
            WHERE workspace_id = $1::uuid AND person_id = $2
            """,
            workspace_id,
            person_id,
        )
        if not person:
            return None

        engagements = await conn.fetch(
            """
            SELECT * FROM tenant_engagements
            WHERE workspace_id = $1::uuid AND person_id = $2
            ORDER BY date DESC NULLS LAST
            """,
            workspace_id,
            person_id,
        )

    res = dict(person)
    res["engagements"] = [dict(e) for e in engagements]
    return res


async def upsert_person(pool: Any, workspace_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Upsert a person record, respecting non-downgrade status rules and appending engagements."""
    person_id = record["person_id"]
    new_status = record.get("status", "lead")
    tags = record.get("tags") or []
    raw_fields = json.dumps(record.get("raw_fields") or {})

    async with workspace_scope(pool, workspace_id) as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT * FROM tenant_people WHERE workspace_id = $1::uuid AND person_id = $2",
                workspace_id,
                person_id,
            )

            if existing:
                current_status = existing["status"]
                # Enforce non-downgrade status rule
                if current_status == "disqualified" and new_status != "disqualified":
                    final_status = "disqualified"
                elif (
                    current_status in _STATUS_RANK
                    and new_status in _STATUS_RANK
                    and _STATUS_RANK[new_status] < _STATUS_RANK[current_status]
                ):
                    final_status = current_status
                else:
                    final_status = new_status

                row = await conn.fetchrow(
                    """
                    UPDATE tenant_people
                    SET name = COALESCE(tenant_people.name, $3),
                        headline = COALESCE(tenant_people.headline, $4),
                        company = COALESCE(tenant_people.company, $5),
                        profile_url = COALESCE(tenant_people.profile_url, $6),
                        tags = ARRAY(SELECT DISTINCT unnest(tenant_people.tags || $7::text[])),
                        status = $8,
                        linked_account = COALESCE(tenant_people.linked_account, $9),
                        raw_fields = tenant_people.raw_fields || $10::jsonb,
                        updated_at = now()
                    WHERE workspace_id = $1::uuid AND person_id = $2
                    RETURNING *
                    """,
                    workspace_id,
                    person_id,
                    record.get("name"),
                    record.get("headline"),
                    record.get("company"),
                    record.get("profile_url"),
                    tags,
                    final_status,
                    record.get("linked_account"),
                    raw_fields,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO tenant_people (
                        workspace_id, person_id, name, headline, company,
                        profile_url, tags, status, linked_account, raw_fields
                    ) VALUES (
                        $1::uuid, $2, $3, $4, $5,
                        $6, $7, $8, $9, $10::jsonb
                    )
                    RETURNING *
                    """,
                    workspace_id,
                    person_id,
                    record.get("name"),
                    record.get("headline"),
                    record.get("company"),
                    record.get("profile_url"),
                    tags,
                    new_status,
                    record.get("linked_account"),
                    raw_fields,
                )

            ev = record.get("engagement")
            if ev:
                await conn.execute(
                    """
                    INSERT INTO tenant_engagements (
                        workspace_id, person_id, type, post_url, date, notes, meta
                    ) VALUES (
                        $1::uuid, $2, $3, $4, COALESCE($5, now()), $6, $7::jsonb
                    )
                    """,
                    workspace_id,
                    person_id,
                    ev["type"],
                    ev.get("post_url"),
                    ev.get("date"),
                    ev.get("notes"),
                    json.dumps(ev.get("meta") or {}),
                )
                # Refresh count and seen dates
                await conn.execute(
                    """
                    UPDATE tenant_people
                    SET engagement_count = (
                            SELECT count(*) FROM tenant_engagements
                            WHERE workspace_id = $1::uuid AND person_id = $2
                        ),
                        first_seen = (
                            SELECT min(date) FROM tenant_engagements
                            WHERE workspace_id = $1::uuid AND person_id = $2
                        ),
                        last_seen = (
                            SELECT max(date) FROM tenant_engagements
                            WHERE workspace_id = $1::uuid AND person_id = $2
                        ),
                        updated_at = now()
                    WHERE workspace_id = $1::uuid AND person_id = $2
                    """,
                    workspace_id,
                    person_id,
                )

    if not row:
        raise RuntimeError(f"Failed to upsert person {person_id}")
    return dict(row)


async def list_people(
    pool: Any,
    workspace_id: str,
    *,
    status: str | None = None,
    tag: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List people in a workspace."""
    query = "SELECT * FROM tenant_people WHERE workspace_id = $1::uuid"
    params: list[Any] = [workspace_id]

    if status:
        params.append(status)
        query += f" AND status = ${len(params)}"
    if tag:
        params.append(tag)
        query += f" AND ${len(params)} = ANY(tags)"

    query += f" ORDER BY updated_at DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
    params.extend([limit, offset])

    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]
