"""Backend pre-call cost-cap guard (the P2 hole the cost PRD closes).

Drives _execute_run directly (asyncio.run — repo convention) with a fake pool /
sessions, asserting that an over-cap workspace is refused BEFORE any paid brain call:
the run is marked failed and sessions.run is never invoked. Mirrors the SDK-free,
mock-pool style of the other backend tests.

Also covers RL-08/M-06: ``admits()`` (the synchronous admission-time twin of
``_budget_guard``) and the full ``POST /v1/runs`` admission chain refusing an
over-cap workspace with 402 before any row is written.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid as _uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.callers.rest import require_principal
from backend.deps import WorkspaceCtx, require_auth
from backend.errors import register_error_handlers
from backend.routers import runs as runs_router
from backend.services.runs import budget as budget_mod
from tests.backend._protocol1 import (
    BUDGET_MODULES,
    DONE_PUSH_MODULES,
    REPO,
    SCOPE_MODULES,
    patch_everywhere,
    user_principal,
)
from tests.backend.test_packs_api import PROFILE, _provision

WS_ID = "00000000-0000-0000-0000-000000000001"
RUN_ID = "00000000-0000-0000-0000-000000000010"
AGENT_ID = "00000000-0000-0000-0000-0000000000a1"


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchrow(self, sql, *args):
        """RL-03: start_run/_fail_run now guard their UPDATE with `status NOT IN (...)`
        and read the match back via `RETURNING id` instead of a bare `execute`. Nothing
        here seeds a terminal row, so every write is reported as matched — recorded into
        `executed` exactly like `execute` does, so the SQL-log assertions below keep
        seeing these writes."""
        self.executed.append((sql, args))
        if sql.lstrip().startswith("UPDATE runs") and "RETURNING id" in sql:
            return {"id": args[0]}
        return None

    async def fetchval(self, sql, *args):
        """RL-08: insert_run_row's _reserve_cap reads the in-flight count on this same
        connection before it INSERTs — always report 0 in flight (well under the cap)
        so the admission-chain tests below reach the INSERT rather than a 429.

        RT-04: the INSERT itself is now a `fetchval(...RETURNING id::text)` (an
        `ON CONFLICT (workspace_id, client_request_id) ... DO NOTHING`) — this fake
        conn carries no unique-constraint model, so it always reports a fresh insert
        (returns the run's own id); no test here exercises a real client_request_id
        collision, that lives in test_client_request_id.py's fake and its T2 dbtest."""
        self.executed.append((sql, args))
        if "count(*) FROM runs" in sql:
            return 0
        if sql.strip().startswith("INSERT INTO runs"):
            return args[0]
        return None


class _Sessions:
    """Fake BackendSessionStore: records run() calls, streams nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))

        async def _agen():
            return
            yield ""  # pragma: no cover — makes this an async generator

        return _agen()


def _fake_scope_factory(conn):
    @asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    return _scope


def _drive(over_cap: bool):
    """Run _execute_run with acheck_budget stubbed; return (conn, sessions)."""
    conn = _Conn()
    sessions = _Sessions()

    async def _go():
        with (
            patch_everywhere(SCOPE_MODULES, "workspace_scope", _fake_scope_factory(conn)),
            patch_everywhere(BUDGET_MODULES, "acheck_budget", AsyncMock(return_value=not over_cap)),
            patch_everywhere(DONE_PUSH_MODULES, "send_run_done_push", AsyncMock(return_value=0)),
        ):
            await runs_router._execute_run(
                MagicMock(),  # pool
                sessions,
                WS_ID,
                RUN_ID,
                "example2",
                "do the thing",
                False,
                entitlement="pro_plus",
            )

    asyncio.run(_go())
    return conn, sessions


def test_over_cap_run_refused_before_brain_call():
    conn, sessions = _drive(over_cap=True)
    # The run was marked failed with a cost-cap reason. Since the Phase-1b spine the reason
    # is a BOUND PARAMETER on the shared `_fail_run` write (which also stamps completed_at),
    # not a SQL literal inlined by the prompt path — behavioral-diff row 2.
    fails = [(sql, args) for sql, args in conn.executed if "status = 'failed'" in sql]
    assert len(fails) == 1, conn.executed
    sql, args = fails[0]
    assert "completed_at = now()" in sql
    assert "monthly cost cap reached" in args
    # ...and the paid session was never started.
    assert sessions.calls == []


def test_under_cap_run_proceeds_to_session():
    conn, sessions = _drive(over_cap=False)
    # Under cap → status set to running and the session stream is started.
    joined = " ".join(sql for sql, _ in conn.executed)
    assert "running" in joined
    assert len(sessions.calls) == 1


# ── admits() — the RL-08/M-06 synchronous admission-time twin of _budget_guard ────


def _run_admits(*, agent_id=None, agent_budget_usd=None, workspace_ok=True, agent_ok=None):
    """Drive admits() directly with workspace_scope + acheck_budget stubbed. When
    agent_ok is given, acheck_agent_budget is stubbed too (forces its verdict without
    needing a real cost_records read); when it's None, the REAL acheck_agent_budget
    runs (exercised for the no-agent trivial-allow branch)."""
    conn = _Conn()

    async def _go():
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch_everywhere(SCOPE_MODULES, "workspace_scope", _fake_scope_factory(conn))
            )
            stack.enter_context(
                patch_everywhere(
                    BUDGET_MODULES, "acheck_budget", AsyncMock(return_value=workspace_ok)
                )
            )
            if agent_ok is not None:
                stack.enter_context(
                    patch("backend.agents.acheck_agent_budget", AsyncMock(return_value=agent_ok))
                )
            return await budget_mod.admits(MagicMock(), WS_ID, agent_id, agent_budget_usd)

    return asyncio.run(_go())


def test_admits_false_when_workspace_over_cap():
    assert _run_admits(workspace_ok=False) is False


def test_admits_true_when_under_cap_and_no_agent():
    # No agent narrowing at all: acheck_agent_budget's own trivial-allow branch runs
    # for real (agent_id is None), never touching the DB.
    assert _run_admits(workspace_ok=True) is True


def test_admits_false_when_agent_budget_exhausted():
    """Workspace is UNDER cap, but the acting agent's own narrower budget is spent —
    the same AND semantics _budget_guard already applies before every dispatch batch."""
    assert (
        _run_admits(workspace_ok=True, agent_id=AGENT_ID, agent_budget_usd=5.0, agent_ok=False)
        is False
    )


def test_admits_true_when_agent_under_its_own_budget():
    assert (
        _run_admits(workspace_ok=True, agent_id=AGENT_ID, agent_budget_usd=5.0, agent_ok=True)
        is True
    )


# ── HTTP: POST /v1/runs refuses an over-cap workspace with 402, before any row ────


def _create_run_app(ws_id: str):
    app = FastAPI()
    app.include_router(runs_router.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.state.sessions = MagicMock()
    app.state.cfg = MagicMock(repo_root=REPO)
    ctx = WorkspaceCtx(user_id=str(_uuid.uuid4()), workspace_id=ws_id, entitlement="pro_plus")
    app.dependency_overrides[require_auth] = lambda: ctx
    # Fleet Phase A (Task 3): create_run now depends on require_principal.
    app.dependency_overrides[require_principal] = lambda: user_principal(ctx)
    register_error_handlers(app)
    return app


def _post_run(app, conn, body, *, workspace_ok: bool):
    with (
        patch_everywhere(SCOPE_MODULES, "workspace_scope", _fake_scope_factory(conn)),
        patch_everywhere(BUDGET_MODULES, "acheck_budget", AsyncMock(return_value=workspace_ok)),
        TestClient(app) as client,
    ):
        return client.post("/v1/runs", json=body)


def test_create_run_over_cap_refused_with_402_prompt_mode():
    """No pack/profile provisioning needed — prompt mode never touches the pack
    catalog, so admits() is the only thing standing between the request and the row."""
    ws_id = str(_uuid.uuid4())
    conn = _Conn()
    app = _create_run_app(ws_id)
    resp = _post_run(
        app, conn, {"profile_name": "any-profile", "prompt": "do the thing"}, workspace_ok=False
    )
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "cost_cap_reached"
    assert not any("INSERT INTO runs" in sql for sql, _ in conn.executed)


def test_create_run_over_cap_refused_with_402_pack_mode(ws_env):
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    conn = _Conn()
    app = _create_run_app(ws_env.ws_id)
    resp = _post_run(
        app,
        conn,
        {
            "profile_name": PROFILE,
            "pack": "marketing",
            "variant": "linkedin-post",
            "inputs": {"brand_name": "ExampleCo"},
        },
        workspace_ok=False,
    )
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "cost_cap_reached"
    assert not any("INSERT INTO runs" in sql for sql, _ in conn.executed)


def test_create_run_under_cap_still_returns_202(ws_env):
    """Regression: nothing broke for the happy path — an under-cap workspace's
    POST /v1/runs is unaffected and the queued row is written as before."""
    _provision(ws_env.profiles_root, packs_toml='active = ["marketing"]\n')
    conn = _Conn()
    app = _create_run_app(ws_env.ws_id)
    resp = _post_run(
        app,
        conn,
        {
            "profile_name": PROFILE,
            "pack": "marketing",
            "variant": "linkedin-post",
            "inputs": {"brand_name": "ExampleCo"},
        },
        workspace_ok=True,
    )
    assert resp.status_code == 202
    assert any("INSERT INTO runs" in sql for sql, _ in conn.executed)


# ── RT-04: an optional client_request_id makes POST /v1/runs safe to retry ───────


class _IdempotentConn:
    """A richer fake than `_Conn` above: models `runs` as real rows (not just a call
    log), so it can answer both the INSERT's `ON CONFLICT ... RETURNING id::text` and
    the fast-path replay's full `fetch_run_detail` read — the two things `_Conn`
    deliberately doesn't model, since the 402/cap tests above never reach a real
    insert."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.insert_count = 0

    async def execute(self, sql, *args):
        return None  # pg_advisory_xact_lock — no real locking needed, one conn at a time

    async def fetchval(self, sql, *args):
        if "count(*) FROM runs" in sql:
            return len(
                [
                    r
                    for r in self.rows.values()
                    if r["status"] in ("queued", "running", "awaiting_approval")
                ]
            )
        if sql.strip().startswith("INSERT INTO runs"):
            # Fleet Phase A: principal_kind/principal_id are now inserted between
            # payload and client_request_id (admission.py:insert_run_row).
            # Fleet Phase B: external_ref is added after client_request_id.
            (
                run_id,
                ws,
                profile,
                prompt,
                dry_run,
                agent_id,
                payload,
                principal_kind,
                principal_id,
                client_request_id,
                *rest,
            ) = args
            if client_request_id is not None:
                clash = next(
                    (
                        r
                        for r in self.rows.values()
                        if r["workspace_id"] == ws and r["client_request_id"] == client_request_id
                    ),
                    None,
                )
                if clash is not None:
                    return None  # ON CONFLICT ... DO NOTHING
            self.insert_count += 1
            self.rows[run_id] = {
                "id": run_id,
                "workspace_id": ws,
                "status": "queued",
                "profile_name": profile,
                "output": None,
                "error": None,
                "error_code": None,
                "pending_gate": None,
                "pending_content": None,
                "agent_id": agent_id,
                "payload": payload,
                "created_at": None,
                "principal_kind": principal_kind,
                "principal_id": principal_id,
                "client_request_id": client_request_id,
            }
            return run_id
        if "SELECT id::text FROM runs WHERE workspace_id" in sql and "client_request_id" in sql:
            ws, client_request_id = args
            match = next(
                (
                    r
                    for r in self.rows.values()
                    if r["workspace_id"] == ws and r["client_request_id"] == client_request_id
                ),
                None,
            )
            return match["id"] if match is not None else None
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql, *args):
        if "FROM runs r WHERE r.id" in sql:
            run_id = args[0]
            row = self.rows.get(run_id)
            if row is None:
                return None
            return {**row, "gate_kind": None, "gate_node_id": None}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *args):
        return []  # no run_nodes/run_blocks rows in any of these tests


