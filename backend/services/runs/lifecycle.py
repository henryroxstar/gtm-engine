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
from .events import _utc_now, publish_run_event
from .gates import _claim_gate_decision, _content_sha, _open_gate_row
from .persistence import _fail_run
from .state import _TERMINAL_STATUSES, _cancelled_runs, _gate_decisions, _gate_events

#: A gate holds for a day. Longer than any operator wait, short enough to bound a leak.
GATE_TIMEOUT_S = 86400

#: How often a waiting gate re-reads its durable row (A5 step 3). The broker's
#: ``gtm:gate:`` wake makes the common case resolve in milliseconds; this is the
#: BACKSTOP that makes the wake an optimisation rather than a dependency — with the
#: broker entirely down a decision still applies within one tick. Correctness lives in
#: the row, latency lives in Redis.
GATE_POLL_S = 15

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
    publish_run_event(
        workspace_id, run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()}
    )


async def resume_run(pool, workspace_id: str, run_id: str) -> None:
    """Back to running after an approved gate — no `started_at`, the run already started."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute("UPDATE runs SET status = 'running' WHERE id = $1::uuid", run_id)
    publish_run_event(
        workspace_id, run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()}
    )


async def reject_run(pool, workspace_id: str, run_id: str) -> None:
    """The operator declined the gate. No `error` — a rejection is an outcome, not a fault."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute("UPDATE runs SET status = 'rejected' WHERE id = $1::uuid", run_id)
    publish_run_event(workspace_id, run_id, "done", {"run_id": run_id, "status": "rejected"})


async def complete_run(pool, workspace_id: str, run_id: str, output: str) -> None:
    """The terminal success write + the `done` frame carrying the output."""
    async with workspace_scope(pool, workspace_id) as conn:
        await conn.execute(
            "UPDATE runs SET status = 'ok', output = $2, completed_at = now() WHERE id = $1::uuid",
            run_id,
            output,
        )
    publish_run_event(
        workspace_id, run_id, "done", {"run_id": run_id, "status": "ok", "output": output}
    )


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
    publish_run_event(workspace_id, run_id, "awaiting_approval", data)

    try:
        if pending is not None:
            return pending
        # Non-blocking; a push failure never blocks the gate.
        asyncio.create_task(send_gate_push(pool, workspace_id, run_id, sentinel))
        return await _wait_for_decision(
            pool, workspace_id, run_id, event, gate=gate, node_id=node_id, durable=durable
        )
    finally:
        _gate_events.pop(run_id, None)


async def _wait_for_decision(
    pool,
    workspace_id: str,
    run_id: str,
    event: asyncio.Event,
    *,
    gate: str,
    node_id: str | None,
    durable: bool,
) -> dict | str:
    """Wait up to :data:`GATE_TIMEOUT_S` for a decision, re-reading the durable row every
    :data:`GATE_POLL_S`.

    Three things make this a loop rather than one long ``wait_for`` (A5 step 3):

    * **A wake is not a decision.** With more than one worker the operator's POST lands
      on whichever worker the load balancer picks, and that worker publishes a bare
      ``gtm:gate:`` wake — never the decision itself, which is read from the durable
      ``run_gates`` row here. A wake that arrives with nothing recorded yet (or a
      spurious one) must re-arm and keep waiting, never resolve the gate; returning an
      empty decision would read as "not approved" and silently reject an approved run.
    * **The poll is the backstop.** Every tick re-claims the row, so a decision applies
      within one tick even with the broker completely down. There is no configuration in
      which a recorded decision is not applied.
    * **A cancel can arrive from another worker**, where it only writes the row (no local
      waiter to resolve). A terminal status seen on a tick stops the wait cleanly instead
      of parking the run for the remaining 24 h.

    Cost, weighed and accepted: a gate held the full day polls twice per tick — about 11k
    single-row indexed reads over 24 h per gated run, i.e. a fraction of a query per second
    at any plausible concurrency. Backing the interval off over time would cut that, at the
    price of making rule 19's guarantee ("resolves within one tick with the broker down") a
    moving number. A flat interval keeps the guarantee exact.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + GATE_TIMEOUT_S
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            await _fail_run(pool, workspace_id, run_id, "gate timeout")
            return TIMED_OUT
        try:
            await asyncio.wait_for(event.wait(), timeout=min(GATE_POLL_S, remaining))
        except TimeoutError:
            pass
        # Clear BEFORE reading: a set that races this window leaves the event set for the
        # next iteration, so the signal is delayed by one loop at worst, never dropped.
        event.clear()

        if durable:
            # The durable row is authoritative; the in-memory dict is the fast path for
            # the same decision decide_gate just wrote — and the ONLY path for a cancel,
            # which resolves the waiter without writing a gate row.
            claimed = await _claim_gate_decision(pool, workspace_id, run_id, gate, node_id)
            if claimed is not None:
                _gate_decisions.pop(run_id, None)
                return claimed
        local = _gate_decisions.pop(run_id, None)
        if local is not None:
            return local
        if await _run_went_terminal(pool, workspace_id, run_id):
            return CANCELLED


async def _run_went_terminal(pool, workspace_id: str, run_id: str) -> bool:
    """True only when the row DEFINITELY reached a terminal state (e.g. a cancel handled
    by another worker). Fail-safe: an unreadable row reads as False, so a transient DB
    error keeps the gate open rather than abandoning a run nobody decided."""
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            row = await conn.fetchrow(
                "SELECT status FROM runs WHERE id = $1::uuid AND workspace_id = $2::uuid",
                run_id,
                workspace_id,
            )
    except Exception:  # noqa: BLE001
        return False
    status = row["status"] if row is not None else None
    return isinstance(status, str) and status in _TERMINAL_STATUSES


def approved(decision: dict) -> bool:
    """ "approve" and "edit" both proceed — an edit is approve-with-substituted-bytes, not a
    rejection. Only an explicit "reject" (or an empty/unexpected decision) stops the run."""
    return decision.get("decision") in ("approve", "edit")
