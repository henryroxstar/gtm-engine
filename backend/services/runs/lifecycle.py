"""The transitions both run lifecycles share (PRD 2026-09-01 Phase 1b).

`_execute_run` (prompt) and `_execute_pack_run` (pack) drive two different engines — an SDK
stream and the graph runner — but they make the SAME run-row transitions around them: start,
fail, reject, complete, and hold a gate. Those live here; everything the behavioral-diff
table classifies as *deliberately different* stays in its own executor
(the 2026-09-02 run-lifecycle unification note).

That boundary is the point. A shared abstraction over the work loop or the resume path is
exactly the flattening §6.4 exists to prevent, so there is none: this module knows nothing
about streams, graphs, drafts, or publishing.
"""

from __future__ import annotations

import asyncio

from ...database import workspace_scope
from ...push import send_gate_push
from .events import _publish_event, _utc_now
from .gates import _claim_gate_decision, _content_sha, _open_gate_row
from .persistence import _fail_run
from .state import _cancelled_runs, _gate_decisions, _gate_events

#: A gate holds for a day. Longer than any operator wait, short enough to bound a leak.
GATE_TIMEOUT_S = 86400

#: `hold_gate` outcomes that are not a decision. Sentinels rather than exceptions: both are
#: ordinary "stop here" paths in a background task, not errors to propagate.
CANCELLED = "__cancelled__"
TIMED_OUT = "__timed_out__"


async def start_run(pool, workspace_id: str, run_id: str) -> None:
    """running + started_at, and the `status` event that tells a subscriber the run began."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            "UPDATE runs SET status = 'running', started_at = now() WHERE id = $1::uuid",
            run_id,
        )
    _publish_event(run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()})


async def resume_run(pool, workspace_id: str, run_id: str) -> None:
    """Back to running after an approved gate — no `started_at`, the run already started."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute("UPDATE runs SET status = 'running' WHERE id = $1::uuid", run_id)
    _publish_event(run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()})


async def reject_run(pool, workspace_id: str, run_id: str) -> None:
    """The operator declined the gate. No `error` — a rejection is an outcome, not a fault."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute("UPDATE runs SET status = 'rejected' WHERE id = $1::uuid", run_id)
    _publish_event(run_id, "done", {"run_id": run_id, "status": "rejected"})


async def complete_run(pool, workspace_id: str, run_id: str, output: str) -> None:
    """The terminal success write + the `done` frame carrying the output."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            "UPDATE runs SET status = 'ok', output = $2, completed_at = now() WHERE id = $1::uuid",
            run_id,
            output,
        )
    _publish_event(run_id, "done", {"run_id": run_id, "status": "ok", "output": output})


async def rewrite_pending_content(pool, workspace_id: str, run_id: str, content: str) -> None:
    """Approve-with-edits: the approved bytes become the gate's audit row, so the record is
    what was approved rather than the draft that was shown."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            "UPDATE runs SET pending_content = $2 WHERE id = $1::uuid", run_id, content
        )


async def hold_gate(
    pool,
    workspace_id: str,
    run_id: str,
    *,
    sentinel: str,
    gate: str,
    pending_content: str,
    node_id: str | None = None,
    durable: bool = False,
) -> dict | str:
    """Open a gate, wait for the operator, and return their decision.

    Returns the decision dict, or :data:`CANCELLED` (the run was cancelled inside the
    registration window — the caller just returns) or :data:`TIMED_OUT` (nobody decided
    within :data:`GATE_TIMEOUT_S`; the run has already been failed).

    ``durable`` selects the pack path's ``run_gates`` row (behavioral-diff row 7): the row
    persists BOTH the wait and the decision, so a decision posted while the runner was down
    is claimed on resume — and, having been claimed, does not re-notify (row 9). A prompt
    run is unresumable (its SDK session dies with the process), so it deliberately opens no
    row and ``reconcile_gates`` fails it explicitly rather than pretending to resume it.

    The waiter is removed in a ``finally``: before unification the prompt path popped it on
    the approve path only, leaking one ``asyncio.Event`` per rejected or timed-out run.
    """
    pending = (
        await _claim_gate_decision(pool, workspace_id, run_id, gate, node_id) if durable else None
    )

    event = asyncio.Event()
    _gate_events[run_id] = event
    # Cancelled in the window between the runner pausing and the waiter existing: without
    # this the cancel lands on nothing and the run parks for a day.
    if run_id in _cancelled_runs:
        _cancelled_runs.discard(run_id)
        _gate_events.pop(run_id, None)
        return CANCELLED

    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """UPDATE runs
               SET status = 'awaiting_approval', pending_gate = $2, pending_content = $3
               WHERE id = $1::uuid""",
            run_id,
            sentinel,
            pending_content,
        )
    if durable and pending is None:
        await _open_gate_row(
            pool, workspace_id, run_id, gate, node_id or "", _content_sha(pending_content)
        )

    data = {
        "run_id": run_id,
        "pending_gate": sentinel,
        "pending_content": pending_content,
        # Protocol-1 additive fields (run-event.schema.json). `node_id` is present only
        # where there IS a graph node — a prompt run has no structure to name.
        "gate": gate,
    }
    if node_id is not None:
        data["node_id"] = node_id
    data["pending_content_sha"] = _content_sha(pending_content)
    _publish_event(run_id, "awaiting_approval", data)

    try:
        if pending is not None:
            return pending
        # Non-blocking; a push failure never blocks the gate.
        asyncio.create_task(send_gate_push(pool, workspace_id, run_id, sentinel))
        try:
            await asyncio.wait_for(event.wait(), timeout=GATE_TIMEOUT_S)
        except TimeoutError:
            await _fail_run(pool, workspace_id, run_id, "gate timeout")
            return TIMED_OUT
        if durable:
            # The durable row is authoritative; the in-memory dict is the fast path for the
            # same decision decide_gate just wrote.
            claimed = await _claim_gate_decision(pool, workspace_id, run_id, gate, node_id)
            decision = claimed or _gate_decisions.pop(run_id, {})
            _gate_decisions.pop(run_id, None)
            return decision
        return _gate_decisions.pop(run_id, {})
    finally:
        _gate_events.pop(run_id, None)


def approved(decision: dict) -> bool:
    """ "approve" and "edit" both proceed — an edit is approve-with-substituted-bytes, not a
    rejection. Only an explicit "reject" (or an empty/unexpected decision) stops the run."""
    return decision.get("decision") in ("approve", "edit")
