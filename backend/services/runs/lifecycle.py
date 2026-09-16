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
from datetime import datetime

from ...database import workspace_scope
from ...push import send_gate_push
from .events import _utc_now, publish_run_event
from .gates import _claim_gate_decision, _content_sha, _open_gate_row
from .persistence import _fail_run
from .state import _TERMINAL_STATUSES, _cancelled_runs, _gate_decisions, _gate_events

#: RL-03: every terminal-row write below (the operator-decision transitions; the
#: pack/prompt executors' own admission and completion writes) guards its UPDATE with
#: this clause — mirroring cancel()'s own guard (decisions.py) — so a write racing a
#: terminal row (most often a cancel from another worker, or another worker's own
#: terminal write) can never resurrect it. Derived from `_TERMINAL_STATUSES` so a fifth
#: terminal status needs one edit, not six.
_NOT_TERMINAL_SQL = (
    "status NOT IN (" + ", ".join(f"'{s}'" for s in sorted(_TERMINAL_STATUSES)) + ")"
)

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
    """running + started_at, and the `status` event that tells a subscriber the run began.

    RL-03: guarded against a terminal row — a cancel that lands between admission and
    this write (another worker, or a raced in-process cancel) must not be resurrected
    back to 'running'. The event is published only when the write actually matched."""
    async with workspace_scope(pool, workspace_id) as conn:
        updated = await conn.fetchrow(
            f"UPDATE runs SET status = 'running', started_at = now() "  # nosec B608 — status list is a module constant, never external input
            f"WHERE id = $1::uuid AND {_NOT_TERMINAL_SQL} RETURNING id",
            run_id,
        )
    if updated is None:
        return
    publish_run_event(
        workspace_id, run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()}
    )


async def resume_run(pool, workspace_id: str, run_id: str) -> None:
    """Back to running after an approved gate — no `started_at`, the run already started.

    RL-03: same terminal guard as start_run — a cancel landing between the gate decision
    and this write must not be resurrected."""
    async with workspace_scope(pool, workspace_id) as conn:
        updated = await conn.fetchrow(
            f"UPDATE runs SET status = 'running', "  # nosec B608 — status list is a module constant, never external input
            f"pending_gate = NULL, pending_content = NULL "
            f"WHERE id = $1::uuid AND {_NOT_TERMINAL_SQL} RETURNING id",
            run_id,
        )
    if updated is None:
        return
    publish_run_event(
        workspace_id, run_id, "status", {"run_id": run_id, "status": "running", "ts": _utc_now()}
    )


async def reject_run(pool, workspace_id: str, run_id: str) -> None:
    """The operator declined the gate. No `error` — a rejection is an outcome, not a fault.

    RL-03: guarded against a terminal row — a duplicate/late decision applied after the
    run already ended some other way must not overwrite it."""
    async with workspace_scope(pool, workspace_id) as conn:
        updated = await conn.fetchrow(
            f"UPDATE runs SET status = 'rejected', "  # nosec B608 — status list is a module constant, never external input
            f"pending_gate = NULL, pending_content = NULL "
            f"WHERE id = $1::uuid AND {_NOT_TERMINAL_SQL} RETURNING id",
            run_id,
        )
    if updated is None:
        return
    publish_run_event(workspace_id, run_id, "done", {"run_id": run_id, "status": "rejected"})


