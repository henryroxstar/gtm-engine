"""Cost reservation + the per-run terminal hook (§R2 on the backend paid route).

``_track_run`` lives here rather than with the state because its done-callback is what
settles a run's reservation on ANY terminal state — the reservation plumbing and the
guaranteed-terminal hook are one concern."""

from __future__ import annotations

import asyncio
import os

from gtm_core.metering import acheck_budget, areserve_budget

from ...database import workspace_scope
from .state import _background_tasks, _release_run_slot

# ── cost reservation (follow-up (f), Phase 1) — default OFF ───────────────────────
# COST_RESERVATION_ENABLED gates the atomic-reservation path (areserve_budget). OFF ⇒
# the gating calls stay acheck_budget and this behaves byte-identically to today (a
# reservation-free system has zero open reservations ⇒ same numbers). Phase 1 reserves a
# fixed conservative per-run estimate; Phase 2 (accurate per-stage estimates) is blocked
# on pricing decision #2.


def _reservation_enabled() -> bool:
    return os.getenv("COST_RESERVATION_ENABLED", "false").lower() in ("1", "true", "yes")


# Fixed conservative Phase-1 estimate (p90-ish of recent run cost); tune via env.
RESERVATION_ESTIMATE_USD = float(os.getenv("RESERVATION_ESTIMATE_USD", "2.0"))


async def _reserve_or_deny(pool, workspace_id: str, run_id: str) -> bool:
    """Flag-gated pre-spend gate. Returns True if the run may proceed (spend admitted).

    Flag ON  → atomic areserve_budget inside workspace_scope (holds a cost_reservations
               slot; exact under concurrency).
    Flag OFF → today's acheck_budget read (byte-identical to pre-reservation behaviour).
    Both run RLS-subject and are fail-closed on the backend paid route.
    """
    async with workspace_scope(pool, workspace_id) as conn:
        if _reservation_enabled():
            rid = await areserve_budget(
                conn,
                workspace_id,
                run_id=run_id,
                estimate=RESERVATION_ESTIMATE_USD,
                table="cost_records",
                fail_closed=True,
            )
            return rid is not None
        return await acheck_budget(
            pool, workspace_id, table="cost_records", conn=conn, fail_closed=True
        )


async def _settle_run_reservations(pool, workspace_id: str, run_id: str) -> None:
    """Close (settle) any open reservations for a terminal run. Best-effort, never raises.

    Fires from the guaranteed terminal hook (_track_run done-callback) on ANY terminal
    state — ok/failed/rejected/gate-timeout/cancel/shutdown-cancel — so a reservation can
    never leak. The authoritative spend is the real cost_records rows written during the
    run; the reservation only held the slot in flight. A hard process kill that skips this
    is caught by the crash sweep (release_stale_reservations, backend/main.py _evict)."""
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                "UPDATE cost_reservations SET state = 'settled', closed_at = now() "
                "WHERE run_id = $1::uuid AND state = 'open'",
                run_id,
            )
    except Exception:  # noqa: BLE001
        pass  # nosec B110 — intentional best-effort swallow


def _track_run(task: asyncio.Task, pool, workspace_id: str, run_id: str) -> asyncio.Task:
    """Track a run task AND guarantee its concurrency slot is freed on ANY terminal
    state — including a task cancelled by shutdown-drain BEFORE its body runs, which a
    ``finally:`` inside _execute_run would never reach (that was the slot leak).

    When cost reservation is enabled, this same guaranteed-terminal hook also settles the
    run's open reservations, so a reservation cannot leak on any terminal path."""
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        _release_run_slot(workspace_id, run_id)
        if _reservation_enabled():
            try:
                # Fire-and-forget: settle is a DB op and this callback is sync. The
                # crash sweep (release_stale_reservations) backstops a loop already
                # closing during shutdown-drain (RuntimeError below).
                asyncio.create_task(_settle_run_reservations(pool, workspace_id, run_id))
            except RuntimeError:
                pass

    task.add_done_callback(_done)
    return task
