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
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from gtm_core.capabilities import Entitlement
from gtm_core.metering import acheck_budget, areserve_budget

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..publish_dispatch import dispatch_backend_publish, publish_gated_node
from ..push import send_gate_push
from ..ratelimit import limiter
from ..schemas import GateRequest, RunRequest, RunResponse

log = logging.getLogger(__name__)

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


# Poison marker enqueued on overflow: the generator closes the stream so the
# client reconnects and resyncs from a FRESH snapshot. Interim A2 backpressure
# contract — silently dropping events could absorb an awaiting_approval, which
# must never be possible; real per-run seq + replay arrives with A5's DB events.
_OVERFLOW_CLOSE = ("__overflow_close__", {})


def _publish_event(run_id: str, event: str, data: dict) -> None:
    """Push an event to every SSE subscriber of run_id. Never raises.

    Bounded queue; on overflow the stream is CLOSED (drop-oldest-then-poison) —
    a slow/dead consumer can never stall _execute_run, and a client that fell
    behind rebuilds from the reconnect snapshot instead of silently missing
    frames.
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
                q.get_nowait()  # make room, then poison — the generator closes
                q.put_nowait(_OVERFLOW_CLOSE)
            except Exception:  # noqa: BLE001
                pass  # nosec B110 — intentional best-effort swallow
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — intentional best-effort swallow


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
    *,
    entitlement: Entitlement | str,
    agent_id: str | None = None,
) -> None:
    """Background task: run the pipeline, persist stage outputs, handle gates.

    ``entitlement`` is REQUIRED (no default) on purpose. Prompt mode ran unscoped
    until 2026-08-25 because the scope was only ever wired into ``_execute_pack_run``
    — a caller that simply forgot the argument is exactly how that hole opened, so
    there is nothing here to forget. It is the live value ``require_auth`` read for
    this request, never a cached one.
    """
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

        # Commercial skill scope for the free-text path. Prompt mode has no graph, so
        # there is no run-level min_entitlement to check (that is pack mode's 403) —
        # the per-skill floors in gtm_core/gating.toml ARE the boundary here, and this
        # is what makes them enforceable: the session carries the set as the SDK
        # `skills=` allowlist and as the can_use_tool deny scope. Registry ∩
        # entitlement, NOT pack reachability — see gating.entitled_skills.
        from gtm_core.gating import entitled_skills

        allowed_skills = entitled_skills(entitlement)

        # agent_id passed only when set: pre-A4 session fakes (and the store's old
        # signature) keep working for agent-less runs.
        _run_kwargs = {"agent_id": agent_id} if agent_id is not None else {}
        async for chunk in sessions.run(
            pool,
            workspace_id,
            profile_name,
            prompt,
            run_id,
            allowed_skills=allowed_skills,
            **_run_kwargs,
        ):
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
                        {
                            "run_id": run_id,
                            "pending_gate": sentinel,
                            "pending_content": combined,
                            # Protocol-1 additive fields (no node_id — prompt runs
                            # have no graph structure).
                            "gate": "publish" if "publish" in sentinel else "plan",
                            "pending_content_sha": _content_sha(combined),
                        },
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
            pass  # nosec B110 — intentional best-effort swallow
        _publish_event(run_id, "done", {"run_id": run_id, "status": "failed", "error": str(exc)})
    # The per-workspace concurrency slot is freed by the task done-callback set in
    # create_run (_track_run) — guaranteed on success, error, or cancellation.


_GATE_PLAN_SENTINEL = "⟦GATE:plan⟧"

# Manifest stage status → wire node state (run-event.schema.json node.state enum).
# A gate-paused node reads 'running' on the wire — the awaiting_approval event
# carries the gate detail; 'awaiting_approval' is a RUN status, not a node state.
_WIRE_NODE_STATE = {
    "pending": "queued",
    "running": "running",
    "ok": "completed",
    "failed": "failed",
    "skipped": "skipped",
    "awaiting_approval": "running",
}


# ── durable gates (A5 step 1, V017 run_gates) ────────────────────────────────
# The in-memory _gate_events/_gate_decisions above stay as the fast in-process
# path; run_gates is the DURABLE record of both the WAIT and the DECISION. That
# pairing is what survives a restart: persisting only the wait would still lose a
# decision posted while the runner was down.

_DECISION_STATE = {"approve": "approved", "edit": "edited", "reject": "rejected"}


async def _open_gate_row(
    pool, workspace_id: str, run_id: str, gate: str, node_id: str, content_sha: str | None
) -> None:
    """Record an open gate. Re-opening the same (run, gate, node) resets the row so a
    prior decision can never be re-applied to a later gate of the same node."""
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                """INSERT INTO run_gates(run_id, gate, node_id, workspace_id, content_sha, state)
                   VALUES($1::uuid, $2, $3, $4::uuid, $5, 'open')
                   ON CONFLICT (run_id, gate, node_id) DO UPDATE
                     SET state = 'open', content_sha = EXCLUDED.content_sha,
                         opened_at = now(), decided_at = NULL, applied_at = NULL,
                         edited_content = NULL""",
                run_id,
                gate,
                node_id,
                workspace_id,
                content_sha,
            )
    except Exception:  # noqa: BLE001 — durability is additive; never break the gate path
        pass  # nosec B110 — intentional best-effort swallow


async def _record_gate_decision(
    conn, workspace_id: str, run_id: str, decision: str, edited_content: str | None
) -> bool:
    """Persist a decision onto this run's OPEN gate row. True if one was recorded.

    Only an ``open`` row is writable, so a duplicate/stale POST cannot overwrite a
    decision — the durable single-consumption guarantee, independent of the
    in-process ``_gate_decisions`` dict (which a restart would have emptied)."""
    row = await conn.fetchrow(
        """UPDATE run_gates SET state = $3, edited_content = $4, decided_at = now()
           WHERE run_id = $1::uuid AND workspace_id = $2::uuid AND state = 'open'
           RETURNING gate, node_id""",
        run_id,
        workspace_id,
        _DECISION_STATE[decision],
        edited_content,
    )
    return row is not None


async def _claim_gate_decision(
    pool, workspace_id: str, run_id: str, gate: str, node_id: str
) -> dict | None:
    """Atomically claim a decided-but-unapplied gate decision, or None.

    ``applied_at IS NULL`` in the predicate + ``now()`` in the SET make the claim
    single-consumption even if a restarted runner and a racing in-process waiter
    both reach for it."""
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            row = await conn.fetchrow(
                """UPDATE run_gates SET applied_at = now()
                   WHERE run_id = $1::uuid AND workspace_id = $2::uuid
                     AND gate = $3 AND node_id = $4
                     AND state <> 'open' AND applied_at IS NULL
                   RETURNING state, edited_content""",
                run_id,
                workspace_id,
                gate,
                node_id,
            )
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    # Strict: only a recognised decision state claims the gate. A malformed row
    # must read as "no decision" (leaving the gate open for a real one) rather
    # than resolving it to an arbitrary outcome — this guard is what keeps a
    # durable-read failure fail-SAFE rather than silently auto-approving.
    state = row["state"]
    decision = {"approved": "approve", "edited": "edit", "rejected": "reject"}.get(
        state if isinstance(state, str) else ""
    )
    if decision is None:
        return None
    edited = row["edited_content"]
    return {"decision": decision, "edited_content": edited if isinstance(edited, str) else None}


_PACK_PROMPT_RE = re.compile(r"^\[pack\] (?P<pack>[^/\s]+)/(?P<variant>\S+) inputs=(?P<inputs>.*)$")


async def reconcile_gates(pool, repo_root) -> int:
    """Startup reconciliation (A5): resume pack runs stranded mid-gate by a restart.

    A process restart empties ``_gate_events``, so an ``awaiting_approval`` run has
    no task waiting on it — the run would hang forever even after the operator
    decides. For every such run we re-dispatch ``_execute_pack_run``: the runner
    reloads the durable manifest, returns at the still-gated node, and the gate
    loop either claims an already-recorded decision or re-opens the wait.

    Prompt-mode runs cannot be resumed (their SDK session died with the process) —
    they are failed explicitly rather than left hanging, so a client sees a
    terminal state instead of a run that never moves. Returns runs resumed.
    """
    # A4: this cross-tenant read runs on the runtime pool (gtm_api, no BYPASSRLS)
    # with no workspace context, so a plain `SELECT ... FROM runs` would return zero
    # rows under FORCE RLS. awaiting_approval_runs() is a SECURITY DEFINER function
    # (owned by gtm_bootstrap, V018) — the same audited pattern as
    # release_stale_reservations — that returns the awaiting_approval rows across all
    # tenants. Each row is still acted on below under workspace_scope.
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id::text AS id, workspace_id::text AS workspace_id, profile_name,
                          prompt, agent_id::text AS agent_id
                   FROM awaiting_approval_runs()"""
            )
    except Exception:  # noqa: BLE001 — reconciliation must never block boot
        log.exception("gate reconciliation: could not read awaiting_approval runs")
        return 0

    resumed = 0
    for row in rows:
        match = _PACK_PROMPT_RE.match(row["prompt"] or "")
        if match is None:
            await _fail_run(
                pool,
                row["workspace_id"],
                row["id"],
                "run did not survive a backend restart — start a new run",
            )
            continue
        try:
            inputs = json.loads(match.group("inputs"))
        except (json.JSONDecodeError, TypeError):
            inputs = {}
        # Re-fetch entitlement rather than trusting anything stored from before the
        # restart — the same "live, never cached" rule require_auth follows, so a
        # downgrade that happened while the run sat gated is honoured on resume. Uses
        # this module's own `workspace_scope` (not backend.deps.fetch_entitlement's)
        # so it goes through the exact same RLS/connection path — and the exact same
        # test-patch point — as every other query in this function.
        async with workspace_scope(pool, row["workspace_id"]) as conn:
            entitlement_row = await conn.fetchrow(
                "SELECT entitlement FROM subscriptions WHERE workspace_id = $1",
                row["workspace_id"],
            )
        entitlement = entitlement_row["entitlement"] if entitlement_row else Entitlement.FREE.value
        task = asyncio.create_task(
            _execute_pack_run(
                pool,
                repo_root,
                row["workspace_id"],
                row["id"],
                row["profile_name"],
                match.group("pack"),
                match.group("variant"),
                inputs if isinstance(inputs, dict) else {},
                entitlement=entitlement,
                agent_id=row["agent_id"],
            )
        )
        _track_run(task, pool, row["workspace_id"], row["id"])
        resumed += 1

    if rows:
        log.info(
            "gate reconciliation: resumed %d of %d awaiting_approval run(s)", resumed, len(rows)
        )
    return resumed


