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

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..database import workspace_scope
from ..deps import WorkspaceCtx, require_auth
from ..ratelimit import limiter
from ..schemas import GateRequest, RunRequest, RunResponse
from ..services.runs.admission import (
    find_existing_run_id,
    insert_run_row,
    resolve_acting_agent,
    resolve_pack_for_run,
)
from ..services.runs.budget import (  # noqa: F401
    RESERVATION_ESTIMATE_USD,
    _reservation_enabled,
    _reserve_or_deny,
    _settle_run_reservations,
    _track_run,
    admits,
)
from ..services.runs.decisions import (
    cancel,
    content_matches,
    fetch_open_gate,
    publish_gate_resolved,
    record_decision,
    refuses_edit,
    run_status,
)
from ..services.runs.events import (  # noqa: F401
    _publish_event,
    _sse_frame,
    _subscribe,
    _unsubscribe,
    publish_run_event,
    start_relay,
    stop_relay,
)
from ..services.runs.executor import _execute_run  # noqa: F401
from ..services.runs.gates import (  # noqa: F401
    _DECISION_STATE,
    _claim_gate_decision,
    _content_sha,
    _open_gate_row,
    _record_gate_decision,
)
from ..services.runs.pack_executor import _GATE_PLAN_SENTINEL, _execute_pack_run  # noqa: F401
from ..services.runs.persistence import (  # noqa: F401
    _WIRE_NODE_STATE,
    _fail_run,
    _file_block,
    _persist_node,
    _upsert_block,
)
from ..services.runs.queries import (
    attachment_headers,
    fetch_artifact,
    fetch_recent_runs,
    fetch_run_artifacts,
    fetch_run_detail,
    resolve_artifact_path,
)
from ..services.runs.queue import (  # noqa: F401
    LEASE_S,
    MAX_ATTEMPTS,
    WORKER_ID,
    claim_loop,
    claim_next,
    dispatch_claimed,
    heartbeat_loop,
    heartbeat_once,
    stop_task,
)
from ..services.runs.reconcile import _PACK_PROMPT_RE, reconcile_gates  # noqa: F401
from ..services.runs.state import (  # noqa: F401
    _MAX_CONCURRENT_RUNS_PER_WORKSPACE,
    _MAX_STREAMS_PER_WORKSPACE,
    _STREAM_QUEUE_MAX,
    _TERMINAL_STATUSES,
    _background_tasks,
    _cancelled_runs,
    _gate_decisions,
    _gate_events,
    _release_run_slot,
    _run_subscribers,
    _state_lock,
    _track,
    _workspace_runs,
    _workspace_stream_count,
    drain_background_tasks,
)

