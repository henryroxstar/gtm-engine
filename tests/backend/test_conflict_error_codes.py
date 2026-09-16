"""Conflicts and not-founds the app branches on carry their own ``error.code`` (RL-06, ER-04).

The gate screen needs to tell "refetch and re-render" (``content_sha_mismatch``,
``gate_not_open``) from "someone already decided, stop" (``gate_already_decided``,
``run_already_terminal``). Before this all four rendered as ``conflict``, and the message
heuristic mis-coded three 404s (``No profile for this workspace`` → ``workspace_not_found``).
Apps are enveloped as ``backend/main.py`` wires them; auth is overridden, the DB mocked.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")

from backend.deps import WorkspaceCtx, require_auth  # noqa: E402
from backend.errors import register_error_handlers  # noqa: E402
from backend.routers import api_keys as api_keys_router  # noqa: E402
from backend.routers import integrations as integrations_router  # noqa: E402
from backend.routers import packs as packs_router  # noqa: E402
from backend.routers import runs as runs_router  # noqa: E402
from gtm_core.capabilities import Entitlement  # noqa: E402

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000010"
KEY_ID = "00000000-0000-0000-0000-000000000020"
PENDING = "Draft awaiting approval."
PENDING_SHA = hashlib.sha256(PENDING.encode()).hexdigest()


def _client(*routers) -> TestClient:
    app = FastAPI()
    for rtr in routers:
        app.include_router(rtr.router, prefix="/v1")
    app.state.pool = MagicMock()
    app.dependency_overrides[require_auth] = lambda: WorkspaceCtx(USER_ID, WS_ID, Entitlement.PRO)
    register_error_handlers(app)
    return TestClient(app)


def _error(resp) -> tuple[int, str, dict | None]:
    err = resp.json()["error"]
    return resp.status_code, err["code"], err["details"]


def _scope(conn):
    @asynccontextmanager
    async def _s(pool, workspace_id):
        yield conn

    return _s


# ── runs: cancel + gate ───────────────────────────────────────────────────────


@pytest.mark.parametrize("terminal", ["ok", "failed", "rejected", "canceled"])
def test_cancel_of_a_terminal_run_is_run_already_terminal(terminal):
    with (
        patch.object(runs_router, "run_status", AsyncMock(return_value=terminal)),
        _client(runs_router) as c,
    ):
        resp = c.post(f"/v1/runs/{RUN_ID}/cancel")
    assert _error(resp) == (409, "run_already_terminal", {"status": terminal})


def _decide(c, sha: str = PENDING_SHA):
    return c.post(f"/v1/runs/{RUN_ID}/gate", json={"decision": "approve", "content_sha": sha})


def _gate_row(status: str = "awaiting_approval") -> dict:
    return {
        "status": status,
        "pending_gate": "⟦GATE:publish⟧",
        "pending_content": PENDING,
        "gate_kind": None,
    }


def test_gate_decision_on_a_run_not_at_a_gate_is_gate_not_open():
    with (
        patch.object(runs_router, "fetch_open_gate", AsyncMock(return_value=_gate_row("running"))),
        _client(runs_router) as c,
    ):
        resp = _decide(c)
    assert _error(resp) == (409, "gate_not_open", {"status": "running"})


def test_gate_decision_for_stale_bytes_is_content_sha_mismatch():
    with (
        patch.object(runs_router, "fetch_open_gate", AsyncMock(return_value=_gate_row())),
        _client(runs_router) as c,
    ):
        resp = _decide(c, sha="0" * 64)
    assert _error(resp) == (409, "content_sha_mismatch", None)


def test_second_gate_decision_is_gate_already_decided():
    record = AsyncMock(return_value=False)
    with (
        patch.object(runs_router, "fetch_open_gate", AsyncMock(return_value=_gate_row())),
        patch.object(runs_router, "record_decision", record),
        _client(runs_router) as c,
    ):
        resp = _decide(c)
    assert record.await_count == 1
    assert _error(resp) == (409, "gate_already_decided", None)


def test_gate_decision_for_an_invisible_run_stays_run_not_found():
    with (
        patch.object(runs_router, "fetch_open_gate", AsyncMock(return_value=None)),
        _client(runs_router) as c,
    ):
        resp = _decide(c)
    assert _error(resp) == (404, "run_not_found", None)


# ── mis-coded 404s and the api-key 409 ────────────────────────────────────────


def test_readiness_with_no_profile_is_profile_not_found():
    with (
        patch.object(packs_router, "_profile_for", AsyncMock(return_value=None)),
        _client(packs_router) as c,
    ):
        resp = c.get("/v1/packs/marketing/linkedin-post/readiness")
    assert _error(resp) == (404, "profile_not_found", None)


def test_revoking_an_unknown_api_key_is_api_key_not_found():
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    with (
        patch.object(api_keys_router, "workspace_scope", _scope(conn)),
        _client(api_keys_router) as c,
    ):
        resp = c.delete(f"/v1/api-keys/{KEY_ID}")
    assert _error(resp) == (404, "api_key_not_found", None)


def test_revoking_a_revoked_api_key_is_api_key_already_revoked():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"revoked_at": datetime(2025, 1, 1, tzinfo=UTC)}
    with (
        patch.object(api_keys_router, "workspace_scope", _scope(conn)),
        _client(api_keys_router) as c,
    ):
        resp = c.delete(f"/v1/api-keys/{KEY_ID}")
    assert _error(resp) == (409, "api_key_already_revoked", None)
    conn.execute.assert_not_awaited()


def test_deleting_an_unconfigured_integration_is_integration_not_found():
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 0"
    with (
        patch.object(integrations_router, "workspace_scope", _scope(conn)),
        _client(integrations_router) as c,
    ):
        resp = c.delete("/v1/integrations/apollo")
    assert _error(resp) == (404, "integration_not_found", None)


def test_registering_a_taken_email_keeps_already_exists():
    from backend.routers import auth as auth_router

    conn = AsyncMock()
    conn.fetchval.return_value = USER_ID

    @asynccontextmanager
    async def _acquire():
        yield conn

    client = _client(auth_router)
    client.app.state.pool = MagicMock(acquire=_acquire)
    with client as c:
        resp = c.post(
            "/v1/auth/register", json={"email": "dana@example.com", "password": "hunter2-hunter2"}
        )
    assert _error(resp) == (409, "already_exists", None)
    conn.execute.assert_not_awaited()
