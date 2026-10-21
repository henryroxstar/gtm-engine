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
import json
import logging
import os
import random

from gtm_core.capabilities import Entitlement

from ...broker import WORKER_ID  # noqa: F401 — re-exported: the queue's claim identity
from ...database import workspace_scope
from .budget import _settle_run_reservations, _track_run
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

# ── Worker compute concurrency cap (R5) ──────────────────────────────────────
_worker_max_slots: int = 4
_worker_in_use: int = 0
_worker_slot_event: asyncio.Event | None = None
_worker_slot_loop: asyncio.AbstractEventLoop | None = None


def _max_concurrent_runs_from_env() -> int:
    raw = os.getenv("GTM_WORKER_MAX_CONCURRENT_RUNS", "4")
    try:
        return max(1, int(raw))
    except ValueError:
        return 4


def _get_slot_event() -> asyncio.Event:
    global _worker_slot_event, _worker_slot_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _worker_slot_event is None or _worker_slot_loop is not loop:
        _worker_slot_event = asyncio.Event()
        _worker_slot_loop = loop
        if _worker_in_use < _worker_max_slots:
            _worker_slot_event.set()
    return _worker_slot_event


def reset_worker_slots(limit: int | None = None) -> None:
    """Reset the worker compute concurrency cap and in-use counter."""
    global _worker_max_slots, _worker_in_use, _worker_slot_event, _worker_slot_loop
    _worker_max_slots = max(1, int(limit) if limit is not None else _max_concurrent_runs_from_env())
    _worker_in_use = 0
    _worker_slot_event = None
    _worker_slot_loop = None


def available_worker_slots() -> int:
    """Return the number of currently available worker compute slots."""
    return max(0, _worker_max_slots - _worker_in_use)


async def acquire_worker_slot() -> None:
    """Wait until a worker compute slot is free, then claim one."""
    global _worker_in_use
    while _worker_in_use >= _worker_max_slots:
        ev = _get_slot_event()
        ev.clear()
        await ev.wait()
    _worker_in_use += 1
    if _worker_in_use >= _worker_max_slots:
        _get_slot_event().clear()


def release_worker_slot() -> None:
    """Release one held worker compute slot and wake any waiting claim_loop."""
    global _worker_in_use
    if _worker_in_use > 0:
        _worker_in_use -= 1
    if _worker_in_use < _worker_max_slots:
        _get_slot_event().set()


@contextlib.asynccontextmanager
async def worker_slot_released_during_gate(reacquire: bool = True):
    """Temporarily release a run's worker compute slot while parked at awaiting_approval (R5)."""
    curr = asyncio.current_task()
    was_held = getattr(curr, "_gtm_slot_held", True) and _worker_in_use > 0
    if was_held:
        release_worker_slot()
        if curr is not None:
            curr._gtm_slot_released = True

    gate_ctrl = {"reacquire": reacquire}
    try:
        yield gate_ctrl
    except BaseException:
        if curr is not None:
            curr._gtm_slot_released = True
        raise
    finally:
        if gate_ctrl.get("reacquire") and was_held:
            await acquire_worker_slot()
            if curr is not None:
                curr._gtm_slot_released = False
                curr._gtm_slot_held = True


async def wait_for_wakeup(delay: float) -> None:
    """Sleep until the next queue poll tick."""
    await asyncio.sleep(delay)


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


def _payload(row) -> dict:
    """The claimed row's job record as a dict (asyncpg may hand JSONB back as text)."""
    payload = row["payload"] or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def _require_one_row(status: str | None, what: str) -> None:
    """Raise unless an asyncpg command status says exactly one row was updated."""
    if status != "UPDATE 1":
        raise RuntimeError(f"{what} expected to update exactly one row, got {status!r}")


