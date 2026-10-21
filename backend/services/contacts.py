"""Database service for tenant contacts / lead pool."""

from __future__ import annotations

import json
from typing import Any

from ..database import workspace_scope


async def get_contact(pool: Any, workspace_id: str, pool_row_id: str) -> dict[str, Any] | None:
    """Fetch a single contact by pool_row_id within a workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM tenant_contacts
            WHERE workspace_id = $1::uuid AND pool_row_id = $2
            """,
            workspace_id,
            pool_row_id,
        )
    return dict(row) if row else None


async def get_contact_by_email(pool: Any, workspace_id: str, email: str) -> dict[str, Any] | None:
    """Fetch a contact by email within a workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM tenant_contacts
            WHERE workspace_id = $1::uuid AND lower(email) = lower($2)
            """,
            workspace_id,
            email,
        )
    return dict(row) if row else None


async def list_contacts(
    pool: Any,
    workspace_id: str,
    *,
    account_id: str | None = None,
    tier: str | None = None,
    lane: str | None = None,
    verdict: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List contacts with optional filtering."""
    query = "SELECT * FROM tenant_contacts WHERE workspace_id = $1::uuid"
    params: list[Any] = [workspace_id]

    if account_id:
        params.append(account_id)
        query += f" AND account_id = ${len(params)}"
    if tier:
        params.append(tier)
        query += f" AND tier = ${len(params)}"
    if lane:
        params.append(lane)
        query += f" AND lane = ${len(params)}"
    if verdict:
        params.append(verdict)
        query += f" AND verdict = ${len(params)}"

    query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}"
    params.extend([limit, offset])

    async with workspace_scope(pool, workspace_id) as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


async def upsert_contact(pool: Any, workspace_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Upsert a single contact row."""
    raw_fields_json = json.dumps(data.get("raw_fields") or {})
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tenant_contacts (
                workspace_id, account_id, pool_row_id, email,
                first_name, last_name, title, company, company_domain,
                city, country, segment, tier, score, conf, conf_tier,
                email_status, why_now, case_study, src, heat,
                top_intent_score, intent_topics, cohort, qualification_path,
                signal_source_url, signal_observed, signal_evidence,
                signal_subject, signal_agent_kind, category_relation,
                verdict, verdict_reason, signal_column, suppression,
                suppression_date, judge_verdict, judge_verdict_reason,
                judge_calibrated, judge_defect_class, lane, lane_reason,
                raw_fields, updated_at
            ) VALUES (
                $1::uuid, $2, $3, $4,
                $5, $6, $7, $8, $9,
                $10, $11, $12, $13, $14, $15, $16,
                $17, $18, $19, $20, $21,
                $22, $23, $24, $25,
                $26, $27, $28,
                $29, $30, $31,
                $32, $33, $34, $35,
                $36, $37, $38,
                $39, $40, $41, $42,
                $43::jsonb, now()
            )
            ON CONFLICT (workspace_id, pool_row_id) DO UPDATE SET
                account_id = COALESCE(EXCLUDED.account_id, tenant_contacts.account_id),
                email = COALESCE(EXCLUDED.email, tenant_contacts.email),
                first_name = COALESCE(EXCLUDED.first_name, tenant_contacts.first_name),
                last_name = COALESCE(EXCLUDED.last_name, tenant_contacts.last_name),
                title = COALESCE(EXCLUDED.title, tenant_contacts.title),
                company = COALESCE(EXCLUDED.company, tenant_contacts.company),
                company_domain = COALESCE(EXCLUDED.company_domain, tenant_contacts.company_domain),
                city = COALESCE(EXCLUDED.city, tenant_contacts.city),
                country = COALESCE(EXCLUDED.country, tenant_contacts.country),
                segment = COALESCE(EXCLUDED.segment, tenant_contacts.segment),
                tier = COALESCE(EXCLUDED.tier, tenant_contacts.tier),
                score = COALESCE(EXCLUDED.score, tenant_contacts.score),
                conf = COALESCE(EXCLUDED.conf, tenant_contacts.conf),
                conf_tier = COALESCE(EXCLUDED.conf_tier, tenant_contacts.conf_tier),
                email_status = COALESCE(EXCLUDED.email_status, tenant_contacts.email_status),
                why_now = COALESCE(EXCLUDED.why_now, tenant_contacts.why_now),
                case_study = COALESCE(EXCLUDED.case_study, tenant_contacts.case_study),
                src = COALESCE(EXCLUDED.src, tenant_contacts.src),
                heat = COALESCE(EXCLUDED.heat, tenant_contacts.heat),
                top_intent_score = COALESCE(EXCLUDED.top_intent_score, tenant_contacts.top_intent_score),
                intent_topics = COALESCE(EXCLUDED.intent_topics, tenant_contacts.intent_topics),
                cohort = COALESCE(EXCLUDED.cohort, tenant_contacts.cohort),
                qualification_path = COALESCE(EXCLUDED.qualification_path, tenant_contacts.qualification_path),
                signal_source_url = COALESCE(EXCLUDED.signal_source_url, tenant_contacts.signal_source_url),
                signal_observed = COALESCE(EXCLUDED.signal_observed, tenant_contacts.signal_observed),
                signal_evidence = COALESCE(EXCLUDED.signal_evidence, tenant_contacts.signal_evidence),
                signal_subject = COALESCE(EXCLUDED.signal_subject, tenant_contacts.signal_subject),
                signal_agent_kind = COALESCE(EXCLUDED.signal_agent_kind, tenant_contacts.signal_agent_kind),
                category_relation = COALESCE(EXCLUDED.category_relation, tenant_contacts.category_relation),
                verdict = COALESCE(EXCLUDED.verdict, tenant_contacts.verdict),
                verdict_reason = COALESCE(EXCLUDED.verdict_reason, tenant_contacts.verdict_reason),
                signal_column = COALESCE(EXCLUDED.signal_column, tenant_contacts.signal_column),
                suppression = COALESCE(EXCLUDED.suppression, tenant_contacts.suppression),
                suppression_date = COALESCE(EXCLUDED.suppression_date, tenant_contacts.suppression_date),
                judge_verdict = COALESCE(EXCLUDED.judge_verdict, tenant_contacts.judge_verdict),
                judge_verdict_reason = COALESCE(EXCLUDED.judge_verdict_reason, tenant_contacts.judge_verdict_reason),
                judge_calibrated = COALESCE(EXCLUDED.judge_calibrated, tenant_contacts.judge_calibrated),
                judge_defect_class = COALESCE(EXCLUDED.judge_defect_class, tenant_contacts.judge_defect_class),
                lane = COALESCE(EXCLUDED.lane, tenant_contacts.lane),
                lane_reason = COALESCE(EXCLUDED.lane_reason, tenant_contacts.lane_reason),
                raw_fields = tenant_contacts.raw_fields || EXCLUDED.raw_fields,
                updated_at = now()
            RETURNING *
            """,
            workspace_id,
            data.get("account_id"),
            data["pool_row_id"],
            data.get("email"),
            data.get("first_name") or data.get("first"),
            data.get("last_name") or data.get("last"),
            data.get("title"),
            data.get("company"),
            data.get("company_domain"),
            data.get("city"),
            data.get("country"),
            data.get("segment"),
            data.get("tier"),
            data.get("score"),
            data.get("conf"),
            data.get("conf_tier"),
            data.get("email_status"),
            data.get("why_now"),
            data.get("case_study"),
            data.get("src"),
            data.get("heat"),
            data.get("top_intent_score"),
            data.get("intent_topics"),
            data.get("cohort"),
            data.get("qualification_path"),
            data.get("signal_source_url"),
            data.get("signal_observed"),
            data.get("signal_evidence"),
            data.get("signal_subject"),
            data.get("signal_agent_kind"),
            data.get("category_relation"),
            data.get("verdict"),
            data.get("verdict_reason"),
            data.get("signal_column"),
            data.get("suppression"),
            data.get("suppression_date"),
            data.get("judge_verdict"),
            data.get("judge_verdict_reason"),
            data.get("judge_calibrated"),
            data.get("judge_defect_class"),
            data.get("lane"),
            data.get("lane_reason"),
            raw_fields_json,
        )
    if not row:
        raise RuntimeError(f"Failed to upsert contact {data.get('pool_row_id')}")
    return dict(row)