def test_replaying_the_same_client_request_id_returns_the_original_run():
    """A retry after a lost 202 (the realistic case on a mobile network) must not
    create a second run, consume a second concurrency slot, or spend budget twice."""
    ws_id = str(_uuid.uuid4())
    conn = _IdempotentConn()
    app = _create_run_app(ws_id)
    body = {"profile_name": "any-profile", "prompt": "do the thing", "client_request_id": "req-1"}

    first = _post_run(app, conn, body, workspace_ok=True)
    assert first.status_code == 202
    first_run_id = first.json()["run_id"]

    second = _post_run(app, conn, body, workspace_ok=True)
    assert second.status_code == 202
    assert second.json()["run_id"] == first_run_id
    assert conn.insert_count == 1, "the replay must not write a second row"


def test_different_client_request_ids_create_different_runs():
    """Regression: distinct ids (the common case) never collapse into one run."""
    ws_id = str(_uuid.uuid4())
    conn = _IdempotentConn()
    app = _create_run_app(ws_id)

    first = _post_run(
        app,
        conn,
        {"profile_name": "any-profile", "prompt": "a", "client_request_id": "req-a"},
        workspace_ok=True,
    )
    second = _post_run(
        app,
        conn,
        {"profile_name": "any-profile", "prompt": "b", "client_request_id": "req-b"},
        workspace_ok=True,
    )
    assert first.json()["run_id"] != second.json()["run_id"]
    assert conn.insert_count == 2


def test_no_client_request_id_is_byte_identical_to_pre_rt04_behavior():
    """No id at all: every request is its own run, exactly as before RT-04 — the
    idempotency fast path and the ON CONFLICT branch are both skipped entirely."""
    ws_id = str(_uuid.uuid4())
    conn = _IdempotentConn()
    app = _create_run_app(ws_id)
    body = {"profile_name": "any-profile", "prompt": "do the thing"}

    first = _post_run(app, conn, body, workspace_ok=True)
    second = _post_run(app, conn, body, workspace_ok=True)
    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]
    assert conn.insert_count == 2
