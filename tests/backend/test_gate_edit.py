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

FL12/RL-11 (below): ``decision="edit"`` with no ``edited_content`` must 422 at the Pydantic
boundary, never reach ``decide_gate``, and never be silently treated as an approve of the
run's ORIGINAL bytes (which would report ``edited=true`` with the unedited content_sha).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")


import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from backend.callers.rest import require_principal  # noqa: E402
from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from backend.schemas import GateRequest  # noqa: E402
from backend.services.runs import pack_executor as runs_pack_executor  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402
from tests.backend._protocol1 import (  # noqa: E402
    BUDGET_MODULES,
    DONE_PUSH_MODULES,
    PUSH_MODULES,
    REPO,
    SCOPE_MODULES,
    drive_gate,
    fake_executor,
    pack_run_harness,
    patch_everywhere,
    user_principal,
)
from tests.backend.test_packs_api import PROFILE, _provision  # noqa: E402

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000010"
GATE_CHUNK = "Here is the draft.\n⟦GATE:publish⟧\nORIGINAL post body"
PENDING = "Draft awaiting approval."
PENDING_SHA = hashlib.sha256(PENDING.encode()).hexdigest()


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchrow(self, sql, *args):
        """RL-03: start_run/resume_run/reject_run/complete_run/hold_gate/_fail_run now
        guard their UPDATE with `status NOT IN (...)` and read the match back via
        `RETURNING id` instead of a bare `execute`. Nothing here seeds a terminal row, so
        every write is reported as matched — recorded into `executed` exactly like
        `execute` does, so `_all_sql`/`_final_output` keep seeing these writes."""
        self.executed.append((sql, args))
        if sql.lstrip().startswith("UPDATE runs") and "RETURNING id" in sql:
            return {"id": args[0]}
        return None


