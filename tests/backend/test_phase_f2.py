"""Unit tests for Phase F.2 backend surface.

Tests for:
  - GET /v1/runs/{id} — pending_content field
  - POST /v1/runs/{id}/cancel
  - POST/GET/DELETE /v1/api-keys
  - PATCH /v1/account
  - DELETE /v1/account
  - PATCH /v1/workspace/cost-cap

All DB and pool calls are mocked — no live Postgres required.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("BACKEND_JWT_SECRET", "test-secret-for-unit-tests-only-32x")
os.environ.setdefault("BACKEND_JWT_EXPIRE_MINUTES", "60")
os.environ.setdefault("BACKEND_REFRESH_EXPIRE_DAYS", "30")

from datetime import UTC

from backend.auth import create_access_token
from backend.routers import account as account_router
from backend.routers import api_keys as api_keys_router
from backend.routers import runs as runs_router
from backend.routers import workspaces as workspaces_router

WS_ID = "00000000-0000-0000-0000-000000000001"
USER_ID = "00000000-0000-0000-0000-000000000002"
RUN_ID = "00000000-0000-0000-0000-000000000010"
KEY_ID = "00000000-0000-0000-0000-000000000020"


def _access_token() -> str:
    return create_access_token(USER_ID, WS_ID)


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}"}


def _make_app(*routers) -> FastAPI:
    app = FastAPI()
    for rtr in routers:
        app.include_router(rtr.router, prefix="/v1")
    app.state.pool = MagicMock()
    return app


# ── GET /v1/runs/{id} — pending_content ───────────────────────────────────────

_RUN_ROW = {
    "id": RUN_ID,
    "status": "awaiting_approval",
    "profile_name": "test",
    "output": None,
    "error": None,
    "pending_gate": "⟦GATE:publish⟧",
    "pending_content": "Here is the draft content for approval.",
}
_ENTITLEMENT_ROW = {"entitlement": "pro"}


class TestRunGetPendingContent:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        # fetchrow is called twice: once by require_auth (entitlement), once by get_run
        c.fetchrow.side_effect = [_ENTITLEMENT_ROW, _RUN_ROW]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(runs_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.runs.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_pending_content_returned(self, client):
        resp = client.get(f"/v1/runs/{RUN_ID}", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["pending_content"] == "Here is the draft content for approval."
        assert body["status"] == "awaiting_approval"

    def test_pending_content_null_when_not_set(self, conn, client):
        conn.fetchrow.side_effect = [
            _ENTITLEMENT_ROW,
            {**_RUN_ROW, "status": "running", "pending_gate": None, "pending_content": None},
        ]
        resp = client.get(f"/v1/runs/{RUN_ID}", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["pending_content"] is None

    def test_run_not_found(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, None]
        resp = client.get(f"/v1/runs/{RUN_ID}", headers=_auth_header())
        assert resp.status_code == 404


# ── POST /v1/runs/{id}/cancel ─────────────────────────────────────────────────


class TestCancelRun:
    # cancel_run calls fetchrow twice: require_auth (entitlement) + run status check
    # then execute once for the UPDATE

    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "UPDATE 1"
        c.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "running"}]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(runs_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.runs.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            runs_router._cancelled_runs.clear()
            runs_router._gate_events.clear()
            runs_router._gate_decisions.clear()
            with TestClient(app) as c:
                yield c

    def test_cancel_running_run(self, client):
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["run_id"] == RUN_ID

    def test_cancel_pending_run(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "pending"}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 200

    def test_cancel_awaiting_approval_fires_gate_event(self, conn, client):
        import asyncio

        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "awaiting_approval"}]
        event = asyncio.Event()
        runs_router._gate_events[RUN_ID] = event

        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 200
        assert event.is_set()
        assert runs_router._gate_decisions.get(RUN_ID, {}).get("decision") == "reject"

    def test_cancel_terminal_ok_returns_409(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "ok"}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 409

    def test_cancel_terminal_failed_returns_409(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "failed"}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 409

    def test_cancel_terminal_rejected_returns_409(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, {"status": "rejected"}]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 409

    def test_cancel_not_found_returns_404(self, conn, client):
        conn.fetchrow.side_effect = [_ENTITLEMENT_ROW, None]
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel", headers=_auth_header())
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.post(f"/v1/runs/{RUN_ID}/cancel")
        assert resp.status_code == 401


# ── POST /v1/api-keys ─────────────────────────────────────────────────────────


class TestCreateApiKey:
    @pytest.fixture()
    def conn(self):
        from datetime import datetime

        c = AsyncMock()
        c.fetchrow.side_effect = [
            # require_auth — entitlement lookup
            {"entitlement": "pro"},
            # INSERT RETURNING
            {
                "id": KEY_ID,
                "prefix": "sk-abc12",
                "label": "test-key",
                "entitlement": "pro",
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            },
        ]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(api_keys_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.api_keys.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_create_returns_raw_key(self, client):
        resp = client.post(
            "/v1/api-keys",
            json={"label": "test-key", "entitlement": "pro"},
            headers=_auth_header(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "raw_key" in body
        assert body["raw_key"].startswith("sk-")
        assert body["entitlement"] == "pro"
        assert body["is_revoked"] is False

    def test_entitlement_capped_at_workspace_level(self, conn, client):
        from datetime import datetime

        # Workspace is 'pro' but key requests 'pro_plus' — should be capped to 'pro'
        conn.fetchrow.side_effect = [
            {"entitlement": "pro"},
            {
                "id": KEY_ID,
                "prefix": "sk-abc12",
                "label": None,
                "entitlement": "pro",  # capped
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            },
        ]
        resp = client.post(
            "/v1/api-keys",
            json={"entitlement": "pro_plus"},
            headers=_auth_header(),
        )
        assert resp.status_code == 201

    def test_requires_auth(self, client):
        resp = client.post("/v1/api-keys", json={"entitlement": "pro"})
        assert resp.status_code == 401


# ── GET /v1/api-keys ──────────────────────────────────────────────────────────


class TestListApiKeys:
    @pytest.fixture()
    def conn(self):
        from datetime import datetime

        c = AsyncMock()
        c.fetchrow.return_value = {"entitlement": "pro"}
        c.fetch.return_value = [
            {
                "id": KEY_ID,
                "prefix": "sk-abc12",
                "label": "prod-key",
                "entitlement": "pro",
                "last_used_at": None,
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                "revoked_at": None,
            }
        ]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(api_keys_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.api_keys.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_lists_keys(self, client):
        resp = client.get("/v1/api-keys", headers=_auth_header())
        assert resp.status_code == 200
        keys = resp.json()
        assert len(keys) == 1
        assert keys[0]["prefix"] == "sk-abc12"
        assert "raw_key" not in keys[0]

    def test_requires_auth(self, client):
        resp = client.get("/v1/api-keys")
        assert resp.status_code == 401


# ── DELETE /v1/api-keys/{key_id} ──────────────────────────────────────────────


class TestRevokeApiKey:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "UPDATE 1"
        c.fetchrow.side_effect = [
            {"entitlement": "pro"},  # require_auth
            {"revoked_at": None},  # key lookup — not yet revoked
        ]
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(api_keys_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.api_keys.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_revoke_existing_key(self, client):
        resp = client.delete(f"/v1/api-keys/{KEY_ID}", headers=_auth_header())
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    def test_revoke_missing_key_returns_404(self, conn, client):
        conn.fetchrow.side_effect = [
            {"entitlement": "pro"},
            None,
        ]
        resp = client.delete(f"/v1/api-keys/{KEY_ID}", headers=_auth_header())
        assert resp.status_code == 404

    def test_revoke_already_revoked_returns_409(self, conn, client):
        from datetime import datetime

        conn.fetchrow.side_effect = [
            {"entitlement": "pro"},
            {"revoked_at": datetime(2025, 1, 1, tzinfo=UTC)},
        ]
        resp = client.delete(f"/v1/api-keys/{KEY_ID}", headers=_auth_header())
        assert resp.status_code == 409

    def test_requires_auth(self, client):
        resp = client.delete(f"/v1/api-keys/{KEY_ID}")
        assert resp.status_code == 401


# ── PATCH /v1/account ─────────────────────────────────────────────────────────


class TestPatchAccount:
    @pytest.fixture()
    def pool(self):
        p = MagicMock()
        conn = AsyncMock()
        conn.execute.return_value = "UPDATE 1"
        conn.fetchrow.return_value = {"entitlement": "pro"}
        p.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return p, conn

    @pytest.fixture()
    def client(self, pool):
        p, conn = pool
        app = _make_app(account_router)
        app.state.pool = p

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with patch("backend.deps.workspace_scope", _scope):
            with TestClient(app) as c:
                yield c, conn

    def test_update_display_name(self, client):
        c, conn = client
        resp = c.patch(
            "/v1/account",
            json={"display_name": "New Name"},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_update_password_requires_current_password(self, client):
        c, _ = client
        resp = c.patch(
            "/v1/account",
            json={"new_password": "newpass123"},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_no_fields_returns_422(self, client):
        c, _ = client
        resp = c.patch("/v1/account", json={}, headers=_auth_header())
        assert resp.status_code == 422

    def test_requires_auth(self, client):
        c, _ = client
        resp = c.patch("/v1/account", json={"display_name": "x"})
        assert resp.status_code == 401


# ── DELETE /v1/account ────────────────────────────────────────────────────────


class TestDeleteAccount:
    @pytest.fixture()
    def pool(self):
        p = MagicMock()
        conn = AsyncMock()
        conn.execute.return_value = "DELETE 1"
        # includes password_hash for the step-up re-auth SELECT + entitlement for require_auth
        conn.fetchrow.return_value = {"entitlement": "free", "password_hash": "bcrypt-hash"}
        p.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return p, conn

    @pytest.fixture()
    def client(self, pool):
        p, conn = pool
        app = _make_app(account_router)
        app.state.pool = p
        # delete_account resolves the per-workspace tree via cfg.repo_root (P3); the
        # path does not exist in tests, so no rmtree runs.
        app.state.cfg = MagicMock(repo_root=Path("/nonexistent-gtm-test-root"))

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.deps.workspace_scope", _scope),
            patch("backend.auth.verify_password", return_value=True),
        ):
            with TestClient(app) as c:
                yield c, conn

    def test_delete_account_success(self, client):
        c, _ = client
        resp = c.request(
            "DELETE", "/v1/account", headers=_auth_header(), json={"current_password": "pw"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_user_not_found_returns_404(self, client):
        c, conn = client
        conn.execute.return_value = "DELETE 0"
        resp = c.request(
            "DELETE", "/v1/account", headers=_auth_header(), json={"current_password": "pw"}
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        c, _ = client
        resp = c.request("DELETE", "/v1/account", json={"current_password": "pw"})
        assert resp.status_code == 401


# ── PATCH /v1/workspace/cost-cap ──────────────────────────────────────────────


class TestPatchCostCap:
    @pytest.fixture()
    def conn(self):
        c = AsyncMock()
        c.execute.return_value = "UPDATE 1"
        c.fetchrow.return_value = {"entitlement": "pro"}
        return c

    @pytest.fixture()
    def client(self, conn):
        app = _make_app(workspaces_router)

        @asynccontextmanager
        async def _scope(pool, workspace_id):
            yield conn

        with (
            patch("backend.routers.workspaces.workspace_scope", _scope),
            patch("backend.deps.workspace_scope", _scope),
        ):
            with TestClient(app) as c:
                yield c

    def test_update_cost_cap(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 100.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 200
        assert resp.json()["monthly_cost_cap_usd"] == 100.0

    def test_zero_cap_rejected(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 0.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_negative_cap_rejected(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": -10.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_cap_above_500_rejected(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 501.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 422

    def test_cap_at_max_boundary(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 500.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 200

    def test_subscription_not_found_returns_404(self, conn, client):
        conn.execute.return_value = "UPDATE 0"
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 50.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client):
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 50.0},
        )
        assert resp.status_code == 401

    def test_free_tier_cannot_set_cap(self, conn, client):
        # VULN-0002: a FREE workspace must not raise its own $0 cap. require_tier
        # denies FREE/NONE before the UPDATE ever runs.
        conn.fetchrow.return_value = {"entitlement": "free"}
        resp = client.patch(
            "/v1/workspace/cost-cap",
            json={"monthly_cost_cap_usd": 499.0},
            headers=_auth_header(),
        )
        assert resp.status_code == 403
        conn.execute.assert_not_called()
