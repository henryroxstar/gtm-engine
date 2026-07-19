"""Pipeline run endpoints: start, poll, gate approval.

Run lifecycle:
  POST /v1/runs               → creates run record, starts background task
  GET  /v1/runs/{run_id}      → poll for status + stage outputs
  POST /v1/runs/{run_id}/gate → approve | edit | reject a gate
  GET  /v1/runs               → list recent runs for this workspace

The background task runs the agent pipeline via BackendSessionStore.run().
Gates (⟦GATE:plan⟧, ⟦GATE:publish⟧) are detected in the agent output and stored
as "awaiting_approval" status — the mobile app polls and then POSTs to /gate.
The publish gate invariant holds: agent/publish.py makes the call only after
the operator approves the exact bytes via this endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from gtm_core.metering import acheck_budget, areserve_budget

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..push import send_gate_push
from ..ratelimit import limiter
from ..schemas import GateRequest, RunRequest, RunResponse

router = APIRouter(prefix="/runs", tags=["runs"])

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

# ── cost reservation (follow-up (f), Phase 1) — default OFF ───────────────────────
# COST_RESERVATION_ENABLED gates the atomic-reservation path (areserve_budget). OFF ⇒
# the gating calls stay acheck_budget and this behaves byte-identically to today (a
# reservation-free system has zero open reservations ⇒ same numbers). Phase 1 reserves a
# fixed conservative per-run estimate; Phase 2 (accurate per-stage estimates) is blocked
# on pricing decision #2 — see docs/prds/2026-07-06-atomic-cost-reservation.md.


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
        pass


def _track(task: asyncio.Task) -> asyncio.Task:
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


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


def _content_sha(text: str | None) -> str | None:
    """sha256 of gate content — binds an approval to the exact bytes shown (H9)."""
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sse_frame(event: str, data: dict, *, seq: int | None = None) -> str:
    """Format one text/event-stream frame (id/event/data lines, blank-line terminated)."""
    parts = []
    if seq is not None:
        parts.append(f"id: {seq}")
    parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, separators=(',', ':'))}")
    return "\n".join(parts) + "\n\n"


def _publish_event(run_id: str, event: str, data: dict) -> None:
    """Push an event to every SSE subscriber of run_id. Never raises.

    Bounded queue with drop-oldest: a slow/dead consumer can never stall
    _execute_run (which holds the publish-gate invariant) or grow memory.
    """
    subs = _run_subscribers.get(run_id)
    if not subs:
        return
    payload = (event, data)
    for q in subs:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()  # drop oldest, then enqueue the newest
                q.put_nowait(payload)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass


def _subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_STREAM_QUEUE_MAX)
    _run_subscribers.setdefault(run_id, set()).add(q)
    return q


def _unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    subs = _run_subscribers.get(run_id)
    if subs is None:
        return
    subs.discard(q)
    if not subs:
        _run_subscribers.pop(run_id, None)


async def _execute_run(
    pool,
    sessions,
    workspace_id: str,
    run_id: str,
    profile_name: str,
    prompt: str,
    dry_run: bool,
) -> None:
    """Background task: run the pipeline, persist stage outputs, handle gates."""
    try:
        # Guard: run was cancelled before background task started
        if run_id in _cancelled_runs:
            _cancelled_runs.discard(run_id)
            return

        # Pre-call budget gate (§R2): block before any paid brain call if the workspace
        # is at/over its monthly cost cap. Fail-closed on the backend paid route. Runs
        # RLS-subject. With COST_RESERVATION_ENABLED this atomically reserves a slot
        # (exact under concurrency); otherwise it is today's acheck_budget read.
        if not await _reserve_or_deny(pool, workspace_id, run_id):
            async with workspace_scope(pool, workspace_id) as conn:
                await conn.execute(
                    "UPDATE runs SET status = 'failed', error = 'monthly cost cap reached' "
                    "WHERE id = $1::uuid",
                    run_id,
                )
            _publish_event(
                run_id,
                "done",
                {"run_id": run_id, "status": "failed", "error": "monthly cost cap reached"},
            )
            return
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                """UPDATE runs SET status = 'running', started_at = now()
                   WHERE id = $1::uuid""",
                run_id,
            )
        _publish_event(run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()})

        output_buf: list[str] = []
        handled_gates: set[str] = set()

        async for chunk in sessions.run(pool, workspace_id, profile_name, prompt, run_id):
            output_buf.append(chunk)
            # Detect gate sentinels in the streamed output
            combined = "".join(output_buf)
            for sentinel in ("⟦GATE:plan⟧", "⟦GATE:publish⟧"):
                # Each sentinel fires AT MOST once. output_buf is never trimmed, so
                # a consumed sentinel stays in `combined` for the rest of the stream;
                # tracking handled sentinels (rather than a single reset-to-None flag)
                # stops the next chunk from re-detecting and re-blocking the SAME gate
                # — otherwise an approved gate re-opens on every subsequent chunk and
                # the run can never progress past its first gate.
                if sentinel in combined and sentinel not in handled_gates:
                    handled_gates.add(sentinel)
                    # Pause: await operator approval via POST /gate
                    event = asyncio.Event()
                    _gate_events[run_id] = event
                    # Guard: run was cancelled in the tiny window before event registered
                    if run_id in _cancelled_runs:
                        _cancelled_runs.discard(run_id)
                        _gate_events.pop(run_id, None)
                        return
                    async with workspace_scope(pool, workspace_id) as conn:
                        await conn.execute(
                            """UPDATE runs
                               SET status = 'awaiting_approval',
                                   pending_gate = $2,
                                   pending_content = $3
                               WHERE id = $1::uuid""",
                            run_id,
                            sentinel,
                            combined,
                        )
                    _publish_event(
                        run_id,
                        "awaiting_approval",
                        {"run_id": run_id, "pending_gate": sentinel, "pending_content": combined},
                    )
                    # Notify registered devices — non-blocking; failure never blocks the gate
                    asyncio.create_task(send_gate_push(pool, workspace_id, run_id, sentinel))
                    # Wait up to 24h for the gate decision
                    try:
                        await asyncio.wait_for(event.wait(), timeout=86400)
                    except TimeoutError:
                        async with workspace_scope(pool, workspace_id) as conn:
                            await conn.execute(
                                "UPDATE runs SET status = 'failed', error = 'gate timeout' "
                                "WHERE id = $1::uuid",
                                run_id,
                            )
                        _publish_event(
                            run_id,
                            "done",
                            {"run_id": run_id, "status": "failed", "error": "gate timeout"},
                        )
                        return

                    decision = _gate_decisions.pop(run_id, {})
                    # "approve" and "edit" both proceed — "edit" is approve-with-
                    # substituted-bytes (applied below), NOT a rejection. Only an
                    # explicit "reject" (or an unexpected/empty decision) stops the run.
                    if decision.get("decision") not in ("approve", "edit"):
                        async with workspace_scope(pool, workspace_id) as conn:
                            await conn.execute(
                                "UPDATE runs SET status = 'rejected' WHERE id = $1::uuid",
                                run_id,
                            )
                        _publish_event(run_id, "done", {"run_id": run_id, "status": "rejected"})
                        return
                    # Approved — re-gate before spending more, so a long multi-gate run
                    # can't overshoot the cap set at start (H6). Same reserve/deny path as
                    # the start guard (reserves an additional slot when the flag is on).
                    if not await _reserve_or_deny(pool, workspace_id, run_id):
                        async with workspace_scope(pool, workspace_id) as conn:
                            await conn.execute(
                                "UPDATE runs SET status = 'failed', "
                                "error = 'monthly cost cap reached' WHERE id = $1::uuid",
                                run_id,
                            )
                        _publish_event(
                            run_id,
                            "done",
                            {
                                "run_id": run_id,
                                "status": "failed",
                                "error": "monthly cost cap reached",
                            },
                        )
                        _gate_events.pop(run_id, None)
                        return
                    # Approve-with-edits: when the operator supplies edited bytes,
                    # those become this run's content of record. Replace the
                    # streamed-so-far buffer so the persisted `output` is EXACTLY
                    # what was approved, and rewrite `pending_content` so the gate's
                    # audit row (and any downstream consumer) sees the approved bytes,
                    # not the original draft. NOTE: the backend records the approved
                    # content; the server-side publish CALL is not wired in this
                    # runtime (it lives in the Telegram cockpit via agent/publish.py),
                    # so this cannot ship the wrong bytes — it fixes the persisted
                    # record + what a future backend publish path would consume.
                    edited = decision.get("edited_content")
                    if edited is not None:
                        output_buf.clear()
                        output_buf.append(edited)
                        async with workspace_scope(pool, workspace_id) as conn:
                            await conn.execute(
                                "UPDATE runs SET pending_content = $2 WHERE id = $1::uuid",
                                run_id,
                                edited,
                            )
                    # Continue streaming. `handled_gates` already contains this
                    # sentinel, so it will not re-fire; do NOT clear it.
                    _gate_events.pop(run_id, None)

        # Guard: run was cancelled while streaming
        if run_id in _cancelled_runs:
            _cancelled_runs.discard(run_id)
            return

        final_output = "".join(output_buf)
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                """UPDATE runs SET status = 'ok', output = $2, completed_at = now()
                   WHERE id = $1::uuid""",
                run_id,
                final_output,
            )
        _publish_event(run_id, "done", {"run_id": run_id, "status": "ok", "output": final_output})
    except Exception as exc:  # noqa: BLE001
        try:
            async with workspace_scope(pool, workspace_id) as conn:
                await conn.execute(
                    "UPDATE runs SET status = 'failed', error = $2 WHERE id = $1::uuid",
                    run_id,
                    str(exc),
                )
        except Exception:
            pass
        _publish_event(run_id, "done", {"run_id": run_id, "status": "failed", "error": str(exc)})
    # The per-workspace concurrency slot is freed by the task done-callback set in
    # create_run (_track_run) — guaranteed on success, error, or cancellation.


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("30/minute")
async def create_run(
    body: RunRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> RunResponse:
    """Start a pipeline run. Returns immediately; poll GET /runs/{id} for status."""
    pool = request.app.state.pool
    sessions = request.app.state.sessions
    run_id = str(uuid.uuid4())

    # Reserve a concurrency slot (atomic check-and-add of this run_id) — 429 over the
    # cap. The task done-callback frees it on any terminal state; a failed INSERT
    # rolls it back here (no task is created on that path).
    async with _state_lock:
        active = _workspace_runs.get(ws.workspace_id, set())
        if len(active) >= _MAX_CONCURRENT_RUNS_PER_WORKSPACE:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Too many concurrent runs (max {_MAX_CONCURRENT_RUNS_PER_WORKSPACE})",
            )
        active.add(run_id)
        _workspace_runs[ws.workspace_id] = active

    try:
        async with workspace_scope(pool, ws.workspace_id) as conn:
            await conn.execute(
                """INSERT INTO runs(id, workspace_id, profile_name, prompt, dry_run, status)
                   VALUES($1::uuid, $2::uuid, $3, $4, $5, 'pending')""",
                run_id,
                ws.workspace_id,
                body.profile_name,
                body.prompt,
                body.dry_run,
            )
    except Exception:
        _release_run_slot(ws.workspace_id, run_id)
        raise

    # Tracked asyncio task (not FastAPI BackgroundTasks): GC-safe + drainable on
    # shutdown. _track_run's done-callback frees the concurrency slot on any terminal
    # state (incl. a shutdown-drain cancel before the task body runs).
    _track_run(
        asyncio.create_task(
            _execute_run(
                pool,
                sessions,
                ws.workspace_id,
                run_id,
                body.profile_name,
                body.prompt,
                body.dry_run,
            )
        ),
        pool,
        ws.workspace_id,
        run_id,
    )
    return RunResponse(run_id=run_id, status="pending", profile_name=body.profile_name)


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> RunResponse:
    """Poll a run for current status and output."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """SELECT id::text, status, profile_name, output, error,
                      pending_gate, pending_content
               FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid""",
            run_id,
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    return RunResponse(
        run_id=row["id"],
        status=row["status"],
        profile_name=row["profile_name"],
        stages=[
            {"output": row["output"], "error": row["error"], "pending_gate": row["pending_gate"]}
        ],
        pending_content=row["pending_content"],
        pending_content_sha=_content_sha(row["pending_content"]),
    )


@router.get("", response_model=list[RunResponse])
async def list_runs(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    limit: int = 20,
) -> list[RunResponse]:
    """List the most recent runs for this workspace."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        rows = await conn.fetch(
            """SELECT id::text, status, profile_name
               FROM runs WHERE workspace_id = $1::uuid
               ORDER BY created_at DESC LIMIT $2""",
            ws.workspace_id,
            min(limit, 100),
        )
    return [
        RunResponse(run_id=r["id"], status=r["status"], profile_name=r["profile_name"])
        for r in rows
    ]