async def _persist_node(pool, workspace_id: str, run_id: str, node_id: str, entry: dict) -> str:
    """Upsert one run_nodes row (wire vocabulary) and return the wire state."""
    state = _WIRE_NODE_STATE.get(entry.get("status", ""), "running")
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """INSERT INTO run_nodes(run_id, workspace_id, node_id, state, error,
                                     started_at, finished_at)
               VALUES($1::uuid, $2::uuid, $3, $4, $5,
                      COALESCE($6::timestamptz, now()),
                      CASE WHEN $4 IN ('completed','failed','skipped')
                           THEN now() ELSE NULL END)
               ON CONFLICT (run_id, node_id) DO UPDATE
                 SET state = EXCLUDED.state,
                     error = EXCLUDED.error,
                     finished_at = EXCLUDED.finished_at""",
            run_id,
            workspace_id,
            node_id,
            state,
            entry.get("error"),
            entry.get("started"),
        )
    return state


async def _upsert_block(
    pool, workspace_id: str, run_id: str, block_id: str, node_id: str | None, block: dict
) -> None:
    """Upsert one content block, assigning first-insert ord (snapshot order)."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """INSERT INTO run_blocks(run_id, workspace_id, block_id, ord, node_id, block)
               VALUES($1::uuid, $2::uuid, $3,
                      COALESCE((SELECT max(ord) + 1 FROM run_blocks
                                WHERE run_id = $1::uuid AND workspace_id = $2::uuid), 0),
                      $4, $5::jsonb)
               ON CONFLICT (run_id, block_id) DO UPDATE
                 SET block = EXCLUDED.block, updated_at = now()""",
            run_id,
            workspace_id,
            block_id,
            node_id,
            json.dumps(block, ensure_ascii=False),
        )


def _file_block(artifact_id: str, art) -> dict:
    """A `file` content block (run-event contract): artifact_id only — never a URL;
    fallback_text mandatory so no client drops the deliverable silently."""
    return {
        "id": f"file-{artifact_id}",
        "type": "file",
        "props": {
            "artifact_id": artifact_id,
            "name": art.name,
            "size_bytes": art.size_bytes,
            "media_type": art.media_type,
            "sha256": art.sha256,
        },
        "fallback_text": f"Attachment: {art.name} ({art.size_bytes} bytes)",
    }


async def _fail_run(pool, workspace_id: str, run_id: str, error: str) -> None:
    """Persist a failed terminal state + emit `done` — the pack path's one failure exit."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            "UPDATE runs SET status = 'failed', error = $2, completed_at = now() "
            "WHERE id = $1::uuid",
            run_id,
            error,
        )
    _publish_event(run_id, "done", {"run_id": run_id, "status": "failed", "error": error})