async def complete_run(pool, workspace_id: str, run_id: str, output: str) -> None:
    """The terminal success write + the `done` frame carrying the output.

    RL-03: guarded against a terminal row — a run cancelled mid-node still finishes its
    in-flight stage (the runner is not interrupted), and that completion must not
    resurrect an already-`canceled` row."""
    async with workspace_scope(pool, workspace_id) as conn:
        updated = await conn.fetchrow(
            f"UPDATE runs SET status = 'ok', output = $2, completed_at = now(), "  # nosec B608 — status list is a module constant, never external input
            f"pending_gate = NULL, pending_content = NULL "
            f"WHERE id = $1::uuid AND {_NOT_TERMINAL_SQL} RETURNING id",
            run_id,
            output,
        )
    if updated is None:
        return
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

    A recorded approval or edit is claimed only if it was made over ``pending_content`` (H9);
    a reject always is. On a reclaim the gated node re-runs and may write different bytes; a
    stale approval is then left unclaimed, and opening the row below discards it — the gate
    waits for a fresh one.

    The waiter is removed in a ``finally``: before unification the prompt path popped it on
    the approve path only, leaking one ``asyncio.Event`` per rejected or timed-out run.
    """
    content_sha = _content_sha(pending_content)
    pending = (
        await _claim_gate_decision(pool, workspace_id, run_id, gate, node_id, content_sha)
        if durable
        else None
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
        updated = await conn.fetchrow(
            "UPDATE runs\n"  # nosec B608 — status list is a module constant, never external input
            "               SET status = 'awaiting_approval', pending_gate = $2, "
            "pending_content = $3\n"
            f"               WHERE id = $1::uuid AND {_NOT_TERMINAL_SQL}\n"
            "               RETURNING id",
            run_id,
            sentinel,
            pending_content,
        )
    if updated is None:
        # RL-03: the row went terminal (e.g. a cancel handled by another worker) in the
        # tiny window between the _cancelled_runs check above and this UPDATE. Same
        # outcome as that check: stop here, before a durable gate row is opened or a
        # push is sent.
        _cancelled_runs.discard(run_id)
        _gate_events.pop(run_id, None)
        return CANCELLED
    # RL-13/ST-06: whether this open is something NEW to tell the operator about.
    # Non-durable (prompt mode) and a fresh/genuinely-changed durable open both are; the
    # one case that is not is a RESUME (boot reconcile, lease reclaim) landing on a
    # row already `open` on these EXACT bytes — nothing has changed since the operator
    # was last shown it, so there is nothing to re-announce. `pending is not None` (an
    # already-decided row claimed above) is unaffected by this flag — it keeps its own
    # existing behaviour below (the event still fires; the ONE thing that never happens
    # for a decided gate is a push, which the `pending is not None` return already skips).
    #
    # RL-12 (Gap 2): `opened_at`/`db_now` are the durable row's actual open time and a
    # same-moment server clock reading (None/None for the non-durable prompt path, which
    # opens no row at all) — threaded into `_wait_for_decision` so a RESUME measures
    # GATE_TIMEOUT_S from when the gate was ACTUALLY opened, not from now.
    reopened = True
    opened_at = db_now = None
    if durable and pending is None:
        reopened, opened_at, db_now = await _open_gate_row(
            pool, workspace_id, run_id, gate, node_id or "", content_sha
        )

    if reopened:
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
        data["pending_content_sha"] = content_sha
        publish_run_event(workspace_id, run_id, "awaiting_approval", data)

    try:
        if pending is not None:
            return pending
        if reopened:
            # Non-blocking; a push failure never blocks the gate. Skipped on a resume
            # that landed on an unchanged, still-open gate (RL-13/ST-06) — the operator
            # was already pushed once for these exact bytes.
            asyncio.create_task(send_gate_push(pool, workspace_id, run_id, gate, node_id=node_id))
        return await _wait_for_decision(
            pool,
            workspace_id,
            run_id,
            event,
            gate=gate,
            node_id=node_id,
            durable=durable,
            content_sha=content_sha,
            opened_at=opened_at,
            db_now=db_now,
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
    content_sha: str | None,
    opened_at: datetime | None = None,
    db_now: datetime | None = None,
) -> dict | str:
    """Wait up to :data:`GATE_TIMEOUT_S` for a decision, re-reading the durable row every
    :data:`GATE_POLL_S` (an approval is claimed only when made over ``content_sha``, the held
    bytes; a reject always is).

    RL-12 (Gap 2): ``GATE_TIMEOUT_S`` bounds how long an operator decision may be
    outstanding, measured from when the gate was ACTUALLY opened — not from whenever this
    process happens to (re-)enter this wait. A boot reconcile or a lease reclaim re-enters
    ``hold_gate`` for a gate that may have been open for hours; without this the wait clock
    would silently restart at a fresh ``GATE_TIMEOUT_S`` on every such resume, and a run
    parked at a gate across enough reclaims would never actually time out. ``opened_at`` +
    ``db_now`` (both read together by ``_open_gate_row``, one DB round trip) convert the
    durable open time into ``loop.time()``'s monotonic frame ONCE, right here at wait-start
    — never by re-querying ``now()`` on every poll tick, which would make the timeout a
    moving target. Both None (the non-durable prompt path, which opens no row at all — its
    behaviour is deliberately unchanged) falls back to the original "fresh GATE_TIMEOUT_S
    from right now". For a freshly opened/reset durable gate ``opened_at == db_now`` (the
    same ``now()`` evaluated once in ``_open_gate_row``'s own statement), so this reduces to
    exactly the same deadline as before — no behaviour change for the common case.

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
    if opened_at is not None and db_now is not None:
        # Computed ONCE, here, at wait-start — never re-derived per tick. `opened_at` and
        # `db_now` came from the SAME query (_open_gate_row), so this offset is exact: for
        # a fresh/reset gate it is ~0 (identical `GATE_TIMEOUT_S` deadline as before); for a
        # resume onto a gate opened hours ago it is negative, correctly shrinking the wait
        # to whatever time actually remains.
        deadline = loop.time() + (opened_at - db_now).total_seconds() + GATE_TIMEOUT_S
    else:
        deadline = loop.time() + GATE_TIMEOUT_S
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            await _fail_run(pool, workspace_id, run_id, "gate timeout", error_code="gate_timeout")
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
            claimed = await _claim_gate_decision(
                pool, workspace_id, run_id, gate, node_id, content_sha
            )
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
