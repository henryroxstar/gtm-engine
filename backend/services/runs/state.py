"""The ONE home of the run lifecycle's in-process state (PRD 2026-09-01 §5 rule 2).

Every module-level dict/set/Event/Lock the router used to own is defined here and only
here; the router and its sibling service modules IMPORT these objects, never redefine them
— a second definition anywhere is the split-brain bug (§6.1 row 3: an SSE stream that
stays empty, a gate that never releases, a cancel that is ignored). Single-worker
assumption unchanged: this is pure motion; moving to app.state or a broker for a
multi-worker deploy is now a one-module seam."""

from __future__ import annotations

import asyncio

# In-memory gate waiters: run_id → asyncio.Event (resolved when gate decision arrives)
_gate_events: dict[str, asyncio.Event] = {}
_gate_decisions: dict[str, dict] = {}
# run_ids that were cancelled while running — guards against 'ok' overwrite
_cancelled_runs: set[str] = set()

# ── SSE run-progress streaming (GET /runs/{id}/stream) ───────────────────────────
# In-memory per-run subscriber queues fed by _publish_event at each status-write
# site in _execute_run. Same single-process assumption as _gate_events above; the
# publish/subscribe is isolated behind the helpers so the transport can later move
# to Postgres LISTEN/NOTIFY or Redis for a multi-worker deploy without touching
# _execute_run or the endpoint. Polling (GET /runs/{id}) is unaffected.
_run_subscribers: dict[str, set[asyncio.Queue]] = {}
_workspace_stream_count: dict[str, int] = {}
_STREAM_QUEUE_MAX = (
    256  # bounded; drop-oldest on overflow so a slow consumer can't stall _execute_run
)
_STREAM_HEARTBEAT_S = 15  # ping cadence — keeps the connection alive through the Cloudflare Tunnel
_MAX_STREAMS_PER_WORKSPACE = 5  # guard the asyncpg pool (max 10) against many long-lived streams

# ── concurrency + task safety (P2) ───────────────────────────────────────────────
# Per-workspace cap on CONCURRENTLY executing paid runs — immediate 429 over cap
# (not a queue). Bounds the cost/CPU blast radius of a burst and shrinks the
# budget-check TOCTOU window. Slots are a SET of in-flight run_ids (not a bare
# counter) so release is idempotent and keyed to a specific run: a double release,
# or a run whose task is cancelled before it starts, can never under/over-count.
# _state_lock guards the reserve (check-and-add) so two creates can't both pass the cap.
_MAX_CONCURRENT_RUNS_PER_WORKSPACE = 3
_workspace_runs: dict[str, set[str]] = {}
_state_lock = asyncio.Lock()

# Tracked background pipeline tasks: FastAPI BackgroundTasks are fire-and-forget +
# untracked (GC-able mid-flight, undrainable on shutdown). We hold strong refs and
# drain them in the lifespan shutdown.
_background_tasks: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> asyncio.Task:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def drain_background_tasks(timeout: float = 10.0) -> None:
    """Cancel and await tracked pipeline tasks on shutdown (best-effort)."""
    tasks = list(_background_tasks)
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


def _release_run_slot(workspace_id: str, run_id: str) -> None:
    """Free one per-workspace run slot. Sync + lock-free + idempotent: ``set.discard``
    is atomic under the single-threaded event loop and the reserve critical section
    holds no ``await``, so this cannot interleave badly — which is what lets it run
    from a task done-callback (a sync context that cannot ``await`` _state_lock)."""
    active = _workspace_runs.get(workspace_id)
    if active is not None:
        active.discard(run_id)
        if not active:
            _workspace_runs.pop(workspace_id, None)


_TERMINAL_STATUSES = frozenset({"ok", "failed", "rejected", "canceled"})
