"""Cost reservation + the per-run terminal hook (§R2 on the backend paid route).

``_track_run`` lives here rather than with the state because its done-callback is what
settles a run's reservation on ANY terminal state — the reservation plumbing and the
guaranteed-terminal hook are one concern."""

from __future__ import annotations

import asyncio
import logging
import os

from gtm_core.metering import (
    acheck_budget,
    areserve_budget,
    mark_debit_failed,
    reserve_credits,
    settle_credits,
    workspace_jsonl_run_total,
)

from ...database import workspace_scope
from .state import _background_tasks, _release_run_slot

_log = logging.getLogger(__name__)

# ── cost reservation (follow-up (f), Phase 1) — default OFF ───────────────────────
# COST_RESERVATION_ENABLED gates the atomic-reservation path (areserve_budget/reserve_credits). OFF ⇒
# the gating calls stay acheck_budget and this behaves byte-identically to today (a
# reservation-free system has zero open reservations ⇒ same numbers). Phase 1 reserves a
# fixed conservative per-run estimate; Phase 2 (accurate per-stage estimates) is blocked
# on pricing decision #2.


def _reservation_enabled() -> bool:
    return os.getenv("COST_RESERVATION_ENABLED", "false").lower() in ("1", "true", "yes")


# Fixed conservative Phase-1 estimate (p90-ish of recent run cost); tune via env.
RESERVATION_ESTIMATE_USD = float(os.getenv("RESERVATION_ESTIMATE_USD", "2.0"))
RESERVATION_ESTIMATE_CREDITS = float(os.getenv("RESERVATION_ESTIMATE_CREDITS", "2000.0"))


async def _reserve_or_deny(pool, workspace_id: str, run_id: str) -> bool:
    """Flag-gated pre-spend gate. Returns True if the run may proceed (spend admitted).

    Flag ON  → atomic reserve_credits inside workspace_scope (holds a cost_reservations
               slot; exact under concurrency).
    Flag OFF → today's acheck_budget read (byte-identical to pre-reservation behaviour).
    Both run RLS-subject and are fail-closed on the backend paid route.
    """
    async with workspace_scope(pool, workspace_id) as conn:
        if _reservation_enabled():
            # ADOPT, never re-insert: at most one reservation may be open per run (V040's unique
            # partial index). A run that was reserved at dispatch (MM12), or that already passed a
            # gate with this flag on, holds its reservation still — a second INSERT would violate
            # the index, which reserve_credits reads as "deny", failing every gated run
            # cost_cap_reached. Adopting is still a re-check: the cap and the failed-debit marker
            # are read again, so an unreconciled wallet stops the run here too.
            if await conn.fetchval(
                "SELECT 1 FROM cost_reservations "
                "WHERE run_id = $1::uuid AND state = 'open' LIMIT 1",
                run_id,
            ):
                return await acheck_budget(
                    pool, workspace_id, table="cost_records", conn=conn, fail_closed=True
                )
            rid = await reserve_credits(
                conn,
                workspace_id,
                run_id=run_id,
                estimated_credits=RESERVATION_ESTIMATE_CREDITS,
                fail_closed=True,
            )
            if rid is not None:
                return True
            legacy_rid = await areserve_budget(
                conn,
                workspace_id,
                run_id=run_id,
                estimate=RESERVATION_ESTIMATE_USD,
                table="cost_records",
                fail_closed=True,
            )
            return legacy_rid is not None
        return await acheck_budget(
            pool, workspace_id, table="cost_records", conn=conn, fail_closed=True
        )


async def admits(
    pool, workspace_id: str, agent_id: str | None, agent_budget_usd: float | None
) -> bool:
    """RL-08/M-06: the SAME fail-closed §R2 verdict ``pack_executor._budget_guard``
    computes before every dispatch batch — exposed here so ``create_run`` can refuse an
    over-cap run synchronously at POST time, before any row is written or a concurrency
    slot is spent. Not a new check: ``_reserve_or_deny``/``_budget_guard`` still run
    unchanged inside the executor as the defense-in-depth backstop against a cap
    exhausted by OTHER runs in the gap between this admission check and dispatch.

    Workspace cap AND (if an agent is acting) the agent's own narrower cap — the same AND
    ``_budget_guard`` uses. ``acheck_agent_budget`` is imported locally to avoid a
    circular import (``backend.agents`` imports from this package's siblings).
    """
    from ...agents import acheck_agent_budget

    async with workspace_scope(pool, workspace_id) as conn:
        return await acheck_budget(
            pool, workspace_id, table="cost_records", conn=conn, fail_closed=True
        ) and await acheck_agent_budget(conn, workspace_id, agent_id, agent_budget_usd)


