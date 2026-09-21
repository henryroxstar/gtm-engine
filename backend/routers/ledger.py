"""Ledger endpoints: cost summary and history."""

from __future__ import annotations

from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..callers.limits import enforce_principal_rate
from ..callers.principal import Principal
from ..callers.rest import require_principal
from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import (
    ERROR_RESPONSES,
    AgentCostRollupItem,
    AgentCostRollupResponse,
    CostSummaryResponse,
    HistoryResponse,
    RunCostRollupResponse,
    UsageResponse,
)
from ..services.runs.principal_admission import read_scope_for_principal
from ..types import UuidStr

router = APIRouter(prefix="/ledger", tags=["ledger"], responses=ERROR_RESPONSES)


@router.get("/costs", response_model=CostSummaryResponse)
async def get_costs(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    month: str | None = None,  # YYYY-MM, defaults to current month
    agent_id: UuidStr | None = None,  # A4: one agent's partition of the same ledger
) -> CostSummaryResponse:
    """Return cost summary for the workspace, defaulting to the current month.

    ``agent_id`` filters to that agent's rows — partitions are disjoint (rows carry
    at most one agent) and sum to the workspace total across agents + the
    NULL-agent remainder (pre-A4 rows).
    """
    from datetime import datetime

    if month is None:
        month = datetime.now(UTC).strftime("%Y-%m")

    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        if agent_id:
            rows = await conn.fetch(
                """SELECT run_id, stage, model, input_tokens, output_tokens,
                          cost_usd::float, recorded_at::text
                   FROM cost_records
                   WHERE workspace_id = $1::uuid
                     AND to_char(recorded_at, 'YYYY-MM') = $2
                     AND agent_id = $3::uuid
                   ORDER BY recorded_at DESC""",
                ws.workspace_id,
                month,
                agent_id,
            )
        else:
            rows = await conn.fetch(
                """SELECT run_id, stage, model, input_tokens, output_tokens,
                          cost_usd::float, recorded_at::text
                   FROM cost_records
                   WHERE workspace_id = $1::uuid
                     AND to_char(recorded_at, 'YYYY-MM') = $2
                   ORDER BY recorded_at DESC""",
                ws.workspace_id,
                month,
            )
        cap_row = await conn.fetchrow(
            "SELECT monthly_cost_cap_usd::float FROM subscriptions WHERE workspace_id = $1::uuid",
            ws.workspace_id,
        )

    total = sum(r["cost_usd"] for r in rows)
    # Fail-safe: no subscription row → no paid budget (0), not the old $50 default.
    cap = cap_row["monthly_cost_cap_usd"] if cap_row else 0.0

    return CostSummaryResponse(
        month=month,
        total_usd=round(total, 6),
        cap_usd=cap,
        over_cap=total >= cap,
        records=[dict(r) for r in rows],
    )


