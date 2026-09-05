"""The operator's two run-control decisions: cancel a run, and resolve a gate.

Both are *inbound* decisions — a person acting on a run the background task is holding —
which is why they live together and why both take the same `_state_lock` in the same order:
mark the intent, then resolve the in-process waiter, then write the row. Split out of the
router in Phase 1b; the handlers keep the status codes and the response bodies.
"""

from __future__ import annotations

from ...database import workspace_scope
from .events import _publish_event, _utc_now
from .gates import _content_sha, _record_gate_decision
from .state import _cancelled_runs, _gate_decisions, _gate_events, _state_lock


async def run_status(pool, workspace_id: str, run_id: str) -> str | None:
    """This run's current status, or None when it is not visible to this workspace."""
    async with workspace_scope(pool, workspace_id) as conn:
        row = await conn.fetchrow(
            "SELECT status FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            workspace_id,
        )
    return None if row is None else row["status"]


async def cancel(pool, workspace_id: str, run_id: str) -> None:
    """Cancel a pending, running, or gate-paused run.

    Marked cancelled BEFORE the gate event fires, so the background task sees the flag
    whatever the scheduling order; any open gate is resolved as `reject` under the same
    lock decide_gate uses, so exactly one decision is ever consumed. The row is written
    here rather than left to the task: the task may never run, or may overwrite.
    """
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
            run_id, "gate_resolved", {"run_id": run_id, "decision": "reject", "ts": _utc_now()}
        )

    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            """UPDATE runs SET status = 'rejected', error = 'canceled by user'
               WHERE id = $1::uuid AND workspace_id = $2::uuid""",
            run_id,
            workspace_id,
        )
    # Straight to any open stream — the background task may be between awaits.
    _publish_event(
        run_id, "done", {"run_id": run_id, "status": "rejected", "error": "canceled by user"}
    )


async def fetch_open_gate(pool, workspace_id: str, run_id: str):
    """The run's status and the exact bytes currently pending at its gate."""
    async with workspace_scope(pool, workspace_id) as conn:
        return await conn.fetchrow(
            "SELECT status, pending_gate, pending_content FROM runs "
            "WHERE id = $1::uuid AND workspace_id = $2::uuid",
            run_id,
            workspace_id,
        )


def content_matches(row, content_sha: str | None) -> bool:
    """Bind the decision to the EXACT bytes the operator saw (H9). ``content_sha`` is
    required by the schema and must equal the current ``pending_content``'s hash, which
    rejects a stale/duplicate/blind decision meant for a different gate of this run. Fails
    closed when ``pending_content`` is somehow absent — its hash is None, so nothing matches.
    """
    return content_sha == _content_sha(row["pending_content"])


async def record_decision(
    pool, workspace_id: str, run_id: str, decision: str, edited_content: str | None
) -> bool:
    """Record the decision durably, then resolve the in-process waiter.

    A5: the DURABLE row is the record of decision. Writing it first means a decision posted
    while the runner is down (restart, deploy) is applied when the run resumes instead of
    being lost — so the absence of an in-process waiter is no longer an error. Only an
    ``open`` row accepts a write: the durable single-consumption guarantee.

    Returns False when neither a durable open row nor a fresh in-process waiter exists —
    the gate was already decided, or never opened — and the caller must 409.
    """
    async with workspace_scope(pool, workspace_id) as conn:
        recorded = await _record_gate_decision(conn, workspace_id, run_id, decision, edited_content)

    # Single-consumption for the in-process fast path: the first decision wins; a second
    # (duplicate/stale) is refused rather than double-resolving.
    async with _state_lock:
        event = _gate_events.get(run_id)
        already_in_memory = run_id in _gate_decisions
        if not recorded and (event is None or already_in_memory):
            return False
        if event is not None and not already_in_memory:
            _gate_decisions[run_id] = {"decision": decision, "edited_content": edited_content}
            event.set()
    return True


def publish_gate_resolved(
    run_id: str, decision: str, content_sha: str | None, edited_content: str | None
) -> None:
    """Protocol 1: the gate's resolution is an explicit stream event, so an SSE client no
    longer sees `awaiting_approval` followed by silence until `done`. The `content_sha` is
    the content of record AFTER the decision — the edited bytes on an edit."""
    resolved_sha = (
        _content_sha(edited_content)
        if decision == "edit" and edited_content is not None
        else content_sha
    )
    _publish_event(
        run_id,
        "gate_resolved",
        {
            "run_id": run_id,
            "decision": decision,
            "content_sha": resolved_sha,
            "edited": decision == "edit",
            "ts": _utc_now(),
        },
    )
