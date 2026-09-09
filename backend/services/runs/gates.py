"""Durable gates (A5, V017 ``run_gates``): the row persists BOTH the wait and the
decision, so a restart mid-gate strands nothing and a decision posted while the runner
was down is applied on resume. ``_content_sha`` binds every approval to the exact bytes
shown (H9)."""

from __future__ import annotations

import hashlib

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