_TERMINAL_STATUSES = frozenset({"ok", "failed", "rejected"})


@router.post("/{run_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_run(
    run_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Cancel a pending, running, or gate-paused run.

    Sets status to 'rejected' with error 'canceled by user'. If the run is
    waiting at a gate, the gate event is resolved immediately so the background
    task exits cleanly. 409 if the run is already in a terminal state.
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT status FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if row["status"] in _TERMINAL_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Run is already terminal (status={row['status']!r})",
        )

    # Mark cancelled BEFORE firing the gate event (so the task sees the flag
    # regardless of scheduling order) and resolve any active gate with 'reject' —
    # all under the state lock, single-consumption, consistent with decide_gate.
    async with _state_lock:
        _cancelled_runs.add(run_id)
        event = _gate_events.get(run_id)
        if event is not None and run_id not in _gate_decisions:
            _gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
            event.set()

    # Update DB directly — background task may not run or may overwrite otherwise.
    async with workspace_scope(pool, ws.workspace_id) as conn:
        await conn.execute(
            """UPDATE runs SET status = 'rejected', error = 'canceled by user'
               WHERE id = $1::uuid AND workspace_id = $2::uuid""",
            run_id,
            ws.workspace_id,
        )

    # Push to any open stream directly — the background task may be between awaits.
    _publish_event(
        run_id, "done", {"run_id": run_id, "status": "rejected", "error": "canceled by user"}
    )
    return {"run_id": run_id, "status": "rejected"}


