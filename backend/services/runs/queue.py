"""The durable run queue's worker loop (A5 step 2) — claim, dispatch, heartbeat.

``POST /v1/runs`` used to spawn the pipeline with ``asyncio.create_task`` in whichever
process accepted the request. That made the accepting process a single point of failure
for every run in flight — a deploy restart silently killed them, and nothing could
recover a run stranded in ``running`` (startup reconciliation only ever looked at
``awaiting_approval``). It also pinned the backend to one uvicorn worker.

Now the handler only writes a ``queued`` row and returns; this loop claims it. Exactly-once
dispatch across workers comes from ``FOR UPDATE SKIP LOCKED`` on a single row
(``claim_next_run``, V021), not from workers coordinating with each other.

**A gated run is untouchable.** A run may legitimately sit in ``awaiting_approval`` for
the full 24 h ``GATE_TIMEOUT_S``, so a reclaim keys off worker LIVENESS
(``heartbeat_at``) and never off "has been non-terminal for a while" — the latter would
double-dispatch every gated run in the system.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random

from gtm_core.capabilities import Entitlement

from ...broker import WORKER_ID  # noqa: F401 — re-exported: the queue's claim identity
from ...database import workspace_scope
from .budget import _track_run
from .persistence import _fail_run
from .state import _workspace_runs

log = logging.getLogger(__name__)

#: How long a claim survives without a heartbeat before another worker may take the run.
#: Comfortably more than 3 heartbeat intervals, so an ordinary GC pause or a slow query
#: never looks like a dead worker.
LEASE_S = 90

#: Claim poll interval. Jittered by ±25 % so N workers booting together do not
#: synchronise into a thundering herd on one row.
QUEUE_POLL_S = 1.0

#: A run that has been claimed this many times is failed rather than re-dispatched. A job
#: that crashes its worker every time it is picked up would otherwise become a crash loop
#: that walks the whole queue.
MAX_ATTEMPTS = 3

_RESTART_MESSAGE = "run did not survive a backend restart — start a new run"


def _jittered_poll() -> float:
    return QUEUE_POLL_S * random.uniform(0.75, 1.25)  # noqa: S311 # nosec B311 — scheduling jitter, not a secret


async def claim_next(pool, *, worker_id: str = "", lease_s: int = LEASE_S):
    """Claim one runnable row, or None.

    Cross-tenant by nature, so it goes through the SECURITY DEFINER ``claim_next_run``
    (V021) on the runtime pool with no workspace context — under FORCE RLS a plain
    ``SELECT ... FROM runs`` here would return zero rows, which is exactly the bug V018
    was written to fix for reconciliation. Every claimed row is acted on below under
    ``workspace_scope``.
    """
    try:
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT * FROM claim_next_run($1, $2)", worker_id or WORKER_ID, lease_s
            )
    except Exception:  # noqa: BLE001 — a claim failure must never kill the loop
        log.warning("run claim failed", exc_info=True)
        return None


async def _live_entitlement(pool, workspace_id: str) -> str:
    """Re-read the workspace's entitlement at CLAIM time, never from the payload.

    The same "live, never cached" rule ``require_auth`` and ``reconcile_gates`` follow: a
    downgrade that happened while the run sat queued is honoured, rather than a run
    executing at the tier its request was admitted under.
    """
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            row = await conn.fetchrow(
                "SELECT entitlement FROM subscriptions WHERE workspace_id = $1", workspace_id
            )
    except Exception:  # noqa: BLE001
        return Entitlement.FREE.value
    return row["entitlement"] if row else Entitlement.FREE.value


async def dispatch_claimed(pool, repo_root, sessions, row) -> bool:
    """Turn one claimed row into a running task. True when a task was dispatched.

    The reclaim policy mirrors the split ``reconcile_gates`` already makes, because it is
    the same underlying fact about the two engines: a pack run resumes from its durable
    manifest, while a prompt run's SDK session died with its worker and cannot be
    resumed — so it is failed explicitly, giving the client a terminal state instead of a
    run that never moves.
    """
    from .executor import _execute_run
    from .pack_executor import _execute_pack_run

    run_id = str(row["id"])
    workspace_id = str(row["workspace_id"])
    payload = row["payload"] or {}
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    mode = payload.get("mode") or ("pack" if payload.get("pack") else "prompt")
    first_dispatch = row["prev_status"] == "queued"

    if row["attempts"] > MAX_ATTEMPTS:
        await _fail_run(pool, workspace_id, run_id, "run failed too many times — not retried")
        return False
    if not first_dispatch and mode != "pack":
        await _fail_run(pool, workspace_id, run_id, _RESTART_MESSAGE)
        return False

    entitlement = await _live_entitlement(pool, workspace_id)
    agent_id = None if row["agent_id"] is None else str(row["agent_id"])

    if mode == "pack":
        inputs = payload.get("inputs")
        coro = _execute_pack_run(
            pool,
            repo_root,
            workspace_id,
            run_id,
            row["profile_name"],
            payload.get("pack"),
            payload.get("variant"),
            inputs if isinstance(inputs, dict) else {},
            entitlement=entitlement,
            agent_id=agent_id,
            agent_budget_usd=payload.get("agent_budget_usd"),
            language=payload.get("language"),
            dry_run=bool(payload.get("dry_run")),
        )
    else:
        if sessions is None:
            await _fail_run(pool, workspace_id, run_id, "no agent session store on this worker")
            return False
        coro = _execute_run(
            pool,
            sessions,
            workspace_id,
            run_id,
            row["profile_name"],
            row["prompt"],
            bool(payload.get("dry_run")),
            entitlement=entitlement,
            agent_id=agent_id,
        )

    # The per-workspace slot is held by the worker EXECUTING the run (this one), and
    # freed by _track_run's done-callback on any terminal state. It is also the set the
    # heartbeat below refreshes, which is why it is added here and not at admission —
    # admission may well have happened on a different worker.
    _workspace_runs.setdefault(workspace_id, set()).add(run_id)
    _track_run(asyncio.create_task(coro), pool, workspace_id, run_id)
    return True


async def claim_loop(pool, repo_root, sessions) -> None:
    """Poll for claimable runs forever. One task per worker; cancelled at shutdown."""
    while True:
        try:
            row = await claim_next(pool)
            if row is None:
                await asyncio.sleep(_jittered_poll())
                continue
            await dispatch_claimed(pool, repo_root, sessions, row)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop outlives any one bad row
            log.exception("run queue dispatch failed")
            await asyncio.sleep(_jittered_poll())


async def heartbeat_once(pool, *, worker_id: str = "") -> None:
    """Refresh the lease on every run this worker is executing — one statement per tick,
    regardless of run count.

    Cross-tenant like the claim, and for the same reason, so it runs on the definer
    function rather than a scoped UPDATE. Scoped to ``claimed_by``: a worker can only
    ever extend its OWN leases.
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT touch_worker_runs($1)", worker_id or WORKER_ID)
    except Exception:  # noqa: BLE001 — a missed beat costs latency, never correctness
        log.warning("heartbeat failed", exc_info=True)


async def heartbeat_loop(pool) -> None:
    """Beat at a third of the lease, so two consecutive misses still do not expire it."""
    interval = LEASE_S / 3
    while True:
        await asyncio.sleep(interval)
        if _workspace_runs:
            await heartbeat_once(pool)


async def stop_task(task: asyncio.Task | None) -> None:
    """Cancel and await one of this module's loops (shutdown helper)."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
