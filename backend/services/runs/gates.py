"""Durable gates (A5, V017 ``run_gates``): the row persists BOTH the wait and the
decision, so a restart mid-gate strands nothing and a decision posted while the runner
was down is applied on resume. ``_content_sha`` binds every approval to the exact bytes
shown (H9)."""

from __future__ import annotations

import hashlib
from datetime import datetime

from ...database import workspace_scope


def _content_sha(text: str | None) -> str | None:
    """sha256 of gate content — binds an approval to the exact bytes shown (H9)."""
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── durable gates (A5 step 1, V017 run_gates) ────────────────────────────────
# The in-memory _gate_events/_gate_decisions above stay as the fast in-process
# path; run_gates is the DURABLE record of both the WAIT and the DECISION. That
# pairing is what survives a restart: persisting only the wait would still lose a
# decision posted while the runner was down.

_DECISION_STATE = {"approve": "approved", "edit": "edited", "reject": "rejected"}


async def _open_gate_row(
    pool, workspace_id: str, run_id: str, gate: str, node_id: str, content_sha: str | None
) -> tuple[bool, datetime | None, datetime | None]:
    """Record an open gate. Re-opening the same (run, gate, node) resets the row so a
    prior decision can never be re-applied to a later gate of the same node — but ONLY
    when something actually changed. A RESUME (boot reconcile, lease reclaim) that finds
    the row already ``open`` on these EXACT bytes has nothing to reset: the operator was
    already shown this gate and hasn't decided yet, so there is nothing new to announce.

    RL-12 (Gap 1): the read-decide-write is ONE atomic statement, not a SELECT followed
    by a separate UPSERT — two statements let a decision recorded by another worker land
    in the gap between them, where the stale SELECT still read ``state='open'`` (or read a
    just-superseded state) and the blind UPSERT would then reset a row that had just been
    decided, destroying the decision rather than merely delaying it. The ``ON CONFLICT ...
    DO UPDATE ... WHERE`` clause below is evaluated by Postgres against the row's current
    COMMITTED state at execution time — never a value this function read earlier — so
    there is no window for another transaction to interleave. The WHERE condition is the
    negation of ``_claim_gate_decision``'s own claimability predicate (a decided-but-
    unapplied row must be left for that function to claim, never reset out from under it)
    OR-ed with the pre-existing "already open on identical bytes" no-op.

    Returns ``(changed, opened_at, db_now)``:

    * ``changed`` is True when the row was inserted, or genuinely reset (a stale/no-longer-
      claimable decision, or a changed ``content_sha``, discarded for a fresh one) — the
      "something changed, tell the operator" case ``hold_gate`` uses to decide whether to
      re-emit ``awaiting_approval`` and send a gate push. False covers BOTH the original
      no-op (an already-open row on identical bytes) and the new one (a row that is now
      claimable and must be left for ``_claim_gate_decision``'s next poll, not reset) —
      ``hold_gate`` must still WAIT on it either way, just without re-announcing it.
    * ``opened_at``/``db_now`` (RL-12 Gap 2) are the row's ACTUAL open time and a same-
      moment server clock reading, fetched together (one round trip either way) so
      ``_wait_for_decision`` can convert the durable open time into its ``loop.time()``
      frame ONCE at wait-start instead of measuring the timeout from whenever this process
      happened to re-enter the wait. For a fresh insert or a genuine reset both columns
      come from the SAME ``now()`` evaluated once in that statement (so they are equal —
      the timeout reduces to exactly ``GATE_TIMEOUT_S`` from right now); for a left-alone
      row the companion SELECT reports the row's ORIGINAL ``opened_at`` against a fresh
      ``now()``, so a resume correctly sees less time remaining. None/None only when the
      row could not be read at all (should not happen — the statement above always leaves
      a row behind) or the best-effort exception fallback fires.

    Best-effort: a DB error here must never break the gate wait, and defaults to
    ``(True, None, None)`` (announce, fresh timeout) rather than silently swallowing a
    real notification."""
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            row = await conn.fetchrow(
                """INSERT INTO run_gates(run_id, gate, node_id, workspace_id, content_sha, state)
                   VALUES($1::uuid, $2, $3, $4::uuid, $5, 'open')
                   ON CONFLICT (run_id, gate, node_id) DO UPDATE
                     SET state = 'open', content_sha = EXCLUDED.content_sha,
                         opened_at = now(), decided_at = NULL, applied_at = NULL,
                         edited_content = NULL
                     WHERE NOT (
                             run_gates.state <> 'open' AND run_gates.applied_at IS NULL
                             AND (run_gates.state = 'rejected'
                                  OR run_gates.content_sha = EXCLUDED.content_sha)
                           )
                       AND NOT (run_gates.state = 'open'
                                AND run_gates.content_sha = EXCLUDED.content_sha)
                   RETURNING opened_at, now() AS db_now""",
                run_id,
                gate,
                node_id,
                workspace_id,
                content_sha,
            )
            if row is not None:
                return True, *_clock_pair(row["opened_at"], row["db_now"])
            # The WHERE clause skipped the write — either the no-op or the newly-claimable
            # case. Either way the row is left exactly as it was; report its real open time.
            existing = await conn.fetchrow(
                """SELECT opened_at, now() AS db_now FROM run_gates
                   WHERE run_id = $1::uuid AND gate = $2 AND node_id = $3""",
                run_id,
                gate,
                node_id,
            )
            if existing is None:  # pragma: no cover — the statement above always leaves a row
                return True, None, None
            return False, *_clock_pair(existing["opened_at"], existing["db_now"])
    except Exception:  # noqa: BLE001 — durability is additive; never break the gate path
        return True, None, None  # nosec B110 — fail toward announcing, not silently skipping