async def _stamp_fake(pool, workspace_id: str, run_id: str) -> None:
    """Durably mark a run as fake before it runs (``services/runs/fake.py``, "A fake run
    stays fake"). A MERGE, so the job record (mode/pack/inputs) a reclaim re-reads survives.
    Not best-effort: an unstamped fake is exactly the run a later claim or restart with the
    flag off could hand to real spend, so a failed write — or one that matched no row —
    raises and stops this dispatch instead."""
    from .fake import STAMP_KEY

    async with workspace_scope(pool, workspace_id) as conn:
        status = await conn.execute(
            """UPDATE runs SET payload = COALESCE(payload, '{}'::jsonb)
                                          || jsonb_build_object($3::text, true)
               WHERE id = $1::uuid AND workspace_id = $2::uuid""",
            run_id,
            workspace_id,
            STAMP_KEY,
        )
        _require_one_row(status, f"fake stamp on run {run_id}")


async def dispatch_claimed(pool, repo_root, sessions, row) -> bool:
    """Turn one claimed row into a running task. True when a task was dispatched.

    The reclaim policy mirrors the split ``reconcile_gates`` already makes, because it is
    the same underlying fact about the two engines: a pack run resumes from its durable
    manifest, while a prompt run's SDK session died with its worker and cannot be
    resumed — so it is failed explicitly, giving the client a terminal state instead of a
    run that never moves.
    """
    from .executor import _execute_run
    from .fake import FAKE_RUN_OFF_ERROR, _execute_fake_run, fake_runs_enabled, stamped_fake
    from .pack_executor import _execute_pack_run

    run_id = str(row["id"])
    workspace_id = str(row["workspace_id"])
    payload = _payload(row)
    mode = payload.get("mode") or ("pack" if payload.get("pack") else "prompt")
    first_dispatch = row["prev_status"] == "queued"

    if row["attempts"] > MAX_ATTEMPTS:
        await _fail_run(
            pool,
            workspace_id,
            run_id,
            "run failed too many times — not retried",
            error_code="retries_exhausted",
        )
        # No executor task exists to settle this run's reservation (MM12), so release it here.
        await _settle_run_reservations(pool, workspace_id, run_id)
        return False
    if stamped_fake(payload) and not fake_runs_enabled():
        # A run dispatched as a fake stays one. The real executor would spend, and could
        # apply an approval recorded against the fake draft to a real one.
        await _fail_run(
            pool, workspace_id, run_id, FAKE_RUN_OFF_ERROR, error_code="fake_runs_disabled"
        )
        # No executor task exists to settle this run's reservation (MM12), so release it here.
        await _settle_run_reservations(pool, workspace_id, run_id)
        return False
    if not first_dispatch and mode != "pack":
        await _fail_run(pool, workspace_id, run_id, _RESTART_MESSAGE, error_code="run_interrupted")
        # No executor task exists to settle this run's reservation (MM12), so release it here.
        await _settle_run_reservations(pool, workspace_id, run_id)
        return False

    entitlement = await _live_entitlement(pool, workspace_id)
    agent_id = None if row["agent_id"] is None else str(row["agent_id"])

    if fake_runs_enabled():
        # GTM_FAKE_RUNS (dev only; boot-guarded in backend.main): the same claim, slot, §R2
        # check, gate row and events, with scripted work in place of the SDK — for both modes.
        if not stamped_fake(payload):
            await _stamp_fake(pool, workspace_id, run_id)
        coro = _execute_fake_run(
            pool,
            repo_root,
            workspace_id,
            run_id,
            row["profile_name"],
            pack=payload.get("pack"),
            variant=payload.get("variant"),
        )
    elif mode == "pack":
        inputs = payload.get("inputs")
        context = payload.get("context")
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
            context=context if isinstance(context, dict) else {},
        )
    else:
        if sessions is None:
            await _fail_run(
                pool,
                workspace_id,
                run_id,
                "no agent session store on this worker",
                error_code="worker_unavailable",
            )
            # No executor task exists to settle this run's reservation (MM12), so release it here.
            await _settle_run_reservations(pool, workspace_id, run_id)
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
    task = asyncio.create_task(coro)
    task._gtm_slot_held = True
    task._gtm_slot_released = False
    _track_run(task, pool, workspace_id, run_id)
    return True