class _GateSessions:
    """Fake BackendSessionStore: streams chunks that carry a gate sentinel."""

    def __init__(self, chunks) -> None:
        self._chunks = tuple(chunks)
        self.calls: list[tuple] = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
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
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _fake_scope_factory(conn)),
            patch_everywhere(BUDGET_MODULES, "acheck_budget", AsyncMock(return_value=True)),
            patch_everywhere(PUSH_MODULES, "send_gate_push", AsyncMock(return_value=0)),
            patch_everywhere(DONE_PUSH_MODULES, "send_run_done_push", AsyncMock(return_value=0)),
        ):
            task = asyncio.create_task(
                runs_router._execute_run(
                    MagicMock(),
                    sessions,
                    WS_ID,
                    RUN_ID,
                    "example2",
                    "do the thing",
                    False,
                    entitlement="pro_plus",
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
    # Every guarded write's own `status NOT IN (..., 'rejected')` clause (RL-03) mentions
    # the word "rejected" too, so the regression check must look for the actual WRITE,
    # not the bare word.
    assert "status = 'rejected'" not in joined  # regression: 'edit' was treated as reject
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


def test_edited_content_at_a_gate_with_no_draft_fails_the_pack_run(ws_env):
    """Backstop for POST /gate's refusal (client issue #241 Q6): marketing's `case-study` gate
    pauses to show a claim ledger but has no promotable draft, so edited bytes there have
    nowhere to go. A gate the real graph declares — not a fabricated pause — and before the
    fix the run continued as if the edit had been applied."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    run_id = str(uuid.uuid4())
    conn = AsyncMock()
    executor = fake_executor([], awaiting_on="case-study")
    fail_run_mock = AsyncMock()

    async def _go():
        task = asyncio.create_task(
            runs_router._execute_pack_run(
                MagicMock(),
                REPO,
                ws_env.ws_id,
                run_id,
                PROFILE,
                "marketing",
                "case-study",
                {},
                entitlement="pro_plus",
            )
        )
        await drive_gate(run_id, "edit", edited_content="a rewritten claim ledger")
        await asyncio.wait_for(task, timeout=10)

    with (
        pack_run_harness(conn, executor),
        patch.object(runs_pack_executor, "_fail_run", fail_run_mock),
    ):
        asyncio.run(_go())
    fail_run_mock.assert_awaited_once()
    assert "no editable draft" in fail_run_mock.await_args.args[3]
    assert fail_run_mock.await_args.kwargs == {"error_code": "draft_invalid"}


# ── FL12/RL-11: decision="edit" with no edited_content must 422, never silently approve ──


def test_gate_request_edit_with_no_edited_content_is_rejected_at_construction():
    """The Pydantic boundary, not decide_gate, is where this must be caught."""
    with pytest.raises(ValidationError, match="edited_content is required"):
        GateRequest(decision="edit", edited_content=None, content_sha="x" * 64)


def test_gate_request_edit_with_edited_content_constructs_fine():
    body = GateRequest(decision="edit", edited_content="some text", content_sha="x" * 64)
    assert body.decision == "edit"
    assert body.edited_content == "some text"


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_gate_request_approve_and_reject_do_not_require_edited_content(decision):
    """No accidental over-broad validation: only 'edit' needs edited_content."""
    body = GateRequest(decision=decision, edited_content=None, content_sha="x" * 64)
    assert body.decision == decision
    assert body.edited_content is None


def _gate_client() -> TestClient:
    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    ctx = WorkspaceCtx(USER_ID, WS_ID, Entitlement.PRO)
    app.dependency_overrides[require_auth] = lambda: ctx
    # Fleet Phase A (Task 3): decide_gate resolves identity via require_principal, then
    # calls require_human(principal) itself — a kind="user" Principal clears that gate
    # exactly like every plain user JWT did before this task.
    app.dependency_overrides[require_principal] = lambda: user_principal(ctx)
    register_error_handlers(app)
    return TestClient(app)


def _open_gate_row(status: str = "awaiting_approval") -> dict:
    return {
        "status": status,
        "pending_gate": "⟦GATE:publish⟧",
        "pending_content": PENDING,
        "gate_kind": None,
    }


def test_post_gate_edit_with_no_edited_content_is_422_and_gate_stays_open():
    """HTTP-level: FastAPI's own request validation must reject this body BEFORE
    decide_gate ever runs — fetch_open_gate/record_decision are never called, so the
    run's gate is left exactly as open as it was."""
    fetch = AsyncMock(return_value=_open_gate_row())
    record = AsyncMock(return_value=True)
    with (
        patch.object(runs_router, "fetch_open_gate", fetch),
        patch.object(runs_router, "record_decision", record),
        _gate_client() as c,
    ):
        resp = c.post(
            f"/v1/runs/{RUN_ID}/gate",
            json={"decision": "edit", "content_sha": PENDING_SHA},
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    fetch.assert_not_awaited()
    record.assert_not_awaited()


def test_post_gate_edit_with_edited_content_still_reaches_decide_gate():
    """Regression guard for the fix above: a well-formed edit request must NOT be
    collaterally rejected — it should still reach fetch_open_gate/record_decision."""
    fetch = AsyncMock(return_value=_open_gate_row())
    record = AsyncMock(return_value=True)
    with (
        patch.object(runs_router, "fetch_open_gate", fetch),
        patch.object(runs_router, "record_decision", record),
        _gate_client() as c,
    ):
        resp = c.post(
            f"/v1/runs/{RUN_ID}/gate",
            json={
                "decision": "edit",
                "content_sha": PENDING_SHA,
                "edited_content": "the operator's rewrite",
            },
        )
    assert resp.status_code == 200
    fetch.assert_awaited_once()
    record.assert_awaited_once()


# ── FL19 / NG-19: Publish gate EU AI Act disclosure check on edit ─────────────


def _synthetic_publish_gate_row(profile_name: str = "creator") -> dict:
    content = (
        "⟦GATE:publish⟧\n"
        "⟦POST⟧\n"
        "Here is a cool video. Made with AI. Reviewed and posted by a human.\n"
        "⟦/POST⟧\n"
        "⟦IDENTITY⟧soul,voice⟦/IDENTITY⟧"
    )
    return {
        "status": "awaiting_approval",
        "pending_gate": "⟦GATE:publish⟧",
        "pending_content": content,
        "gate_kind": "publish",
        "profile_name": profile_name,
    }


def test_publish_gate_edit_stripping_disclosure_returns_422(tmp_path):
    row = _synthetic_publish_gate_row(profile_name="creator")
    content_sha = hashlib.sha256(row["pending_content"].encode()).hexdigest()
    fetch = AsyncMock(return_value=row)
    record = AsyncMock(return_value=True)

    client = _gate_client()
    client.app.state.cfg = MagicMock(repo_root=tmp_path)

    with (
        patch.object(runs_router, "fetch_open_gate", fetch),
        patch.object(runs_router, "record_decision", record),
        patch(
            "agent.publish.candidate_disclosure_lines",
            return_value=["Made with AI. Reviewed and posted by a human."],
        ),
    ):
        resp = client.post(
            f"/v1/runs/{RUN_ID}/gate",
            json={
                "decision": "edit",
                "content_sha": content_sha,
                "edited_content": "Here is a cool video with disclosure stripped out.",
            },
        )
    assert resp.status_code == 422
    data = resp.json()
    assert data["error"]["code"] == "disclosure_missing"
    record.assert_not_awaited()


def test_publish_gate_edit_preserving_disclosure_succeeds(tmp_path):
    row = _synthetic_publish_gate_row(profile_name="creator")
    content_sha = hashlib.sha256(row["pending_content"].encode()).hexdigest()
    fetch = AsyncMock(return_value=row)
    record = AsyncMock(return_value=True)

    client = _gate_client()
    client.app.state.cfg = MagicMock(repo_root=tmp_path)

    with (
        patch.object(runs_router, "fetch_open_gate", fetch),
        patch.object(runs_router, "record_decision", record),
        patch(
            "agent.publish.candidate_disclosure_lines",
            return_value=["Made with AI. Reviewed and posted by a human."],
        ),
    ):
        resp = client.post(
            f"/v1/runs/{RUN_ID}/gate",
            json={
                "decision": "edit",
                "content_sha": content_sha,
                "edited_content": "Here is a cool video. Made with AI. Reviewed and posted by a human.",
            },
        )
    assert resp.status_code == 200
    record.assert_awaited_once()
