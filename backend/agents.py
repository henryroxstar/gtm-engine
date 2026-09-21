"""Agent-entity helpers (Track A A4) shared by the runs + agents routers.

The narrowing-only invariant is enforced at write (backend/routers/agents.py)
AND re-derived at every use — the stored row is never trusted:

  - effective packs  = agent.packs ∩ profile's currently-activated packs,
    computed at run creation (``agent_pack_allowed``);
  - effective cap    = min(current workspace cap, agent budget), computed at
    spend-check time (``acheck_agent_budget`` runs AFTER the workspace check —
    it can only narrow, never widen).

All queries here run on an RLS-scoped connection (``workspace_scope``); the
explicit ``workspace_id`` filters are defence-in-depth on top of RLS.
"""

from __future__ import annotations

from typing import Any

_AGENT_COLS = (
    "id::text AS agent_id, workspace_id::text, name, profile_name, packs, "
    "language, monthly_budget_usd, status, is_default, read_scope, daily_dispatch_cap, "
    "created_at::text, updated_at::text"
)


def agent_row_to_dict(row: Any) -> dict:
    """Project an agents row (any of the ``_AGENT_COLS`` selects) to the API shape."""
    return {
        "agent_id": row["agent_id"],
        "workspace_id": row["workspace_id"],
        "name": row["name"],
        "profile_name": row["profile_name"],
        "packs": list(row["packs"]) if row["packs"] is not None else None,
        "language": row["language"],
        "monthly_budget_usd": (
            float(row["monthly_budget_usd"]) if row["monthly_budget_usd"] is not None else None
        ),
        "status": row["status"],
        "is_default": bool(row["is_default"]),
        "read_scope": row["read_scope"],
        "daily_dispatch_cap": row["daily_dispatch_cap"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def fetch_agent(conn: Any, workspace_id: str, agent_id: str) -> Any | None:
    """The agent row by id (archived included — resolvable by id forever)."""
    return await conn.fetchrow(
        f"SELECT {_AGENT_COLS} FROM agents WHERE id = $1::uuid AND workspace_id = $2::uuid",  # noqa: S608 # nosec B608 — column list is a module constant
        agent_id,
        workspace_id,
    )


async def ensure_default_agent(conn: Any, workspace_id: str) -> str | None:
    """Race-safe lazy bootstrap of the workspace's default agent; returns its id.

    Bound to the workspace's default profile. Returns ``None`` when the workspace
    has no profile yet (pre-onboarding) — runs then carry a NULL agent_id, exactly
    like pre-A4 rows. Concurrency: the partial unique index
    (``agents_one_default_per_workspace``) + ``ON CONFLICT DO NOTHING`` guarantee
    exactly one default even under concurrent first runs.
    """
    # isinstance guards double as strictness: attribution only ever uses a real
    # string id — anything else (incl. a test double's non-str return) degrades
    # to None, i.e. no attribution, never garbage in runs.agent_id.
    existing = await conn.fetchval(
        "SELECT id::text FROM agents WHERE workspace_id = $1::uuid AND is_default",
        workspace_id,
    )
    if isinstance(existing, str) and existing:
        return existing

    profile = await conn.fetchval(
        """SELECT profile_name FROM profiles WHERE workspace_id = $1::uuid
           ORDER BY is_default DESC, created_at ASC LIMIT 1""",
        workspace_id,
    )
    if not isinstance(profile, str) or not profile:
        return None

    try:
        await conn.execute(
            """INSERT INTO agents(workspace_id, name, profile_name, is_default)
               VALUES($1::uuid, 'default', $2, true)
               ON CONFLICT (workspace_id) WHERE is_default DO NOTHING""",
            workspace_id,
            profile,
        )
    except Exception:  # noqa: BLE001 — e.g. a live agent already NAMED 'default'
        # The default slot is still free (the conflict above was on the name
        # index) — take it under a non-colliding bootstrap name.
        import uuid as _uuid

        try:
            await conn.execute(
                """INSERT INTO agents(workspace_id, name, profile_name, is_default)
                   VALUES($1::uuid, $2, $3, true)
                   ON CONFLICT (workspace_id) WHERE is_default DO NOTHING""",
                workspace_id,
                f"default-{_uuid.uuid4().hex[:6]}",
                profile,
            )
        except Exception:  # noqa: BLE001 — give up: attribution is best-effort
            return None

    final = await conn.fetchval(
        "SELECT id::text FROM agents WHERE workspace_id = $1::uuid AND is_default",
        workspace_id,
    )
    return final if isinstance(final, str) and final else None


def agent_pack_allowed(agent_packs: list[str] | None, pack: str) -> bool:
    """A4 narrowing at use: NULL packs = every activated pack; else membership."""
    return agent_packs is None or pack in agent_packs


async def agent_month_spent(conn: Any, workspace_id: str, agent_id: str) -> float:
    """This agent's month-to-date spend (the disjoint-partition read: rows sum to
    the workspace total across agents + the NULL-agent remainder)."""
    val = await conn.fetchval(
        """SELECT COALESCE(SUM(cost_usd), 0) FROM cost_records
           WHERE workspace_id = $1::uuid AND agent_id = $2::uuid
             AND date_trunc('month', recorded_at AT TIME ZONE 'UTC')
               = date_trunc('month', now() AT TIME ZONE 'UTC')""",
        workspace_id,
        agent_id,
    )
    return float(val or 0)


async def acheck_agent_budget(
    conn: Any, workspace_id: str, agent_id: str | None, budget_usd: float | None
) -> bool:
    """Per-agent narrowing on top of the workspace §R2 check (which runs first).

    No agent / no budget → allow (the workspace cap alone governs). Fail-closed on
    a read error — this guards a network-facing paid path, same policy as the
    backend's ``acheck_budget(fail_closed=True)``.
    """
    if agent_id is None or budget_usd is None:
        return True
    try:
        spent = await agent_month_spent(conn, workspace_id, agent_id)
    except Exception:  # noqa: BLE001
        return False
    return spent < float(budget_usd)


async def agent_today_dispatch_count(conn: Any, workspace_id: str, agent_id: str) -> int:
    """Runs created for this agent since the start of the current UTC day.

    Uses ``runs_agent_daily_idx (workspace_id, agent_id, created_at)`` (V026) as a
    range scan — the index was built specifically for this access pattern.
    """
    val = await conn.fetchval(
        """SELECT COUNT(*) FROM runs
           WHERE workspace_id = $1::uuid AND agent_id = $2::uuid
             AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')""",
        workspace_id,
        agent_id,
    )
    return int(val or 0)


async def acheck_agent_daily_cap(
    conn: Any, workspace_id: str, agent_id: str | None, daily_dispatch_cap: int | None
) -> bool:
    """Per-agent dispatch ceiling for the current UTC day: no agent / no cap → allow.

    Unlike ``acheck_agent_budget``, a read error is NOT folded into ``False``: that
    would report "cap reached" — a 429 telling the caller to wait until tomorrow — for
    what is really "could not count". The error propagates, and the caller
    (``backend/callers/limits.py:enforce_agent_daily_cap``) refuses it as a 503.
    """
    if agent_id is None or daily_dispatch_cap is None:
        return True
    count = await agent_today_dispatch_count(conn, workspace_id, agent_id)
    return count < daily_dispatch_cap