async def _execute_pack_run(  # noqa: PLR0915 — one linear lifecycle, mirrors _execute_run
    pool,
    repo_root,
    workspace_id: str,
    run_id: str,
    profile_name: str,
    pack: str,
    variant: str,
    inputs: dict[str, str],
    *,
    entitlement: Entitlement | str,
    agent_id: str | None = None,
    agent_budget_usd: float | None = None,
    language: str | None = None,
    dry_run: bool = False,
) -> None:
    """Background task for pack mode: drive the graph runner with gates held OUTSIDE it.

    Composition (the backend twin of agent/__main__.py's VPS root): workspace-scoped
    Config → resolve_variant (loader + tenant merge, re-validated) → engine graph →
    pack executor (Postgres cost sink + pack-reachability ∩ entitlement skill scope,
    gtm_core.packs.reachability.entitled_skills_for_profile) → PipelineRunner with a
    Postgres §R2 per-batch budget predicate.

    Gate 1 is return-and-resume: the runner RETURNS on awaiting_approval (profile
    lock released — never held across a human wait), the operator decides via
    POST /gate exactly as in prompt mode, and approval promotes the plan draft
    deterministically (agent/gate_actions.py) before flipping the gated node and
    re-entering the runner — the frontier continues downstream. This shape is the
    A5 durable-gate shape; A5 swaps the in-memory event for a run_gates row.
    """
    try:
        if run_id in _cancelled_runs:
            _cancelled_runs.discard(run_id)
            return

        if not await _reserve_or_deny(pool, workspace_id, run_id):
            await _fail_run(pool, workspace_id, run_id, "monthly cost cap reached")
            return
        async with workspace_scope(pool, workspace_id) as conn:
            await conn.execute(
                "UPDATE runs SET status = 'running', started_at = now() WHERE id = $1::uuid",
                run_id,
            )
        _publish_event(run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()})

        import dataclasses

        from agent import gate_actions
        from agent.config import Config
        from agent.packs import make_executor_from_pack, pack_graph_to_engine_graph
        from agent.pipeline import AWAITING_APPROVAL, PipelineRunner, terminal_status
        from gtm_core.packs.reachability import entitled_skills_for_profile

        from ..pack_catalog import resolve_variant
        from ..session import _workspace_scoped_config, make_pg_usage_sink

        base_cfg = Config.from_env(repo_root=repo_root)
        cfg = _workspace_scoped_config(base_cfg, workspace_id, repo_root)
        resolved = resolve_variant(repo_root, cfg.profiles_root, profile_name, pack, variant)

        # v1 ask-inputs: appended to each prompted node as a suffix line. No inputs ⇒
        # prompts stay byte-identical to the pack file (the golden-trajectory
        # contract); the values are also in the runs.prompt audit column.
        pack_graph = resolved.graph
        if inputs:
            suffix = "\n\nRun inputs (operator-provided): " + json.dumps(inputs, sort_keys=True)
            pack_graph = dataclasses.replace(
                pack_graph,
                nodes=tuple(
                    dataclasses.replace(n, prompt=n.prompt + suffix) if n.prompt else n
                    for n in pack_graph.nodes
                ),
            )

        allowed_skills = entitled_skills_for_profile(
            cfg.profiles_root, profile_name, repo_root / "packs", entitlement
        )
        executor = make_executor_from_pack(
            cfg,
            profile_name,
            pack_graph,
            usage_sink=make_pg_usage_sink(pool, workspace_id, run_id, agent_id=agent_id),
            allowed_skills=allowed_skills,
            language=language,
        )

        async def _budget_ok() -> bool:
            # §R2 per-batch guard against the backend's ledger of record (Postgres
            # cost_records) — the runner's default JSONL read would see $0 here.
            # A4: the per-agent budget can only NARROW the workspace verdict
            # (effective cap = min(workspace cap, agent budget), re-derived here
            # at every spend check so an entitlement downgrade bites immediately).
            from ..agents import acheck_agent_budget

            async with workspace_scope(pool, workspace_id) as conn:
                if not await acheck_budget(
                    pool, workspace_id, table="cost_records", conn=conn, fail_closed=True
                ):
                    return False
                return await acheck_agent_budget(conn, workspace_id, agent_id, agent_budget_usd)

        # A2 protocol-1 observer: run_nodes rows + `node` events at every transition;
        # on a terminal state, the stage's text becomes a markdown block and an
        # artifact rescan registers new files as run_artifacts rows + `file` blocks
        # (A11 tree-diff attribution — sound under the profile lock, PRD §2.1).
        from gtm_core.paths import _safe_segment

        from .. import artifacts as artifacts_mod

        _safe_segment(profile_name, "profile")  # B1 defense-in-depth (also guarded at create_run)
        profile_root = cfg.content_root / profile_name
        tree_before = artifacts_mod.snapshot_tree(profile_root)

        async def _on_node(node_id: str, state: str, entry: dict) -> None:
            nonlocal tree_before
            wire_state = await _persist_node(pool, workspace_id, run_id, node_id, entry)
            _publish_event(
                run_id,
                "node",
                {"run_id": run_id, "node_id": node_id, "state": wire_state, "ts": _utc_now()},
            )
            if wire_state not in ("completed", "failed", "skipped"):
                return
            text = entry.get("text") or ""
            if text:
                block = {
                    "id": f"node-{node_id}",
                    "type": "markdown",
                    "props": {"text": text},
                    "fallback_text": text[:2000],
                }
                await _upsert_block(pool, workspace_id, run_id, block["id"], node_id, block)
                _publish_event(
                    run_id,
                    "content",
                    {"run_id": run_id, "node_id": node_id, "mode": "replace", "block": block},
                )
            tree_after = artifacts_mod.snapshot_tree(profile_root)
            found = artifacts_mod.diff_new_artifacts(
                profile_root, profile_name, tree_before, tree_after
            )
            tree_before = tree_after
            for art in found:
                async with workspace_scope(pool, workspace_id) as conn:
                    artifact_id = await conn.fetchval(
                        """INSERT INTO run_artifacts(run_id, workspace_id, rel_path, name,
                                                     size_bytes, media_type, sha256, node_id)
                           VALUES($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8)
                           ON CONFLICT (run_id, rel_path) DO UPDATE
                             SET size_bytes = EXCLUDED.size_bytes,
                                 sha256 = EXCLUDED.sha256,
                                 node_id = EXCLUDED.node_id
                           RETURNING id::text""",
                        run_id,
                        workspace_id,
                        art.rel_path,
                        art.name,
                        art.size_bytes,
                        art.media_type,
                        art.sha256,
                        node_id,
                    )
                block = _file_block(artifact_id, art)
                await _upsert_block(pool, workspace_id, run_id, block["id"], node_id, block)
                _publish_event(
                    run_id,
                    "content",
                    {"run_id": run_id, "node_id": node_id, "mode": "replace", "block": block},
                )

        runner = PipelineRunner(
            cfg,
            profile_name,
            graph=pack_graph_to_engine_graph(pack_graph),
            budget_ok=_budget_ok,
            on_node=_on_node,
        )

        manifest: dict | None = None
        while True:
            manifest = await runner.run(run_id, "backend", executor, manifest=manifest)
            outcome = terminal_status(manifest)

            if outcome == "failed":
                first_error = next(
                    (
                        s.get("error") or f"stage {s.get('name')} failed"
                        for s in manifest["stages"]
                        if s.get("status") == "failed"
                    ),
                    "pack run failed",
                )
                await _fail_run(pool, workspace_id, run_id, first_error)
                return

            if outcome == "ok":
                if run_id in _cancelled_runs:
                    _cancelled_runs.discard(run_id)
                    return
                output = json.dumps(
                    {"pack": pack, "variant": variant, "stages": manifest["stages"]},
                    ensure_ascii=False,
                )
                async with workspace_scope(pool, workspace_id) as conn:
                    await conn.execute(
                        "UPDATE runs SET status = 'ok', output = $2, completed_at = now() "
                        "WHERE id = $1::uuid",
                        run_id,
                        output,
                    )
                _publish_event(run_id, "done", {"run_id": run_id, "status": "ok", "output": output})
                return

            # awaiting_approval — Gate 1. The content of record is the pending plan
            # draft (the exact bytes promotion will consume); a gated node without a
            # draft pauses on a JSON stub and approve simply unblocks it.
            gated = next(s for s in manifest["stages"] if s.get("status") == AWAITING_APPROVAL)
            draft_path = gate_actions.latest_plan_draft(cfg, profile_name)
            has_draft = draft_path is not None
            pending_content = (
                draft_path.read_text(encoding="utf-8")
                if has_draft
                else json.dumps({"node": gated.get("name"), "note": "awaiting approval"})
            )

            gated_node_id = gated.get("name", "")
            # A5: claim a decision that was posted while this runner was down (the
            # restart path re-enters here with the manifest still gated). Applying
            # it before opening a fresh row is what makes a decision-during-downtime
            # survive rather than silently reopen the gate.
            pending_decision = await _claim_gate_decision(
                pool, workspace_id, run_id, "plan", gated_node_id
            )

            event = asyncio.Event()
            _gate_events[run_id] = event
            if run_id in _cancelled_runs:
                _cancelled_runs.discard(run_id)
                _gate_events.pop(run_id, None)
                return
            async with workspace_scope(pool, workspace_id) as conn:
                await conn.execute(
                    """UPDATE runs
                       SET status = 'awaiting_approval', pending_gate = $2, pending_content = $3
                       WHERE id = $1::uuid""",
                    run_id,
                    _GATE_PLAN_SENTINEL,
                    pending_content,
                )
            if pending_decision is None:
                await _open_gate_row(
                    pool,
                    workspace_id,
                    run_id,
                    "plan",
                    gated_node_id,
                    _content_sha(pending_content),
                )
            _publish_event(
                run_id,
                "awaiting_approval",
                {
                    "run_id": run_id,
                    "pending_gate": _GATE_PLAN_SENTINEL,
                    "pending_content": pending_content,
                    # Protocol-1 additive fields (run-event.schema.json):
                    "gate": "plan",
                    "node_id": gated.get("name"),
                    "pending_content_sha": _content_sha(pending_content),
                },
            )
            if pending_decision is None:
                asyncio.create_task(send_gate_push(pool, workspace_id, run_id, _GATE_PLAN_SENTINEL))

                try:
                    await asyncio.wait_for(event.wait(), timeout=86400)
                except TimeoutError:
                    _gate_events.pop(run_id, None)
                    await _fail_run(pool, workspace_id, run_id, "gate timeout")
                    return
                finally:
                    _gate_events.pop(run_id, None)

                # The durable row is authoritative; the in-memory dict is the fast
                # path for the same decision decide_gate just wrote.
                decision = await _claim_gate_decision(
                    pool, workspace_id, run_id, "plan", gated_node_id
                ) or _gate_decisions.pop(run_id, {})
                _gate_decisions.pop(run_id, None)
            else:
                _gate_events.pop(run_id, None)
                decision = pending_decision
            if decision.get("decision") not in ("approve", "edit"):
                gate_actions.discard_plan_draft(cfg, profile_name)
                async with workspace_scope(pool, workspace_id) as conn:
                    await conn.execute(
                        "UPDATE runs SET status = 'rejected' WHERE id = $1::uuid", run_id
                    )
                _publish_event(run_id, "done", {"run_id": run_id, "status": "rejected"})
                return

            # Approved — re-gate before spending more (H6), same as prompt mode.
            if not await _reserve_or_deny(pool, workspace_id, run_id):
                await _fail_run(pool, workspace_id, run_id, "monthly cost cap reached")
                return

            edited = decision.get("edited_content")
            if has_draft:
                try:
                    gate_actions.promote_plan_draft(cfg, profile_name, edited_content=edited)
                except gate_actions.PlanDraftError as exc:
                    await _fail_run(pool, workspace_id, run_id, str(exc))
                    return
            if edited is not None:
                async with workspace_scope(pool, workspace_id) as conn:
                    await conn.execute(
                        "UPDATE runs SET pending_content = $2 WHERE id = $1::uuid",
                        run_id,
                        edited,
                    )

            # A10 Gate 2: if the approved node DECLARES an external effect, the
            # approval dispatches it — in Python, with the exact approved bytes,
            # to a destination pinned server-side. Driven off the declaration, not
            # the node's name; dry_run never dispatches.
            if publish_gated_node(runner.graph, gated_node_id):
                if edited is None and not has_draft:
                    # No plan draft exists for this node (it isn't a "plan" gate)
                    # and the operator approved without submitting edited_content —
                    # the only content of record is the `{"node": ..., "note":
                    # "awaiting approval"}` JSON stub written above. Dispatching
                    # THAT would publish the stub bytes, not a real post. Refuse
                    # rather than post placeholder text to the live account.
                    await _fail_run(
                        pool,
                        workspace_id,
                        run_id,
                        "publish gate has no draft content to approve — "
                        "submit edited_content with the post text",
                    )
                    return
                approved_bytes = edited if edited is not None else pending_content
                outcome = await dispatch_backend_publish(
                    cfg,
                    profile_name,
                    pool=pool,
                    workspace_id=workspace_id,
                    content=approved_bytes,
                    dry_run=dry_run,
                )
                if outcome is None:
                    # No enabled destination for this workspace (or dispatch_backend_publish
                    # swallowed an internal exception) — nothing was sent. Without this, the
                    # gated node still flipped to "complete" below, marking the run as if the
                    # post went out even though dispatch never happened.
                    await _fail_run(
                        pool,
                        workspace_id,
                        run_id,
                        "no publish destination configured for this workspace — not published",
                    )
                    return
                # Any non-success outcome fails the run, EXCEPT: dry_run (a structurally
                # intentional no-op — see agent/publish_dispatch.py) and a "duplicate"
                # publish (the A5 gate-decision-replay path re-dispatching bytes that were
                # already sent before a restart — idempotency, not failure). Every other
                # status — hash_mismatch, disclosure_missing, and every LinkedInPublisher
                # failure mode collapsed under "publish_failed" (disabled/schedule_disabled/
                # misconfigured/invalid/rate_limited/error) — must not silently read as a
                # successful publish (previously only hash_mismatch/disclosure_missing were
                # checked here; everything else, including a real send failure, fell through
                # to "mark complete").
                already_published = (
                    outcome.result is not None and outcome.result.status == "duplicate"
                )
                if outcome.status != "dry_run" and not outcome.ok and not already_published:
                    await _fail_run(pool, workspace_id, run_id, outcome.operator_line())
                    return

            # Flip the gated node to complete and resume — the runner recomputes the
            # frontier from the mutated manifest and continues downstream. Mirror the
            # flip into run_nodes + a node event (the runner's observer never sees this
            # transition — it happened here, outside the runner).
            gated["status"] = "ok"
            runner.ledgers.write_run_manifest(manifest)
            await _persist_node(pool, workspace_id, run_id, gated_node_id, gated)
            _publish_event(
                run_id,
                "node",
                {
                    "run_id": run_id,
                    "node_id": gated_node_id,
                    "state": "completed",
                    "ts": _utc_now(),
                },
            )
            async with workspace_scope(pool, workspace_id) as conn:
                await conn.execute("UPDATE runs SET status = 'running' WHERE id = $1::uuid", run_id)
            _publish_event(
                run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()}
            )
    except Exception as exc:  # noqa: BLE001
        try:
            await _fail_run(pool, workspace_id, run_id, str(exc))
        except Exception:  # noqa: BLE001
            pass  # nosec B110 — intentional best-effort swallow


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("30/minute")
async def create_run(
    body: RunRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> RunResponse:
    """Start a pipeline run. Returns immediately; poll GET /runs/{id} for status.

    Two modes (RunRequest): prompt mode (unchanged) and pack mode, which validates
    fail-closed BEFORE any slot/spend is touched, in the normative A1/A12 order —
    request-shaped errors before profile-shaped ones: 404 unknown variant → 403
    pack_not_activated → 403 entitlement_required → 422 missing_settings → 422
    pack_not_ready (blocked only; degraded proceeds).
    """
    pool = request.app.state.pool
    sessions = request.app.state.sessions
    run_id = str(uuid.uuid4())

    # A4: resolve the acting agent FIRST — it binds the profile everything below
    # validates against. Explicit agent_id ⇒ full enforcement (profile binding,
    # pack narrowing, budget, paused/archived refusal). Absent ⇒ the workspace's
    # default agent attaches for attribution only (no narrowing — backward
    # compatible with every pre-A4 client).
    from ..agents import agent_pack_allowed, ensure_default_agent, fetch_agent

    agent_row = None
    profile_name = body.profile_name
    if body.agent_id is not None:
        async with workspace_scope(pool, ws.workspace_id) as conn:
            agent_row = await fetch_agent(conn, ws.workspace_id, body.agent_id)
        if agent_row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown agent")
        if agent_row["status"] != "active":
            # paused and archived both refuse new runs (agent.schema.json lifecycle).
            raise HTTPException(status.HTTP_409_CONFLICT, {"code": f"agent_{agent_row['status']}"})
        if body.profile_name and body.profile_name != agent_row["profile_name"]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "agent_profile_mismatch", "agent_profile": agent_row["profile_name"]},
            )
        profile_name = agent_row["profile_name"]

    # B1 tenant-boundary guard: a client-supplied profile_name is a path segment —
    # it selects the profile directory every downstream join reads from and writes
    # to. Reject any traversal shape here so it can never escape the workspace root
    # (e.g. "../../../../profiles/acme", which would resolve onto the shared
    # profiles mount). A bare name stays inside the caller's own workspace tree, so
    # a shape check is what closes the escape. The agent path already bound
    # profile_name from a DB row, so it is exempt.
    if agent_row is None:
        from gtm_core.paths import _safe_segment

        try:
            _safe_segment(profile_name or "", "profile_name")
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "invalid_profile_name"},
            ) from exc

    resolved_pack = None
    if body.pack is not None:
        from gtm_core import gating
        from gtm_core.capabilities import entitlement_meets
        from gtm_core.paths import workspace_profiles_root

        from ..pack_catalog import (
            PackResolutionError,
            blocked_items,
            missing_required_settings,
            resolve_variant,
            variant_readiness,
        )

        repo_root = request.app.state.cfg.repo_root
        profiles_root = workspace_profiles_root(ws.workspace_id, repo_root)
        try:
            resolved_pack = resolve_variant(
                repo_root, profiles_root, profile_name, body.pack, body.variant
            )
        except PackResolutionError as exc:
            if exc.code == "unknown_variant":
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown pack variant") from exc
            if exc.code == "pack_not_activated":
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, {"code": "pack_not_activated"}
                ) from exc
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": "pack_invalid"}
            ) from exc

        # A4 narrowing at use: effective packs = agent.packs ∩ currently-activated
        # (the profile-activation side was just enforced by resolve_variant above).
        if agent_row is not None and not agent_pack_allowed(agent_row["packs"], body.pack):
            raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "agent_pack_not_allowed"})

        # Effective floor: the resolved_pack.graph here is ALREADY the tenant-override
        # merge (resolve_variant -> merge_pack_override), so a tenant override that adds
        # a higher-tier node raises this check too — not just the base graph's own
        # nodes. gtm_core/gating.toml, never a hardcoded ladder — see gating.py.
        run_min_entitlement = gating.resolve_graph_entitlement(
            resolved_pack.graph.pack,
            resolved_pack.graph.variant,
            resolved_pack.graph.nodes,
            explicit=resolved_pack.graph.min_entitlement,
        )
        if not entitlement_meets(ws.entitlement, run_min_entitlement):
            raise HTTPException(status.HTTP_403_FORBIDDEN, {"code": "entitlement_required"})

        missing = missing_required_settings(resolved_pack, body.inputs)
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "missing_settings", "missing": missing},
            )

        try:
            report = variant_readiness(profiles_root, profile_name, resolved_pack)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {
                    "code": "pack_not_ready",
                    "blocked": [
                        {
                            "kind": "setting",
                            "name": "profile",
                            "reason": "profile files are not provisioned yet — run onboarding",
                        }
                    ],
                },
            ) from exc
        blocked = blocked_items(report, resolved_pack)
        if blocked:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {"code": "pack_not_ready", "blocked": blocked},
            )

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

    # A4 attribution: an agent-less run attaches to the workspace's default agent
    # (lazily bootstrapped, race-safe). Best-effort — a failure here must never
    # block the run; the row then carries NULL like every pre-A4 row.
    run_agent_id: str | None = body.agent_id
    agent_budget_usd: float | None = None
    if agent_row is not None and agent_row["monthly_budget_usd"] is not None:
        agent_budget_usd = float(agent_row["monthly_budget_usd"])

    # A7 language precedence: request > agent > profile. Resolving to None here
    # means "unset" — the skills then fall back to the profile's own setting, so
    # the profile tier needs no lookup and pre-A7 behaviour is untouched.
    run_language = body.language or (agent_row["language"] if agent_row is not None else None)
    if run_agent_id is None:
        try:
            async with workspace_scope(pool, ws.workspace_id) as conn:
                run_agent_id = await ensure_default_agent(conn, ws.workspace_id)
        except Exception:  # noqa: BLE001
            run_agent_id = None

    # The runs.prompt column doubles as the audit line for pack mode (NOT NULL).
    prompt_record = (
        body.prompt
        if body.pack is None
        else f"[pack] {body.pack}/{body.variant} inputs={json.dumps(body.inputs, sort_keys=True)}"
    )
    try:
        async with workspace_scope(pool, ws.workspace_id) as conn:
            await conn.execute(
                """INSERT INTO runs(id, workspace_id, profile_name, prompt, dry_run, status,
                                    agent_id)
                   VALUES($1::uuid, $2::uuid, $3, $4, $5, 'pending', $6::uuid)""",
                run_id,
                ws.workspace_id,
                profile_name,
                prompt_record,
                body.dry_run,
                run_agent_id,
            )
    except Exception:
        _release_run_slot(ws.workspace_id, run_id)
        raise

    # Tracked asyncio task (not FastAPI BackgroundTasks): GC-safe + drainable on
    # shutdown. _track_run's done-callback frees the concurrency slot on any terminal
    # state (incl. a shutdown-drain cancel before the task body runs).
    if body.pack is not None:
        task = asyncio.create_task(
            _execute_pack_run(
                pool,
                request.app.state.cfg.repo_root,
                ws.workspace_id,
                run_id,
                profile_name,
                body.pack,
                body.variant,
                dict(body.inputs),
                entitlement=ws.entitlement,
                agent_id=run_agent_id,
                agent_budget_usd=agent_budget_usd,
                language=run_language,
                dry_run=body.dry_run,
            )
        )
    else:
        task = asyncio.create_task(
            _execute_run(
                pool,
                sessions,
                ws.workspace_id,
                run_id,
                profile_name,
                body.prompt,
                body.dry_run,
                entitlement=ws.entitlement,
                agent_id=run_agent_id,
            )
        )
    _track_run(task, pool, ws.workspace_id, run_id)
    return RunResponse(
        run_id=run_id, status="pending", profile_name=profile_name, agent_id=run_agent_id
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> RunResponse:
    """Poll a run for current status and output.

    Protocol-1 additive fields (A2): `nodes`/`content` mirror the SSE snapshot so a
    polling client rebuilds the same state a streaming client would. None on runs
    with no persisted node rows (prompt mode, pre-A2 rows).
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """SELECT id::text, status, profile_name, output, error,
                      pending_gate, pending_content, agent_id::text AS agent_id
               FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid""",
            run_id,
            ws.workspace_id,
        )
        node_rows = await conn.fetch(
            "SELECT node_id, state FROM run_nodes "
            "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY node_id",
            run_id,
            ws.workspace_id,
        )
        block_rows = await conn.fetch(
            "SELECT block FROM run_blocks "
            "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY ord",
            run_id,
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    return RunResponse(
        run_id=row["id"],
        status=row["status"],
        profile_name=row["profile_name"],
        # .get: tolerate pre-A4 fixture rows without the column (asyncpg Record
        # and dict both support it).
        agent_id=row.get("agent_id"),
        stages=[
            {"output": row["output"], "error": row["error"], "pending_gate": row["pending_gate"]}
        ],
        pending_content=row["pending_content"],
        pending_content_sha=_content_sha(row["pending_content"]),
        nodes=(
            [{"id": r["node_id"], "state": r["state"]} for r in node_rows] if node_rows else None
        ),
        content=(
            [
                b["block"] if isinstance(b["block"], dict) else json.loads(b["block"])
                for b in block_rows
            ]
            if block_rows
            else None
        ),
    )


@router.get("", response_model=list[RunResponse])
async def list_runs(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    limit: int = 20,
    agent_id: str | None = None,
) -> list[RunResponse]:
    """List the most recent runs for this workspace (optionally one agent's — A4)."""
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        if agent_id:
            rows = await conn.fetch(
                """SELECT id::text, status, profile_name, agent_id::text AS agent_id
                   FROM runs WHERE workspace_id = $1::uuid AND agent_id = $2::uuid
                   ORDER BY created_at DESC LIMIT $3""",
                ws.workspace_id,
                agent_id,
                min(limit, 100),
            )
        else:
            rows = await conn.fetch(
                """SELECT id::text, status, profile_name, agent_id::text AS agent_id
                   FROM runs WHERE workspace_id = $1::uuid
                   ORDER BY created_at DESC LIMIT $2""",
                ws.workspace_id,
                min(limit, 100),
            )
    return [
        RunResponse(
            run_id=r["id"],
            status=r["status"],
            profile_name=r["profile_name"],
            agent_id=r.get("agent_id"),
        )
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
    gate_was_open = False
    async with _state_lock:
        _cancelled_runs.add(run_id)
        event = _gate_events.get(run_id)
        if event is not None and run_id not in _gate_decisions:
            _gate_decisions[run_id] = {"decision": "reject", "edited_content": None}
            event.set()
            gate_was_open = True
    if gate_was_open:
        _publish_event(
            run_id,
            "gate_resolved",
            {"run_id": run_id, "decision": "reject", "ts": _utc_now()},
        )

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

    # A5: the DURABLE row is the record of decision. Writing it first means a
    # decision posted while the runner is down (restart, deploy) is applied when
    # the run resumes instead of being lost — so the absence of an in-process
    # waiter is no longer an error. Only an `open` row accepts a write, which is
    # the durable single-consumption guarantee.
    async with workspace_scope(pool, ws.workspace_id) as conn:
        recorded = await _record_gate_decision(
            conn, ws.workspace_id, run_id, body.decision, body.edited_content
        )

    # Single-consumption for the in-process fast path: the first decision wins; a
    # second (duplicate/stale) is rejected rather than double-resolving.
    async with _state_lock:
        event = _gate_events.get(run_id)
        already_in_memory = run_id in _gate_decisions
        if not recorded and (event is None or already_in_memory):
            # Neither a durable open row nor a fresh in-process waiter: this gate
            # was already decided (or never opened).
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A decision has already been recorded for this gate"
            )
        if event is not None and not already_in_memory:
            _gate_decisions[run_id] = {
                "decision": body.decision,
                "edited_content": body.edited_content,
            }
            event.set()
    # Protocol 1: the gate's resolution is now an explicit stream event — an SSE
    # client no longer sees awaiting_approval followed by silence until done.
    # content_sha = the content of record AFTER the decision (edited bytes on edit).
    resolved_sha = (
        _content_sha(body.edited_content)
        if body.decision == "edit" and body.edited_content is not None
        else body.content_sha
    )
    _publish_event(
        run_id,
        "gate_resolved",
        {
            "run_id": run_id,
            "decision": body.decision,
            "content_sha": resolved_sha,
            "edited": body.decision == "edit",
            "ts": _utc_now(),
        },
    )
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
                node_rows = await conn.fetch(
                    "SELECT node_id, state FROM run_nodes "
                    "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY node_id",
                    run_id,
                    ws.workspace_id,
                )
                block_rows = await conn.fetch(
                    "SELECT block FROM run_blocks "
                    "WHERE run_id = $1::uuid AND workspace_id = $2::uuid ORDER BY ord",
                    run_id,
                    ws.workspace_id,
                )
            if row is None:  # deleted between the existence check and here
                return
            # Protocol-1 snapshot: authoritative recovery surface — a client that
            # (re)connects mid-run rebuilds the DAG (nodes[]) and the output pane
            # (content[], collapsed replace semantics) from this one frame.
            yield _sse_frame(
                "snapshot",
                {
                    "run_id": row["id"],
                    "status": row["status"],
                    "pending_gate": row["pending_gate"],
                    "pending_content": row["pending_content"],
                    "protocol": 1,
                    "nodes": [{"id": r["node_id"], "state": r["state"]} for r in node_rows or []],
                    "content": [
                        b["block"] if isinstance(b["block"], dict) else json.loads(b["block"])
                        for b in block_rows or []
                    ],
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
                if (event, data) == _OVERFLOW_CLOSE:
                    # Backpressure overflow: close rather than silently drop — the
                    # client reconnects and resyncs from a fresh snapshot.
                    return
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


# ── run artifacts (A11) — file deliverables over HTTP ─────────────────────────
# Opaque-id-only resolution (no route accepts a path), realpath containment behind
# it, attachment-always + nosniff (agent-produced HTML is never rendered inline in a
# browser origin), 410 for a registered-but-gone file, tenant scoping via RLS +
# workspace_scope. These rows point at the system's highest-sensitivity PII.


@router.get("/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """List the run's registered file deliverables (schemas/run-artifact.schema.json).

    Available in ANY run state — a dossier produced before Gate 2 must be
    reviewable at the gate. Unknown/cross-tenant run → 404 (scoped query finds
    nothing; no existence oracle).
    """
    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        run_row = await conn.fetchrow(
            "SELECT 1 FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            ws.workspace_id,
        )
        rows = await conn.fetch(
            """SELECT id::text, run_id::text, rel_path, name, size_bytes, media_type,
                      sha256, node_id, created_at
               FROM run_artifacts
               WHERE run_id = $1::uuid AND workspace_id = $2::uuid
               ORDER BY created_at, rel_path""",
            run_id,
            ws.workspace_id,
        )
    if run_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    artifacts = []
    for r in rows:
        entry = {
            "artifact_id": r["id"],
            "run_id": r["run_id"],
            "name": r["name"],
            "rel_path": r["rel_path"],
            "size_bytes": r["size_bytes"],
            "media_type": r["media_type"],
            "sha256": r["sha256"],
            "created_at": r["created_at"].isoformat(),
        }
        if r["node_id"]:
            entry["node_id"] = r["node_id"]
        artifacts.append(entry)
    return {"artifacts": artifacts}


@router.get("/{run_id}/artifacts/{artifact_id}")
@limiter.limit("30/minute")
async def download_artifact(
    run_id: str,
    artifact_id: str,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
):
    """Serve one artifact's CURRENT bytes (pointer semantics), attachment-always."""
    from urllib.parse import quote

    from fastapi.responses import FileResponse

    from gtm_core.paths import workspace_content_root

    from ..artifacts import resolve_contained

    pool = request.app.state.pool
    async with workspace_scope(pool, ws.workspace_id) as conn:
        row = await conn.fetchrow(
            """SELECT rel_path, name, media_type FROM run_artifacts
               WHERE id = $1::uuid AND run_id = $2::uuid AND workspace_id = $3::uuid""",
            artifact_id,
            run_id,
            ws.workspace_id,
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")

    content_root = workspace_content_root(ws.workspace_id, request.app.state.cfg.repo_root)
    path = resolve_contained(content_root, row["rel_path"])
    if path is None:
        # Row exists but the file is gone (account edit / retention) OR the row
        # resolution escapes the root (tampered/symlink — refused, logged): the
        # client should drop the stale reference, hence 410 for the gone case.
        if (content_root / row["rel_path"]).exists():
            # Exists but escaped containment — refuse loudly in logs, 404 on wire.
            import logging

            logging.getLogger(__name__).warning(
                "artifact %s resolution escaped the workspace root — refused", artifact_id
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
        raise HTTPException(status.HTTP_410_GONE, {"code": "artifact_gone"})

    # RFC 5987 filename* with an ASCII fallback; attachment-always + nosniff.
    ascii_name = row["name"].encode("ascii", "replace").decode()
    disposition = (
        f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(row['name'], safe='')}"
    )
    return FileResponse(
        path,
        media_type=row["media_type"],
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