async def _settle_run_reservations(pool, workspace_id: str, run_id: str) -> None:
    """Close (settle) any open reservations for a terminal run. Never raises.

    Fires from the guaranteed terminal hook (_track_run done-callback) on ANY terminal
    state — ok/failed/rejected/gate-timeout/cancel/shutdown-cancel — so a reservation can
    never leak. The authoritative spend is the real cost_records rows written during the
    run; the reservation only held the slot in flight. A hard process kill that skips this
    is caught by the crash sweep (release_stale_reservations, backend/main.py _evict).

    A settle that FAILS is not best-effort: the run's spend is then undebited and its
    reservation still open, and the crash sweep would later release that hold as free usage.
    The transaction rolled back, so the wallet is marked in a transaction of its own and the
    workspace's next run is refused until an operator reconciles (MM3)."""
    had_open = False
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            open_rows = await conn.fetch(
                "SELECT id::text FROM cost_reservations WHERE run_id = $1::uuid AND state != 'settled'",
                run_id,
            )
            if not open_rows:
                return
            had_open = True

            # cost_records.run_id is TEXT (V003) while cost_reservations.run_id is UUID: a ::uuid
            # cast here raised "operator does not exist: text = uuid" on every settle, hidden by
            # the old bare `except` — with the flag on, no run's spend ever reached its wallet.
            cost_row = await conn.fetchrow(
                "SELECT COALESCE(SUM(cost_usd), 0.0) as actual_usd FROM cost_records WHERE run_id = $1::text",
                run_id,
            )
            actual_usd = float(cost_row["actual_usd"]) if cost_row else 0.0
            # MM2: the workers' spend for this run is in the workspace's JSONL ledgers, stamped with
            # the run id (the stores are disjoint, so this adds, never repeats). A ledger that cannot
            # be totalled raises into the handler below, which marks the wallet: what the run owes is
            # unknown, and a guess in either direction is wrong.
            actual_usd += workspace_jsonl_run_total(workspace_id, run_id)
            actual_credits = actual_usd * 1000.0

            for i, r in enumerate(open_rows):
                settle_amount = actual_credits if i == 0 else 0.0
                await settle_credits(
                    conn,
                    r["id"],
                    settle_amount,
                    runtime="backend",
                    run_id=run_id,
                    raise_on_error=True,
                )

            await conn.execute(
                "UPDATE cost_reservations SET state = 'settled', closed_at = now() "
                "WHERE run_id = $1::uuid AND state != 'settled'",
                run_id,
            )
    except Exception:  # noqa: BLE001
        if not had_open:
            # We never saw an open reservation, so no debit is known to be owed: a transient error on
            # the read is not a reason to lock a workspace that (like every run with no reservation)
            # may well hold none. Loud, but not a marker. The crash sweep is the backstop.
            _log.error(
                "run %s: could not read its reservations for workspace %s; nothing marked",
                run_id,
                workspace_id,
                exc_info=True,
            )
            return
        marked = await mark_debit_failed(pool, workspace_id)
        _log.error(
            "run %s: settling its reservations failed for workspace %s; wallet %s",
            run_id,
            workspace_id,
            "marked, next run will be refused" if marked else "NOT marked — reconcile by hand",
            exc_info=True,
        )


def _track_run(task: asyncio.Task, pool, workspace_id: str, run_id: str) -> asyncio.Task:
    """Track a run task AND guarantee its concurrency slot is freed on ANY terminal
    state — including a task cancelled by shutdown-drain BEFORE its body runs, which a
    ``finally:`` inside _execute_run would never reach (that was the slot leak).

    This same guaranteed-terminal hook also settles the run's open reservations (whatever the
    cost-reservation flag says: MCP dispatch reserves regardless), so a reservation cannot leak on
    any terminal path a task reaches."""
    from .state import register_workspace_task

    _background_tasks.add(task)
    register_workspace_task(workspace_id, task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        _release_run_slot(workspace_id, run_id)
        if getattr(t, "_gtm_slot_held", False) and not getattr(t, "_gtm_slot_released", False):
            from .queue import release_worker_slot

            release_worker_slot()
        # Unconditional (MM12): a run can hold a reservation whatever the flag says — MCP dispatch
        # reserves at enqueue — and settling is a no-op for a run with none open.
        try:
            # Fire-and-forget: settle is a DB op and this callback is sync. The
            # crash sweep (release_stale_reservations) backstops a loop already
            # closing during shutdown-drain (RuntimeError below).
            asyncio.create_task(_settle_run_reservations(pool, workspace_id, run_id))
        except RuntimeError:
            pass

    task.add_done_callback(_done)
    return task
