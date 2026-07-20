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
from gtm_core.metering import CostRecord, PgSink, acheck_budget, ameter


async def check_budget(workspace_id: str, pool) -> bool:
    """Return False if the workspace has exceeded its monthly cost cap (mcp_calls).

    Fail-open on error (no hard ceiling) — MCP is metered per-call and reconcilable.
    Runs inside workspace_scope so the budget read is RLS-subject (gtm_api).
    """
    async with workspace_scope(pool, workspace_id) as conn:
        return await acheck_budget(
            pool,
            workspace_id,
            table="mcp_calls",
            hard_ceiling_multiplier=None,
            conn=conn,
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
    pool,
) -> None:
    """Append a cost record to mcp_calls. Best-effort: errors are swallowed.

    Runs inside workspace_scope so the INSERT is RLS-subject (gtm_api) and passes
    the WITH CHECK on mcp_calls.
    """
    async with workspace_scope(pool, workspace_id) as conn:
        await ameter(
            CostRecord(
                runtime="mcp",
                source=tool_name,
                cost_usd=cost_usd,
                op=tool_name,
                model_or_sku=model,
                profile=profile_name,
                workspace_id=workspace_id,
                api_key_id=api_key_id,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
            ),
            sink=PgSink(pool, "mcp_calls"),
            conn=conn,
        )