# The run lifecycle lives in the service package; the names below are imported
# here so this module keeps its pre-split import surface (backend/main.py and the
# test-suite import them from here). PRD 2026-09-01 §7 Phase 1a — pure motion.
from ..services.runs.stream import run_event_stream
from ..types import UuidStr

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("30/minute")
async def create_run(
    body: RunRequest,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> RunResponse:
    """Queue a pipeline run. Returns immediately; poll GET /runs/{id} for status.

    Two modes (RunRequest): prompt mode (unchanged) and pack mode, which validates
    fail-closed BEFORE any slot/spend is touched, in the normative A1/A12 order —
    request-shaped errors before profile-shaped ones: 404 unknown variant → 403
    pack_not_activated → 403 entitlement_required → 422 missing_settings → 422
    pack_not_ready (blocked only; degraded proceeds) → 402 cost_cap_reached (RL-08/M-06,
    both modes) → 429 too_many_concurrent_runs (inside insert_run_row).

    A5: the handler ENQUEUES (writes a `queued` row + payload) instead of spawning the
    pipeline in this process. A worker's claim loop takes it under FOR UPDATE SKIP
    LOCKED, so a run survives the death of the process that accepted it and any worker
    can execute it. Every admission refusal above still happens before a row exists.

    RT-04: an optional `client_request_id` makes this idempotent. A fast-path replay check
    runs FIRST — before any admission check — so a retry of an already-admitted request
    skips every refusal (pack/entitlement/budget/cap) that already ran the first time and
    simply returns the original run's current state. The actual concurrency-safety
    backstop for two requests racing at the same instant lives inside insert_run_row's own
    `ON CONFLICT` (this fast path can't close that window; the transaction can).
    """
    pool = request.app.state.pool
    run_id = str(uuid.uuid4())

    if body.client_request_id is not None:
        existing_id = await find_existing_run_id(pool, ws.workspace_id, body.client_request_id)
        if existing_id is not None:
            detail = await fetch_run_detail(pool, ws.workspace_id, existing_id)
            if detail is not None:
                return RunResponse(**detail)

    agent_row, profile_name = await resolve_acting_agent(pool, ws.workspace_id, body)
    if body.pack is not None:
        # Called for its refusals: an unknown/unactivated/unentitled/unconfigured/not-ready
        # variant raises here, before any state is reserved. The resolved variant itself is
        # re-derived inside the executor, under the workspace-scoped Config.
        await resolve_pack_for_run(
            request.app.state.cfg.repo_root,
            ws.workspace_id,
            ws.entitlement,
            profile_name,
            agent_row,
            body,
        )

    # RL-08/M-06: refuse an over-cap run synchronously, before any row is written or a
    # concurrency slot is spent — the same fail-closed verdict the executor's own
    # _budget_guard/_reserve_or_deny check before every dispatch batch (unchanged,
    # defense-in-depth against a cap exhausted by other runs in the admission→dispatch
    # gap). agent_budget_usd mirrors insert_run_row's own computation below so the two
    # agree; the two-line duplication is cheaper than reordering insert_run_row's return.
    agent_id = agent_row["agent_id"] if agent_row is not None else None
    agent_budget_usd = (
        float(agent_row["monthly_budget_usd"])
        if agent_row is not None and agent_row["monthly_budget_usd"] is not None
        else None
    )
    if not await admits(pool, ws.workspace_id, agent_id, agent_budget_usd):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            {"code": "cost_cap_reached", "message": "monthly cost cap reached"},
        )

    # Writes the queued row + payload, and enforces the per-workspace concurrency cap
    # (429 over it) in the SAME transaction, so a refusal leaves no row behind. RT-04:
    # `existed` is True only when this exact instant lost an idempotency race — the
    # fast-path check above already covers the (overwhelmingly common) sequential-retry
    # case, so reaching `existed=True` here means two requests with the same
    # client_request_id truly overlapped in time.
    (
        effective_run_id,
        existed,
        run_agent_id,
        _agent_budget_usd,
        _run_language,
    ) = await insert_run_row(
        pool,
        ws.workspace_id,
        run_id,
        profile_name=profile_name,
        agent_row=agent_row,
        body=body,
    )
    if existed:
        detail = await fetch_run_detail(pool, ws.workspace_id, effective_run_id)
        if detail is not None:
            return RunResponse(**detail)
    return RunResponse(
        run_id=effective_run_id,
        status="queued",
        profile_name=profile_name,
        agent_id=run_agent_id,
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> RunResponse:
    """Poll a run for current status and output.

    Protocol-1 additive fields (A2): `nodes`/`content` mirror the SSE snapshot so a
    polling client rebuilds the same state a streaming client would. None on runs
    with no persisted node rows (prompt mode, pre-A2 rows).
    """
    detail = await fetch_run_detail(request.app.state.pool, ws.workspace_id, run_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return RunResponse(**detail)


@router.get("", response_model=list[RunResponse])
async def list_runs(
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    agent_id: UuidStr | None = None,
    status: str | None = None,
) -> list[RunResponse]:
    """List the most recent runs for this workspace, optionally narrowed to one
    agent's (A4) and/or one status — e.g. ``?status=awaiting_approval`` to find every
    run parked at a gate, which the default recency ordering can otherwise push past
    ``limit`` and out of view. A bare `str`, not an enum, for the same reason
    RunResponse.status is (see schemas.py) — an unrecognized value just matches zero
    rows rather than 422ing.
    """
    rows = await fetch_recent_runs(
        request.app.state.pool,
        ws.workspace_id,
        limit=limit,
        agent_id=agent_id,
        status=status,
    )
    return [RunResponse(**r) for r in rows]


@router.post("/{run_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_run(
    run_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """Cancel a pending, running, or gate-paused run.

    Sets status to 'canceled' with error 'canceled by user', and closes any open
    durable gate row (run_gates.state -> 'rejected') in the same write so a polling
    client sees gate=None/pending_node_id=None immediately instead of a stale open
    gate on a dead run. If the run is waiting at a gate, the in-process waiter is
    woken so the background task exits cleanly. 409 if the run is already terminal.
    """
    pool = request.app.state.pool
    current = await run_status(pool, ws.workspace_id, run_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if current in _TERMINAL_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "run_already_terminal",
                "message": f"Run is already terminal (status={current!r})",
                "status": current,
            },
        )
    await cancel(pool, ws.workspace_id, run_id)
    return {"run_id": run_id, "status": "canceled"}


@router.post("/{run_id}/gate", status_code=status.HTTP_200_OK)
async def decide_gate(
    run_id: UuidStr,
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
    row = await fetch_open_gate(pool, ws.workspace_id, run_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if row["status"] != "awaiting_approval":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "gate_not_open",
                "message": f"Run is not awaiting approval (status={row['status']!r})",
                "status": row["status"],
            },
        )
    if not content_matches(row, body.content_sha):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "content_sha_mismatch",
                "message": "content_sha does not match the current gate content "
                "(stale or superseded)",
            },
        )
    if refuses_edit(row, body.decision, body.edited_content):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This gate has no editable draft — edited content would not be applied. "
            "Approve or reject it.",
        )
    if not await record_decision(pool, ws.workspace_id, run_id, body.decision, body.edited_content):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "gate_already_decided",
                "message": "A decision has already been recorded for this gate",
            },
        )
    publish_gate_resolved(
        ws.workspace_id, run_id, body.decision, body.content_sha, body.edited_content
    )
    return {"run_id": run_id, "decision": body.decision}


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
    chunks: bool = False,
    since: int | None = None,
) -> StreamingResponse:
    """Stream run progress as Server-Sent Events (text/event-stream).

    Additive to GET /runs/{id} polling, which is unchanged. On connect emits a
    `snapshot` of current state, then live `status` / `awaiting_approval` / `done`
    events plus a ~15s `ping` heartbeat. The the billing-service client sends Authorization:
    Bearer (CORS already allows it). Closing the stream never cancels the run —
    only POST /runs/{id}/cancel does that.

    `since` (protocol 2, opt-in) resumes from a durable event id — the server replays
    exactly the events after it instead of emitting a snapshot. Omit it and the response
    is byte-identical to protocol 1. It is a query parameter rather than the
    `Last-Event-ID` header on purpose: a protocol-1 client's per-connection seq is
    indistinguishable on the wire from a protocol-2 durable id, and a transport that
    echoed the header automatically would opt a client into a shape it never asked for.
    """
    pool = request.app.state.pool

    # Per-workspace concurrent-stream cap. The asyncpg pool is small (max 10) and
    # streams are long-lived; the stream itself holds no DB connection while idle
    # (snapshot read below, then served from the in-memory queue).
    # The counter is per PROCESS, not per workspace across the deployment: it guards THIS
    # worker's pool, which is the resource at risk, and a stream is pinned to the worker
    # holding its socket. At BACKEND_WORKERS > 1 the effective per-workspace ceiling is
    # N x this — unlike the run cap, which counts in SQL because it bounds spend.
    if _workspace_stream_count.get(ws.workspace_id, 0) >= _MAX_STREAMS_PER_WORKSPACE:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "code": "too_many_streams",
                "message": "Too many open run streams",
                "max": _MAX_STREAMS_PER_WORKSPACE,
            },
        )

    # 404 fast (before opening a stream) if the run isn't visible to this workspace.
    async with workspace_scope(pool, ws.workspace_id) as conn:
        exists = await conn.fetchrow(
            "SELECT 1 FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            ws.workspace_id,
        )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")

    return StreamingResponse(
        run_event_stream(
            pool,
            ws.workspace_id,
            run_id,
            is_disconnected=request.is_disconnected,
            chunks=chunks,
            since=since,
        ),
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
    run_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
) -> dict:
    """List the run's registered file deliverables (schemas/run-artifact.schema.json).

    Available in ANY run state — a dossier produced before Gate 2 must be
    reviewable at the gate. Unknown/cross-tenant run → 404 (scoped query finds
    nothing; no existence oracle).
    """
    artifacts = await fetch_run_artifacts(request.app.state.pool, ws.workspace_id, run_id)
    if artifacts is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return {"artifacts": artifacts}


@router.get("/{run_id}/artifacts/{artifact_id}")
@limiter.limit("30/minute")
async def download_artifact(
    run_id: UuidStr,
    artifact_id: UuidStr,
    ws: Annotated[WorkspaceCtx, Depends(require_auth)],
    request: Request,
):
    """Serve one artifact's CURRENT bytes (pointer semantics), attachment-always."""
    from fastapi.responses import FileResponse

    from gtm_core.paths import workspace_content_root

    row = await fetch_artifact(request.app.state.pool, ws.workspace_id, run_id, artifact_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")

    content_root = workspace_content_root(ws.workspace_id, request.app.state.cfg.repo_root)
    path, reason = resolve_artifact_path(content_root, row["rel_path"])
    if path is None:
        if reason == "escaped":
            import logging

            logging.getLogger(__name__).warning(
                "artifact %s resolution escaped the workspace root — refused", artifact_id
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
        raise HTTPException(status.HTTP_410_GONE, {"code": "artifact_gone"})

    return FileResponse(path, media_type=row["media_type"], headers=attachment_headers(row["name"]))