@router.post("/{run_id}/gate", status_code=status.HTTP_200_OK)
async def decide_gate(
    run_id: str,
    body: GateRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Approve, edit, or reject a gate.

    The operator sees the exact pending content in GET /runs/{id} and POSTs
    their decision here. This is the backend equivalent of the Telegram approve
    button — publish.py makes the actual call only after this approves.
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT status, pending_gate, pending_content FROM runs "
            "WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if row["status"] != "awaiting_approval":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Run is not awaiting approval (status={row['status']!r})"
        )

    # Bind the decision to the EXACT bytes the operator saw (H9). content_sha is
    # REQUIRED (schema) and MUST equal the current pending_content's hash — this
    # rejects a stale/duplicate/blind decision meant for a different gate of this run.
    # Fail closed if pending_content is somehow absent (its hash is None → mismatch).
    if body.content_sha != _content_sha(row["pending_content"]):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "content_sha does not match the current gate content (stale or superseded)",
        )

    # Single-consumption under the state lock: the first decision wins; a second
    # (duplicate/stale) is rejected rather than overwriting or double-resolving.
    async with _state_lock:
        event = _gate_events.get(run_id)
        if event is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No active gate waiter for this run")
        if run_id in _gate_decisions:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A decision has already been recorded for this gate"
            )
        _gate_decisions[run_id] = {
            "decision": body.decision,
            "edited_content": body.edited_content,
        }
        event.set()
    return {"run_id": run_id, "decision": body.decision}


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    chunks: bool = False,
) -> StreamingResponse:
    """Stream run progress as Server-Sent Events (text/event-stream).

    Additive to GET /runs/{id} polling, which is unchanged. On connect emits a
    `snapshot` of current state, then live `status` / `awaiting_approval` / `done`
    events plus a ~15s `ping` heartbeat. The the billing-service client sends Authorization:
    Bearer (CORS already allows it). Closing the stream never cancels the run —
    only POST /runs/{id}/cancel does that.
    """
    pool = request.app.state.pool

    # Per-workspace concurrent-stream cap. The asyncpg pool is small (max 10) and
    # streams are long-lived; the stream itself holds no DB connection while idle
    # (snapshot read below, then served from the in-memory queue).
    if _workspace_stream_count.get(ws.workspace_id, 0) >= _MAX_STREAMS_PER_WORKSPACE:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many open run streams")

    # 404 fast (before opening a stream) if the run isn't visible to this workspace.
    async with workspace_scope(pool, ws.workspace_id) as conn:
        exists = await conn.fetchrow(
            "SELECT 1 FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            ws.workspace_id,
        )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    async def _gen() -> AsyncIterator[str]:
        # Subscribe BEFORE the snapshot read so no event fired during the read is lost.
        q = _subscribe(run_id)
        async with _state_lock:
            _workspace_stream_count[ws.workspace_id] = (
                _workspace_stream_count.get(ws.workspace_id, 0) + 1
            )
        seq = 0
        try:
            yield "retry: 3000\n\n"

            async with workspace_scope(pool, ws.workspace_id) as conn:
                row = await conn.fetchrow(
                    """SELECT id::text, status, output, error, pending_gate, pending_content
                       FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid""",
                    run_id,
                    ws.workspace_id,
                )
            if row is None:  # deleted between the existence check and here
                return
            yield _sse_frame(
                "snapshot",
                {
                    "run_id": row["id"],
                    "status": row["status"],
                    "pending_gate": row["pending_gate"],
                    "pending_content": row["pending_content"],
                },
                seq=seq,
            )
            seq += 1

            if row["status"] in _TERMINAL_STATUSES:
                done = {"run_id": row["id"], "status": row["status"]}
                if row["output"] is not None:
                    done["output"] = row["output"]
                if row["error"] is not None:
                    done["error"] = row["error"]
                yield _sse_frame("done", done, seq=seq)
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=_STREAM_HEARTBEAT_S)
                except TimeoutError:
                    yield _sse_frame("ping", {"ts": _utc_now()}, seq=seq)
                    seq += 1
                    continue
                if event == "chunk" and not chunks:
                    continue
                yield _sse_frame(event, data, seq=seq)
                seq += 1
                if event == "done":
                    return
        finally:
            _unsubscribe(run_id, q)
            async with _state_lock:
                remaining = _workspace_stream_count.get(ws.workspace_id, 0) - 1
                if remaining <= 0:
                    _workspace_stream_count.pop(ws.workspace_id, None)
                else:
                    _workspace_stream_count[ws.workspace_id] = remaining

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