async def claim_loop(pool, repo_root, sessions) -> None:
    """Poll for claimable runs forever. One task per worker; cancelled at shutdown."""
    global _worker_max_slots
    if "GTM_WORKER_MAX_CONCURRENT_RUNS" in os.environ:
        _worker_max_slots = _max_concurrent_runs_from_env()
    while True:
        slot_held = False
        try:
            await acquire_worker_slot()
            slot_held = True
            row = await claim_next(pool)
            if row is None:
                release_worker_slot()
                slot_held = False
                await wait_for_wakeup(_jittered_poll())
                continue
            dispatched = await dispatch_claimed(pool, repo_root, sessions, row)
            if not dispatched:
                release_worker_slot()
                slot_held = False
            else:
                slot_held = False  # ownership transferred to the dispatched run task
        except asyncio.CancelledError:
            if slot_held:
                release_worker_slot()
            raise
        except Exception:  # noqa: BLE001 — the loop outlives any one bad row
            if slot_held:
                release_worker_slot()
            log.exception("run queue dispatch failed")
            await wait_for_wakeup(_jittered_poll())


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


async def _cancel_requested(pool, held: dict[str, set[str]]) -> dict[str, set[str]]:
    """Of the runs this worker holds, which have a cancel request? One WORKSPACE-SCOPED read per workspace.

    Deliberately not folded into the cross-tenant ``SECURITY DEFINER`` heartbeat/claim functions (those are
    audited and stay as they are): RLS filters each read to its own tenant, so a request in one workspace can
    never flag a run in another, whatever ids the worker believes it holds.
    """
    out: dict[str, set[str]] = {}
    for workspace_id, run_ids in held.items():
        if not run_ids:
            continue
        async with workspace_scope(pool, workspace_id) as conn:
            rows = await conn.fetch(
                "SELECT id::text AS id FROM runs "
                "WHERE workspace_id = $1::uuid AND id = ANY($2::uuid[]) AND cancel_requested_at IS NOT NULL",
                workspace_id,
                sorted(run_ids),
            )
        if rows:
            out[workspace_id] = {r["id"] for r in rows}
    return out


async def cancel_tick(pool, store=None) -> list[str]:
    """Interrupt the in-flight session of every held run that has been asked to cancel (F8).

    Rides on the heartbeat tick (<= LEASE_S / 3 ≈ 30 s), so cancel latency is one tick plus margin. It reads
    the DATABASE — Redis is latency, never correctness — and never raises: a failed read costs one tick, not
    the heartbeat. Returns the run ids it interrupted.
    """
    from .state import _cancelled_runs, get_session_store

    held = {ws: {str(r) for r in ids} for ws, ids in _workspace_runs.items() if ids}
    if not held:
        return []
    try:
        requested = await _cancel_requested(pool, held)
    except Exception:  # noqa: BLE001 — a missed cancel read is retried next tick
        log.warning("cancel read failed", exc_info=True)
        return []
    store = store if store is not None else get_session_store()
    interrupted: list[str] = []
    for run_ids in requested.values():
        for run_id in sorted(run_ids):
            _cancelled_runs.add(run_id)  # the executor's own between-chunks guard sees it too
            try:
                if store is not None and await store.interrupt_run(run_id):
                    interrupted.append(run_id)
            except Exception:  # noqa: BLE001
                log.warning("could not interrupt run %s", run_id, exc_info=True)
    return interrupted


async def heartbeat_loop(pool) -> None:
    """Beat at a third of the lease, so two consecutive misses still do not expire it."""
    interval = LEASE_S / 3
    while True:
        await asyncio.sleep(interval)
        if _workspace_runs:
            await heartbeat_once(pool)
            await cancel_tick(pool)


async def stop_task(task: asyncio.Task | None) -> None:
    """Cancel and await one of this module's loops (shutdown helper)."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
