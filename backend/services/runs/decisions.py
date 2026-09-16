"""The operator's two run-control decisions: cancel a run, and resolve a gate.

Both are *inbound* decisions — a person acting on a run the background task is holding —
which is why they live together and why both take the same `_state_lock` in the same order:
mark the intent, then resolve the in-process waiter, then write the row. Split out of the
router in Phase 1b; the handlers keep the status codes and the response bodies.
"""

from __future__ import annotations

from ...broker import publish_gate_wake
from ...database import workspace_scope
from .events import _utc_now, publish_run_event
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

    Marked cancelled BEFORE the DB write, so the background task sees the flag whatever
    the scheduling order. The `runs` row write is the single source of truth for whether
    this call does anything: only a run that is NOT already terminal is written, in the
    SAME `workspace_scope` transaction that also closes any open durable gate row — so a
    run parked at a gate never leaves an `open` `run_gates` row behind a `canceled` run
    (RL-04), and a polling client sees `gate=None`/`pending_node_id=None` immediately.
    The same write also clears the raw `pending_gate`/`pending_content` columns (RL-09's
    pattern in `resume_run`/`reject_run`/`complete_run`/`_fail_run` — cancel's own write
    was the one terminal path that had not picked it up, caught by the tests/live p2
    acceptance suite): a cancelled run must not keep serving the pending draft bytes it
    was cancelled out of.

    A cancel is NOT a gate decision and is never represented as one: nothing is written
    to `_gate_decisions`. The in-process waiter, if one exists, is only woken —
    `_wait_for_decision` (lifecycle.py) re-reads the row on every wake and poll tick,
    finds the row `canceled` (a `_TERMINAL_STATUSES` member) via `_run_went_terminal`,
    and returns `CANCELLED` on its own. Injecting a `{"decision": "reject", ...}` here,
    as before, made the executor ALSO call `reject_run` on top of it — a second `done`
    frame for the same run (ST-15) — which is exactly what this avoids.
    """
    async with _state_lock:
        _cancelled_runs.add(run_id)
        event = _gate_events.get(run_id)

    async with workspace_scope(pool, workspace_id) as conn:
        updated = await conn.fetchrow(
            """UPDATE runs
               SET status = 'canceled', error = 'canceled by user', completed_at = now(),
                   pending_gate = NULL, pending_content = NULL
               WHERE id = $1::uuid AND workspace_id = $2::uuid
                 AND status NOT IN ('ok', 'failed', 'rejected', 'canceled')
               RETURNING id""",
            run_id,
            workspace_id,
        )
        if updated is None:
            # Already terminal. The router's own check runs BEFORE calling cancel() and
            # is the primary guard (409); this is a defensive backstop only — nothing
            # left to close, wake, or announce.
            return
        await conn.execute(
            """UPDATE run_gates SET state = 'rejected', decided_at = now(), applied_at = now()
               WHERE run_id = $1::uuid AND workspace_id = $2::uuid AND state = 'open'""",
            run_id,
            workspace_id,
        )

    if event is not None:
        event.set()
    # A5: the run may be held by ANOTHER worker, where there is no local waiter to
    # resolve — wake it so it sees the terminal row now rather than on its next poll.
    await publish_gate_wake(run_id)
    # Straight to any open stream — the background task may be between awaits.
    publish_run_event(
        workspace_id,
        run_id,
        "done",
        {"run_id": run_id, "status": "canceled", "error": "canceled by user"},
    )


async def fetch_open_gate(pool, workspace_id: str, run_id: str):
    """The run's status, the exact bytes currently pending at its gate, and — for a pack
    run — the open durable gate's kind (``gate_kind``; NULL for a prompt run)."""
    async with workspace_scope(pool, workspace_id) as conn:
        return await conn.fetchrow(
            "SELECT r.status, r.pending_gate, r.pending_content, "
            "(SELECT g.gate FROM run_gates g WHERE g.run_id = r.id AND g.state = 'open' "
            " ORDER BY g.opened_at DESC LIMIT 1) AS gate_kind "
            "FROM runs r WHERE r.id = $1::uuid AND r.workspace_id = $2::uuid",
            run_id,
            workspace_id,
        )


def refuses_edit(row, decision: str, edited_content: str | None) -> bool:
    """True when this decision carries edited bytes for a gate that has nothing to apply
    them to. A ``review`` gate (e.g. marketing's case-study — any pack gate with no draft file of its
    own) only shows a stub; there is no draft for edited bytes to replace, so an edit
    there would be recorded and silently never honoured (client issue #241 Q6). Refusing is
    the honest answer until such a gate has a promotable draft. Plan/enroll gates and
    prompt runs (no durable gate row) are unaffected."""
    if row.get("gate_kind") != "review":
        return False
    return decision == "edit" or edited_content is not None


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
    if recorded and event is None:
        # A5: the durable row is written but this worker is not the one holding the run.
        # Wake the worker that is — with a bare wake, never the decision: it re-reads the
        # row, which is the only place a gate decision is ever read from. If the wake is
        # lost (broker down), the holder's GATE_POLL_S backstop applies it anyway.
        await publish_gate_wake(run_id)
    return True


def publish_gate_resolved(
    workspace_id: str,
    run_id: str,
    decision: str,
    content_sha: str | None,
    edited_content: str | None,
) -> None:
    """Protocol 1: the gate's resolution is an explicit stream event, so an SSE client no
    longer sees `awaiting_approval` followed by silence until `done`. The `content_sha` is
    the content of record AFTER the decision — the edited bytes on an edit."""
    resolved_sha = (
        _content_sha(edited_content)
        if decision == "edit" and edited_content is not None
        else content_sha
    )
    publish_run_event(
        workspace_id,
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