@router.get("/rollup", response_model=RunCostRollupResponse)
async def get_run_rollup(
    run_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> RunCostRollupResponse:
    """Per-run cost rollup: what one run cost end-to-end, broken down by stage + model.

    The unit-economics input pricing (PENDING #2) is blocked on — joins the run's
    metered cost rows by run_id. RLS scopes the read to this workspace.
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        rows = await conn.fetch(
            """SELECT stage,
                      model,
                      COUNT(*)                    AS calls,
                      SUM(cost_usd)::float        AS cost_usd,
                      COALESCE(SUM(input_tokens), 0)  AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens
               FROM cost_records
               WHERE workspace_id = $1::uuid AND run_id = $2
               GROUP BY stage, model
               ORDER BY cost_usd DESC""",
            ws.workspace_id,
            run_id,
        )
    total = sum(r["cost_usd"] for r in rows)
    return RunCostRollupResponse(
        run_id=run_id,
        total_usd=round(total, 6),
        breakdown=[dict(r) for r in rows],
    )


@router.get("/agents", response_model=AgentCostRollupResponse)
async def get_agents_rollup(
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
    from_date: str | None = None,
    to_date: str | None = None,
    agent_id: UuidStr | None = None,
) -> AgentCostRollupResponse:
    """Workspace-scoped cost rollup grouped by agent_id and principal (Fleet PRD §3 G6).

    Aggregates spend across cost_records joined with runs.
    - If caller is a service principal and read_scope != 'workspace', agent_id is forced
      to its own agent_id.
    - from_date and to_date filter recorded_at.
    """
    from datetime import datetime

    pool = request.app.state.pool
    ws = principal.workspace_id

    if principal.kind == "service":
        read_scope = await read_scope_for_principal(pool, ws, principal)
        if read_scope != "workspace":
            agent_id = principal.agent_id

    from_dt = None
    if from_date is not None:
        try:
            from_dt = datetime.fromisoformat(from_date)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "invalid_from_date", "message": "from_date must be an ISO-8601 string"},
            ) from exc

    to_dt = None
    if to_date is not None:
        try:
            to_dt = datetime.fromisoformat(to_date)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "invalid_to_date", "message": "to_date must be an ISO-8601 string"},
            ) from exc

    if from_dt is not None and to_dt is not None and from_dt > to_dt:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "invalid_date_range", "message": "from_date cannot be after to_date"},
        )

    query = """
        SELECT c.agent_id::text                    AS agent_id,
               r.principal_kind,
               r.principal_id,
               COUNT(DISTINCT c.run_id)            AS runs,
               COUNT(*)                            AS calls,
               COALESCE(SUM(c.cost_usd), 0)::float AS cost_usd,
               COALESCE(SUM(c.input_tokens), 0)    AS input_tokens,
               COALESCE(SUM(c.output_tokens), 0)   AS output_tokens
        FROM cost_records c
        LEFT JOIN runs r ON r.id = c.run_id AND r.workspace_id = c.workspace_id
        WHERE c.workspace_id = $1::uuid
    """
    args: list[object] = [ws]
    idx = 2

    if agent_id is not None:
        query += f" AND c.agent_id = ${idx}::uuid"
        args.append(agent_id)
        idx += 1

    if from_date is not None:
        query += f" AND c.recorded_at >= ${idx}::timestamptz"
        args.append(from_date)
        idx += 1

    if to_date is not None:
        query += f" AND c.recorded_at <= ${idx}::timestamptz"
        args.append(to_date)
        idx += 1

    query += " GROUP BY c.agent_id, r.principal_kind, r.principal_id ORDER BY cost_usd DESC"

    async with workspace_scope(pool, ws) as conn:
        rows = await conn.fetch(query, *args)

    items = [
        AgentCostRollupItem(
            agent_id=r["agent_id"],
            principal_kind=r["principal_kind"],
            principal_id=r["principal_id"],
            runs=r["runs"],
            calls=r["calls"],
            cost_usd=round(r["cost_usd"], 6),
            input_tokens=r["input_tokens"],
            output_tokens=r["output_tokens"],
        )
        for r in rows
    ]
    total = sum(item.cost_usd for item in items)
    return AgentCostRollupResponse(total_usd=round(total, 6), items=items)


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> UsageResponse:
    """Lean month-to-date spend vs cap — for the billing service's paywall / overage UI.

    Same numbers as /ledger/costs without the per-record list, so the billing service can poll it
    cheaply to show "you've used $X of $Y" and drive its own upgrade prompts. The cap is
    whatever the billing service last synced (fail-safe 0 until then).
    """
    from datetime import datetime

    now = datetime.now(UTC)
    month = now.strftime("%Y-%m")
    period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """SELECT COALESCE(SUM(c.cost_usd), 0)::float AS spent,
                      s.monthly_cost_cap_usd::float      AS cap
               FROM subscriptions s
               LEFT JOIN cost_records c
                      ON c.workspace_id = s.workspace_id
                     AND to_char(c.recorded_at, 'YYYY-MM') = $2
               WHERE s.workspace_id = $1::uuid
               GROUP BY s.monthly_cost_cap_usd""",
            ws.workspace_id,
            month,
        )
    spent = float(row["spent"]) if row else 0.0
    cap = float(row["cap"]) if row else 0.0

    return UsageResponse(
        period="current_month",
        period_start=period_start,
        spent_usd=round(spent, 6),
        cap_usd=cap,
        over_cap=spent >= cap,
    )


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    limit: Annotated[int, Query(ge=1)] = 50,
) -> HistoryResponse:
    """Return recent run history entries for this workspace."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        rows = await conn.fetch(
            """SELECT id::text, status, profile_name,
                      created_at::text, completed_at::text
               FROM runs
               WHERE workspace_id = $1::uuid
               ORDER BY created_at DESC LIMIT $2""",
            ws.workspace_id,
            min(limit, 200),
        )
    return HistoryResponse(entries=[dict(r) for r in rows])
