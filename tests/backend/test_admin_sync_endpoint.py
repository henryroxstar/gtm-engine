"""Tests for POST /v1/admin/sync endpoint (safe admin state sync)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import WorkspaceCtx, require_auth
from backend.routers.admin_sync import router
from gtm_core.capabilities import Entitlement

app = FastAPI()
app.include_router(router, prefix="/v1")

_WS_ID = "00000000-0000-0000-0000-000000000001"
_USER_ID = "00000000-0000-0000-0000-000000000002"


def _mock_pool(member_role: str | None = "admin"):
    pool = MagicMock()
    conn = MagicMock()

    async def _fetchrow(query, *args):
        if "FROM workspace_members" in query:
            if member_role is not None:
                return {"role": member_role}
            return None
        return {"id": "uuid-1", "account_id": "a-1"}

    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    conn.execute = AsyncMock()

    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def test_admin_sync_missing_auth_returns_401():
    app.dependency_overrides.clear()
    client = TestClient(app)
    resp = client.post("/v1/admin/sync", json={})
    assert resp.status_code == 401


def test_admin_sync_non_admin_member_returns_403():
    app.dependency_overrides[require_auth] = lambda: WorkspaceCtx(
        workspace_id=_WS_ID, user_id=_USER_ID, entitlement=Entitlement.PRO
    )
    pool = _mock_pool(member_role="member")
    app.state.pool = pool
    client = TestClient(app)

    with patch("backend.routers.admin_sync.workspace_scope") as mock_scope:
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"role": "member"})
        mock_scope.return_value.__aenter__.return_value = conn

        resp = client.post("/v1/admin/sync", json={"accounts": []})
        assert resp.status_code == 403
        assert "Only workspace owners or admins" in resp.json()["detail"]


def test_admin_sync_admin_success():
    app.dependency_overrides[require_auth] = lambda: WorkspaceCtx(
        workspace_id=_WS_ID, user_id=_USER_ID, entitlement=Entitlement.PRO
    )
    pool = _mock_pool(member_role="admin")
    app.state.pool = pool
    client = TestClient(app)

    with (
        patch("backend.routers.admin_sync.workspace_scope") as mock_scope,
        patch("backend.services.accounts.upsert_account", new_callable=AsyncMock) as mock_acc,
        patch("backend.services.contacts.upsert_contact", new_callable=AsyncMock) as mock_con,
    ):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={"role": "admin"})
        mock_scope.return_value.__aenter__.return_value = conn

        payload = {
            "accounts": [{"account_id": "a-1", "company_name": "Acme"}],
            "contacts": [{"pool_row_id": "r-1", "email": "a@example.com"}],
            "suppressions": [],
            "people": [],
            "outcomes": [],
            "content_items": [],
        }
        resp = client.post("/v1/admin/sync", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["accounts_synced"] == 1
        assert data["contacts_synced"] == 1
        assert mock_acc.called
        assert mock_con.called
