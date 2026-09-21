"""Cost metering and budget guard for MCP tool calls.

Thin adapters over the shared metering contract (:mod:`gtm_core.metering`) — the
SQL and the cost-record schema now live in one place. The two public functions
keep their original signatures so ``mcp_server/server.py`` call sites are unchanged:

  1. check_budget(workspace_id, pool)  — pre-call guard (§R2).
  2. meter_call(...)                    — post-call record.

Both run RLS-subject: the workspace is known post-auth, so each acquires a
``workspace_scope`` connection (SET LOCAL app.current_workspace_id) and passes it
to the shared contract via ``conn=``. The ``mcp_calls`` INSERT then satisfies its
``WITH CHECK (workspace_id = current_workspace_id())`` under FORCE RLS as gtm_api.

Both are best-effort. The MCP runtime is a *per-call, reconcilable* product, so the
guard is intentionally **fail-open with no hard ceiling**: a transient DB blip must
never lock a paying developer out (PRD §5.4 / decision #5).
"""

from __future__ import annotations

from gtm_core.db import workspace_scope
from gtm_core.metering import (
    CostRecord,
    InsufficientCreditsError,
    PgSink,
    acheck_budget,
    ameter,
    reserve_credits,
    settle_credits,
)

__all__ = [
    "InsufficientCreditsError",
    "check_budget",
    "meter_call",
    "reserve_credits",
    "settle_credits",
]


async def check_budget(workspace_id: str, pool) -> bool:
    """Return False if the workspace has exceeded its monthly cost cap.

    Raises InsufficientCreditsError if the workspace wallet balance <= 0.00 and no active
    subscription is under cap.
    Fail-open on DB connection error (no hard ceiling) — MCP is metered per-call and reconcilable.
    Runs inside workspace_scope so the budget read is RLS-subject (gtm_api).
    """
    async with workspace_scope(pool, workspace_id) as conn:
        wallet_row = await conn.fetchrow(
            "SELECT balance_credits, balance_usd FROM workspace_wallets WHERE workspace_id = $1::uuid",
            workspace_id,
        )
        has_wallet = wallet_row is not None
        wallet_credits = 0.0
        wallet_usd = 0.0
        if has_wallet:
            if wallet_row.get("balance_credits") is not None:
                wallet_credits = float(wallet_row["balance_credits"])
            if wallet_row.get("balance_usd") is not None:
                wallet_usd = float(wallet_row["balance_usd"])
                if wallet_row.get("balance_credits") is None:
                    wallet_credits = wallet_usd * 1000.0

        # 1. Positive prepaid wallet balance allows execution
        if wallet_credits > 0.00 or wallet_usd > 0.00:
            return True

        # 2. Check if an active subscription with positive cap covers the call
        sub_row = await conn.fetchrow(
            "SELECT monthly_cost_cap_usd, status FROM subscriptions WHERE workspace_id = $1::uuid",
            workspace_id,
        )
        has_active_sub = (
            sub_row is not None
            and sub_row["status"] in ("active", "trialing")
            and float(sub_row["monthly_cost_cap_usd"] or 0) > 0
        )
        if has_active_sub:
            sub_allowed = await acheck_budget(
                pool,
                workspace_id,
                table="unified_metering_log",
                hard_ceiling_multiplier=None,
                conn=conn,
            )
            if sub_allowed:
                return True

        raise InsufficientCreditsError(
            "402 Payment Required: Insufficient MCP credits. Please top up your balance."
        )


async def meter_call(
    *,
    workspace_id: str,
    api_key_id: str,
    tool_name: str,
    profile_name: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    cost_credits: float | None = None,
    pool,
) -> None:
    """Append a cost record to unified_metering_log and deduct from workspace_wallets. Best-effort: errors are swallowed.

    Runs inside workspace_scope so the UPDATE and INSERT are RLS-subject (gtm_api) and pass
    the WITH CHECK on unified_metering_log.
    """
    credits = cost_credits if cost_credits is not None else round(cost_usd * 1000.0, 4)
    async with workspace_scope(pool, workspace_id) as conn:
        try:
            await conn.execute(
                """
                UPDATE workspace_wallets
                   SET balance_usd = GREATEST(0.0, balance_usd - $1),
                       balance_credits = GREATEST(0.0, balance_credits - ($1 * 1000.0)),
                       updated_at = now()
                 WHERE workspace_id = $2::uuid
                """,
                cost_usd,
                workspace_id,
            )
        except Exception:  # nosec B110  # noqa: BLE001
            pass

        await ameter(
            CostRecord(
                runtime="mcp",
                source=tool_name,
                cost_usd=cost_usd,
                cost_credits=credits,
                op=tool_name,
                model_or_sku=model,
                profile=profile_name,
                workspace_id=workspace_id,
                api_key_id=api_key_id,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
            ),
            sink=PgSink(pool, "unified_metering_log"),
            conn=conn,
        )
