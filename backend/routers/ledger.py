"""Ledger endpoints: cost summary and history."""

from __future__ import annotations

from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..schemas import (
    CostSummaryResponse,
    HistoryResponse,
    RunCostRollupResponse,
    UsageResponse,
)

router = APIRouter(prefix="/ledger", tags=["ledger"])


@router.get("/costs", response_model=CostSummaryResponse)
async def get_costs(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    month: str | None = None,  # YYYY-MM, defaults to current month
) -> CostSummaryResponse:
    """Return cost summary for the workspace, defaulting to the current month."""
    from datetime import datetime

    if month is None:
        month = datetime.now(UTC).strftime("%Y-%m")

    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
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
    run_id: str,
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
    limit: int = 50,
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
