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
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ..callers.dependency import require_human
from ..callers.limits import enforce_agent_daily_cap, enforce_principal_rate
from ..callers.ports import Refusal
from ..callers.principal import Principal
from ..callers.rest import require_principal
from ..database import workspace_scope
from ..deps import require_auth  # noqa: F401 — still the dep for every route not in D14
from ..ratelimit import limiter
from ..schemas import ERROR_RESPONSES, GateRequest, RunRequest, RunResponse
from ..services.runs.admission import (
    find_existing_run_id,
    insert_run_row,
    resolve_acting_agent,
    resolve_pack_for_run,
)
from ..services.runs.budget import (  # noqa: F401
    RESERVATION_ESTIMATE_CREDITS,
    RESERVATION_ESTIMATE_USD,
    _reservation_enabled,
    _reserve_or_deny,
    _settle_run_reservations,
    _track_run,
    admits,
    reserve_credits,
    settle_credits,
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
from ..services.runs.principal_admission import (
    admit_run_creation,
    audit_service_refusal,
    audit_service_refusal_from,
    authorize_read,
    read_scope_for_principal,
)
from ..services.runs.queries import (
    fetch_artifact,
    fetch_recent_runs,
    fetch_run_agent_id,
    fetch_run_artifacts,
    fetch_run_detail,
    resolve_artifact_response,
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

router = APIRouter(prefix="/runs", tags=["runs"], responses=ERROR_RESPONSES)


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("30/minute")
async def create_run(
    body: RunRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
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

    Fleet Phase A: a `kind="service"` principal may dispatch here too, narrowed by
    `admit_run_creation` (binds/refuses `body.agent_id`, refuses prompt mode) BEFORE
    `resolve_acting_agent` sees the body, and by `enforce_agent_daily_cap` after the
    budget check. The idempotent-replay fast path is scoped like every other read.
    """
    pool = request.app.state.pool
    ws = principal.workspace_id
    run_id = str(uuid.uuid4())

    if body.client_request_id is not None:
        existing_id = await find_existing_run_id(pool, ws, body.client_request_id)
        if existing_id is not None:
            detail = await fetch_run_detail(pool, ws, existing_id)
            if detail is not None:
                await authorize_read(request, principal, detail.get("agent_id"))
                return RunResponse(**detail)

    try:
        admit_run_creation(principal, body)
    except Refusal as exc:
        await audit_service_refusal_from(request.app.state.cfg, principal, request.url.path, exc)
        raise

    try:
        agent_row, profile_name = await resolve_acting_agent(pool, ws, body)
        if body.pack is not None:
            # Called for its refusals — see resolve_pack_for_run's own docstring for the
            # full ordering; the resolved variant is re-derived in the executor.
            await resolve_pack_for_run(
                request.app.state.cfg.repo_root,
                ws,
                principal.entitlement,
                profile_name,
                agent_row,
                body,
            )
        elif getattr(body, "context", None):
            from ..callers.dependency import refusal_to_http
            from ..callers.inlet_guard import DefaultInletGuard

            try:
                DefaultInletGuard().guard_context(body.context, ())
            except Refusal as exc:
                raise refusal_to_http(exc) from exc
    except HTTPException as exc:
        await audit_service_refusal(request.app.state.cfg, principal, request.url.path, exc)
        raise

    # RL-08/M-06: refuse an over-cap run before any row/slot is spent — mirrors the
    # executor's own pre-dispatch check. agent_budget_usd mirrors insert_run_row's own
    # computation below so the two agree.
    agent_id = agent_row["agent_id"] if agent_row is not None else None
    agent_budget_usd = (
        float(agent_row["monthly_budget_usd"])
        if agent_row is not None and agent_row["monthly_budget_usd"] is not None
        else None
    )
    if not await admits(pool, ws, agent_id, agent_budget_usd):
        from gtm_core import budget_status

        try:
            s = budget_status.status(profile_name)
            msg = budget_status.render(s)
        except Exception:
            msg = "Monthly cost cap reached."
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            {
                "code": "cost_cap_reached",
                "message": msg,
                "next_step": "Raise the cap or wait for the reset.",
            },
        )

    # Fleet Phase A, Task 4: a per-agent daily dispatch ceiling — narrowing only, applies
    # to every caller kind (the agent's own cap, not a service-only rule).
    await enforce_agent_daily_cap(
        pool, request.app.state.cfg, principal, agent_row, request.url.path
    )

    # Writes the queued row + payload, and enforces the per-workspace concurrency cap
    # (429 over it) in the SAME transaction. RT-04: `existed` is True only when this
    # exact instant lost an idempotency race — the fast path above already covers the
    # (overwhelmingly common) sequential-retry case.
    (
        effective_run_id,
        existed,
        run_agent_id,
        _agent_budget_usd,
        _run_language,
    ) = await insert_run_row(
        pool,
        ws,
        run_id,
        profile_name=profile_name,
        agent_row=agent_row,
        body=body,
        principal=principal,
    )
    if existed:
        detail = await fetch_run_detail(pool, ws, effective_run_id)
        if detail is not None:
            await authorize_read(request, principal, detail.get("agent_id"))
            return RunResponse(**detail)
    return RunResponse(
        run_id=effective_run_id,
        status="queued",
        profile_name=profile_name,
        agent_id=run_agent_id,
        pack=body.pack,
        variant=body.variant,
        created_at=datetime.now(UTC).isoformat(),
        principal_kind=principal.kind,
        principal_id=principal.subject,
        external_ref=getattr(body, "external_ref", None),
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UuidStr,
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
) -> RunResponse:
    """Poll a run for current status and output.

    Protocol-1 additive fields (A2): `nodes`/`content` mirror the SSE snapshot so a
    polling client rebuilds the same state a streaming client would. None on runs
    with no persisted node rows (prompt mode, pre-A2 rows).
    """
    pool = request.app.state.pool
    ws = principal.workspace_id
    detail = await fetch_run_detail(pool, ws, run_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    await authorize_read(request, principal, detail.get("agent_id"))
    return RunResponse(**detail)


@router.get("", response_model=list[RunResponse])
async def list_runs(
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    agent_id: UuidStr | None = None,
    status: str | None = None,
    external_ref: Annotated[
        str | None, Query(max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    ] = None,
) -> list[RunResponse]:
    """List the most recent runs for this workspace, optionally narrowed to one
    agent's (A4) and/or one status — e.g. ``?status=awaiting_approval`` to find every
    run parked at a gate, which the default recency ordering can otherwise push past
    ``limit`` and out of view. A bare `str`, not an enum, for the same reason
    RunResponse.status is (see schemas.py) — an unrecognized value just matches zero
    rows rather than 422ing.

    Fleet Phase A: a `kind="service"` principal whose bound agent's `read_scope` is
    `"own"` (the default) is FORCED to `agent_id=<its own agent>` — any caller-supplied
    filter is overridden, never merely checked. `read_scope="workspace"` leaves the
    filter as supplied (unrestricted, like a user).
    """
    pool = request.app.state.pool
    ws = principal.workspace_id
    if principal.kind == "service":
        read_scope = await read_scope_for_principal(pool, ws, principal)
        if read_scope != "workspace":
            agent_id = principal.agent_id
    rows = await fetch_recent_runs(
        pool, ws, limit=limit, agent_id=agent_id, status=status, external_ref=external_ref
    )
    return [RunResponse(**r) for r in rows]


@router.post("/{run_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_run(
    run_id: UuidStr,
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
) -> dict:
    """Cancel a pending, running, or gate-paused run.

    Sets status to 'canceled' with error 'canceled by user', and closes any open
    durable gate row (run_gates.state -> 'rejected') in the same write so a polling
    client sees gate=None/pending_node_id=None immediately instead of a stale open
    gate on a dead run. If the run is waiting at a gate, the in-process waiter is
    woken so the background task exits cleanly. 409 if the run is already terminal.
    """
    pool = request.app.state.pool
    ws = principal.workspace_id
    current = await run_status(pool, ws, run_id)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if principal.kind == "service":
        run_agent_id = await fetch_run_agent_id(pool, ws, run_id)
        await authorize_read(request, principal, run_agent_id, write=True)
    if current in _TERMINAL_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "run_already_terminal",
                "message": f"Run is already terminal (status={current!r})",
                "status": current,
            },
        )
    await cancel(pool, ws, run_id)
    return {"run_id": run_id, "status": "canceled"}


@router.post("/{run_id}/gate", status_code=status.HTTP_200_OK)
async def decide_gate(
    run_id: UuidStr,
    body: GateRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
) -> dict:
    """Approve, edit, or reject a gate.

    The operator sees the exact pending content in GET /runs/{id} and POSTs
    their decision here. This is the backend equivalent of the Telegram approve
    button — publish.py makes the actual call only after this approves.

    G4 (Fleet Phase A): resolves identity via `require_principal` (a service principal
    CAN authenticate here) then IMMEDIATELY calls `require_human(principal)` — before
    any gate-kind branching below — so a service principal is refused
    `403 human_approval_required` at every gate kind uniformly. The gate admits
    people only.
    """
    try:
        require_human(principal)
    except Refusal as exc:
        await audit_service_refusal_from(request.app.state.cfg, principal, request.url.path, exc)
        raise
    pool = request.app.state.pool
    ws = principal.workspace_id
    row = await fetch_open_gate(pool, ws, run_id)
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

    if body.decision == "edit" and row.get("gate_kind") == "publish":
        profile_name = row.get("profile_name")
        cfg = getattr(request.app.state, "cfg", None)
        if profile_name and cfg is not None:
            from agent.publish import (
                candidate_disclosure_lines,
                parse_publish_block,
                validate_disclosure,
            )

            original_draft = parse_publish_block(row.get("pending_content") or "")
            if original_draft and original_draft.identity_used:
                from gtm_core.paths import workspace_profiles_root

                profiles_root = workspace_profiles_root(ws, cfg.repo_root)
                lines = tuple(candidate_disclosure_lines(profiles_root, profile_name))
                reason = validate_disclosure(
                    body.edited_content or "", original_draft.identity_used, lines
                )
                if reason:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_CONTENT,
                        {"code": "disclosure_missing", "message": reason},
                    )

    if not await record_decision(pool, ws, run_id, body.decision, body.edited_content):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "gate_already_decided",
                "message": "A decision has already been recorded for this gate",
            },
        )
    publish_gate_resolved(ws, run_id, body.decision, body.content_sha, body.edited_content)
    return {"run_id": run_id, "decision": body.decision}


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: UuidStr,
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
    chunks: bool = False,
    since: int | None = None,
) -> StreamingResponse:
    """Stream run progress as Server-Sent Events (text/event-stream).

    Additive to GET /runs/{id} polling, which is unchanged. On connect emits a
    `snapshot` of current state, then live `status` / `awaiting_approval` / `done`
    events plus a ~15s `ping` heartbeat. Closing the stream never cancels the run —
    only POST /runs/{id}/cancel does that.

    `since` (protocol 2, opt-in) resumes from a durable event id — the server replays
    exactly the events after it instead of emitting a snapshot. Omit it and the
    response is byte-identical to protocol 1 (`Last-Event-ID` is deliberately ignored —
    see backend/services/runs/stream.py's module docstring).
    """
    pool = request.app.state.pool
    ws = principal.workspace_id

    # Per-workspace concurrent-stream cap — per PROCESS, not per workspace across the
    # deployment: it guards THIS worker's small asyncpg pool, and a stream is pinned to
    # the worker holding its socket.
    if _workspace_stream_count.get(ws, 0) >= _MAX_STREAMS_PER_WORKSPACE:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            {
                "code": "too_many_streams",
                "message": "Too many open run streams",
                "max": _MAX_STREAMS_PER_WORKSPACE,
            },
        )

    # 404 fast (before opening a stream). Unchanged shape for kind="user" (still one
    # fetchrow call) — agent_id is only read for a service principal, right below.
    async with workspace_scope(pool, ws) as conn:
        exists = await conn.fetchrow(
            "SELECT 1 FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid", run_id, ws
        )
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if principal.kind == "service":
        run_agent_id = await fetch_run_agent_id(pool, ws, run_id)
        await authorize_read(request, principal, run_agent_id)

    return StreamingResponse(
        run_event_stream(
            pool, ws, run_id, is_disconnected=request.is_disconnected, chunks=chunks, since=since
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
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
) -> dict:
    """List the run's registered file deliverables (schemas/run-artifact.schema.json).

    Available in ANY run state — a dossier produced before Gate 2 must be
    reviewable at the gate. Unknown/cross-tenant run → 404 (scoped query finds
    nothing; no existence oracle).
    """
    pool = request.app.state.pool
    ws = principal.workspace_id
    artifacts = await fetch_run_artifacts(pool, ws, run_id)
    if artifacts is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    if principal.kind == "service":
        run_agent_id = await fetch_run_agent_id(pool, ws, run_id)
        await authorize_read(request, principal, run_agent_id)
    return {"artifacts": artifacts}


@router.get("/{run_id}/artifacts/{artifact_id}")
@limiter.limit("30/minute")
async def download_artifact(
    run_id: UuidStr,
    artifact_id: UuidStr,
    principal: Annotated[Principal, Depends(require_principal)],
    request: Request,
    _rate: Annotated[None, Depends(enforce_principal_rate)] = None,
):
    """Serve one artifact's CURRENT bytes (pointer semantics), attachment-always."""
    pool = request.app.state.pool
    ws = principal.workspace_id
    row = await fetch_artifact(pool, ws, run_id, artifact_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    if principal.kind == "service":
        run_agent_id = await fetch_run_agent_id(pool, ws, run_id)
        await authorize_read(request, principal, run_agent_id, "Artifact not found")

    return resolve_artifact_response(
        row, ws, request.app.state.cfg.repo_root, artifact_id=artifact_id
    )