def _clock_pair(opened_at, db_now) -> tuple[datetime | None, datetime | None]:
    """Trust ``opened_at``/``db_now`` only as a matched pair of real timestamps — never
    individually. A real connection's ``RETURNING``/``SELECT`` always yields two
    ``datetime`` values (the row's ``opened_at`` is ``NOT NULL``); anything else (a test
    double that does not model real column types, a future column-shape change) must fall
    back to BOTH None, which ``_wait_for_decision`` reads as "no durable open time known"
    and uses the original fresh-``GATE_TIMEOUT_S`` deadline — fail-safe, matching this
    module's existing rule that a malformed durable read must never corrupt a gate's
    timing rather than just fail to speed it up."""
    if isinstance(opened_at, datetime) and isinstance(db_now, datetime):
        return opened_at, db_now
    return None, None


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
    pool, workspace_id: str, run_id: str, gate: str, node_id: str, content_sha: str | None
) -> dict | None:
    """Atomically claim a decided-but-unapplied gate decision, or None.

    ``applied_at IS NULL`` in the predicate + ``now()`` in the SET make the claim
    single-consumption even if a restarted runner and a racing in-process waiter
    both reach for it. ``content_sha`` is the hash of the bytes being held NOW: an approval
    or edit recorded over different bytes is not claimed (H9). That matters on reclaim,
    where the re-run gated node can write a new draft — the stale row stays unclaimed and
    ``_open_gate_row`` then resets it, so the operator decides the new bytes afresh. A
    reject is claimed whatever the bytes: it sends nothing, so there is nothing to bind,
    and the operator who ended the run is not asked again."""
    try:
        async with workspace_scope(pool, workspace_id) as conn:
            row = await conn.fetchrow(
                """UPDATE run_gates SET applied_at = now()
                   WHERE run_id = $1::uuid AND workspace_id = $2::uuid
                     AND gate = $3 AND node_id = $4
                     AND state <> 'open' AND applied_at IS NULL
                     AND (state = 'rejected' OR content_sha = $5)
                   RETURNING state, edited_content""",
                run_id,
                workspace_id,
                gate,
                node_id,
                content_sha,
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
