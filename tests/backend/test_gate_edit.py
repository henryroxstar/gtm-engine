"""Gate 'edit' decision + edited_content application (2026-07-05 code-review follow-up).

Before this fix, ``_execute_run`` did ``if decision != "approve": <reject>`` and never
read ``edited_content`` — so a ``decision="edit"`` KILLED the run and approve-with-edits
silently persisted the ORIGINAL bytes. These tests drive ``_execute_run`` directly with a
fake streaming session that emits a gate sentinel, then inject the operator's decision the
way ``decide_gate`` does (write under ``_state_lock``, set the ``Event``), and assert:

  - ``edit`` does NOT reject the run (regression guard)
  - ``edited_content`` becomes the run's content of record — persisted ``output`` AND the
    ``pending_content`` audit row — for both ``edit`` and approve-with-edits
  - a plain ``approve`` keeps the original streamed bytes
  - ``reject`` still rejects

SDK-free, mock-pool style — mirrors tests/backend/test_cost_guard.py.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from backend.routers import runs as runs_router

WS_ID = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-0000-0000-000000000010"
GATE_CHUNK = "Here is the draft.\n⟦GATE:publish⟧\nORIGINAL post body"


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class _GateSessions:
    """Fake BackendSessionStore: streams chunks that carry a gate sentinel."""

    def __init__(self, chunks) -> None:
        self._chunks = tuple(chunks)
        self.calls: list[tuple] = []

    def run(self, *args):
        self.calls.append(args)
        chunks = self._chunks

        async def _agen():
            for c in chunks:
                yield c

        return _agen()


def _fake_scope_factory(conn):
    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    return _scope


def _drive_gate(decision: str, edited_content=None, chunks=(GATE_CHUNK,)) -> _Conn:
    """Drive _execute_run to a gate, inject `decision`, run to completion; return the _Conn."""
    conn = _Conn()
    sessions = _GateSessions(chunks)

    async def _go():
        # Clear any leftover module state from a prior test (shared globals, one RUN_ID).
        runs_router._gate_events.pop(RUN_ID, None)
        runs_router._gate_decisions.pop(RUN_ID, None)
        with (
            patch.object(runs_router, "workspace_scope", _fake_scope_factory(conn)),
            patch.object(runs_router, "acheck_budget", AsyncMock(return_value=True)),
            patch.object(runs_router, "send_gate_push", AsyncMock(return_value=0)),
        ):
            task = asyncio.create_task(
                runs_router._execute_run(
                    MagicMock(), sessions, WS_ID, RUN_ID, "example2", "do the thing", False
                )
            )
            # Wait for the gate to register its Event, then inject the decision exactly
            # the way decide_gate does: write under _state_lock, then set the Event.
            for _ in range(400):
                if RUN_ID in runs_router._gate_events:
                    break
                await asyncio.sleep(0.005)
            else:  # pragma: no cover
                task.cancel()
                raise AssertionError("gate never registered")
            async with runs_router._state_lock:
                runs_router._gate_decisions[RUN_ID] = {
                    "decision": decision,
                    "edited_content": edited_content,
                }
            runs_router._gate_events[RUN_ID].set()
            await asyncio.wait_for(task, timeout=5)

    asyncio.run(_go())
    return conn


def _final_output(conn: _Conn):
    """Return the `output` arg of the terminal `status = 'ok'` UPDATE, or None."""
    for sql, args in conn.executed:
        if "status = 'ok'" in sql and "output = $2" in sql:
            return args[1]  # UPDATE ... SET status='ok', output=$2 ... -> args=(run_id, output)
    return None


def _all_sql(conn: _Conn) -> str:
    return " ".join(sql for sql, _ in conn.executed)


def test_edit_decision_does_not_reject():
    conn = _drive_gate("edit", edited_content="EDITED post body")
    joined = _all_sql(conn)
    assert "rejected" not in joined  # regression: 'edit' was treated as reject
    assert "status = 'ok'" in joined


def test_edit_applies_edited_content_as_output():
    conn = _drive_gate("edit", edited_content="EDITED post body")
    assert _final_output(conn) == "EDITED post body"


def test_edit_rewrites_pending_content_audit_row():
    conn = _drive_gate("edit", edited_content="EDITED post body")
    # The gate-open UPDATE uses `pending_content = $3`; the edit-apply UPDATE uses `$2`.
    rewrites = [args for sql, args in conn.executed if "pending_content = $2" in sql]
    assert rewrites, "pending_content was never rewritten to the approved bytes"
    assert rewrites[-1][1] == "EDITED post body"


def test_approve_with_edits_uses_edited_bytes():
    conn = _drive_gate("approve", edited_content="APPROVED EDIT")
    assert _final_output(conn) == "APPROVED EDIT"


def test_plain_approve_keeps_original_streamed_bytes():
    conn = _drive_gate("approve", edited_content=None)
    assert _final_output(conn) == GATE_CHUNK


def test_reject_still_rejects():
    conn = _drive_gate("reject")
    joined = _all_sql(conn)
    assert "rejected" in joined
    assert "status = 'ok'" not in joined
