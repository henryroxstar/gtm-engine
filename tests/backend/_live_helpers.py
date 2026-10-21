"""Seeding and readers shared by the live (``dbtest``) monetization tests.

Light on purpose: only ``gtm_core.db`` and stdlib, so a file that imports it still collects in the
minimal ``backend-db`` CI job (``pytest asyncpg`` ...). Everything runs against a REAL Postgres as the
non-owner ``gtm_api`` role, so RLS is genuinely in force; ``admin`` is the owner connection used only to
arrange state a tenant could not write for itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from gtm_core.db import workspace_scope


async def register(admin, email: str) -> str:
    """Insert a user (fires the SECURITY DEFINER bootstrap trigger); return the workspace id."""
    uid = await admin.fetchval(
        "INSERT INTO users(email, display_name, password_hash) VALUES($1, $2, 'h') RETURNING id::text",
        email,
        email.split("@")[0],
    )
    return await admin.fetchval("SELECT workspace_for_user($1::uuid)::text", uid)


async def seed_workspace(admin, email: str, *, credits: float, cap_usd: float = 100.0) -> str:
    """A workspace with a prepaid wallet of ``credits`` and a subscription cap that admits spend."""
    wid = await register(admin, email)
    await admin.execute(
        "INSERT INTO workspace_wallets(workspace_id, balance_credits, balance_usd) "
        "VALUES($1::uuid, $2, $3) ON CONFLICT (workspace_id) DO UPDATE "
        "SET balance_credits = EXCLUDED.balance_credits, balance_usd = EXCLUDED.balance_usd",
        wid,
        credits,
        credits / 1000.0,
    )
    await admin.execute(
        "UPDATE subscriptions SET monthly_cost_cap_usd = $2 WHERE workspace_id = $1::uuid",
        wid,
        cap_usd,
    )
    return wid


async def seed_run(api, wid: str, *, status: str | None = None) -> str:
    async with workspace_scope(api, wid) as c:
        if status is None:
            return await c.fetchval(
                "INSERT INTO runs(workspace_id, profile_name, prompt) "
                "VALUES($1::uuid, 'p', 'x') RETURNING id::text",
                wid,
            )
        return await c.fetchval(
            "INSERT INTO runs(workspace_id, profile_name, prompt, status) "
            "VALUES($1::uuid, 'p', 'x', $2) RETURNING id::text",
            wid,
            status,
        )


async def wallet(api, wid: str) -> tuple[float, float]:
    async with workspace_scope(api, wid) as c:
        row = await c.fetchrow(
            "SELECT balance_credits, balance_usd FROM workspace_wallets WHERE workspace_id = $1::uuid",
            wid,
        )
    return float(row["balance_credits"]), float(row["balance_usd"])


async def marker(api, wid: str):
    async with workspace_scope(api, wid) as c:
        return await c.fetchval(
            "SELECT debit_failed_at FROM workspace_wallets WHERE workspace_id = $1::uuid", wid
        )


async def reservation_states(api, wid: str) -> list[str]:
    async with workspace_scope(api, wid) as c:
        rows = await c.fetch(
            "SELECT state FROM cost_reservations WHERE workspace_id = $1::uuid", wid
        )
    return [r["state"] for r in rows]


async def seed_cost_record(admin, wid: str, usd: float, *, run_id: str | None = None) -> None:
    """One brain-style ``cost_records`` row (the Postgres side of the two cost stores)."""
    await admin.execute(
        "INSERT INTO cost_records(workspace_id, profile_name, run_id, stage, model, cost_usd) "
        "VALUES($1::uuid, 'p', $2, 'brain', 'm', $3)",
        wid,
        run_id,
        usd,
    )


def write_worker_ledger(
    workspaces_root: Path, wid: str, profile: str, rows: list[dict], *, raw_tail: str = ""
) -> Path:
    """Write a workspace's per-profile worker ledger (the JSONL side), as a worker would append it.

    ``raw_tail`` is appended verbatim — a torn final line, or any other hand-made damage.
    """
    path = workspaces_root / wid / "content" / profile / "costs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + raw_tail, encoding="utf-8")
    return path
