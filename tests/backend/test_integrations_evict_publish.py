"""Unit tests for BYOK PUT and DELETE publishing credentials_rotated eviction (T13)."""

from __future__ import annotations

import contextlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import require_auth
from backend.routers import integrations
from backend.services import integrations as integrations_service

_WS_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _test_kek(monkeypatch):
    monkeypatch.setenv(
        "VAULT_KEK", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )


def _scope_factory(conn):
    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    return _scope


def test_t13_put_and_delete_publish_credentials_rotated_eviction(monkeypatch):
    """T13: PUT and DELETE /v1/integrations/{provider} call evict_workspace(close_in_use=False) and publish_workspace_evict."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="DELETE 1")

    monkeypatch.setattr(integrations, "workspace_scope", _scope_factory(conn))
    monkeypatch.setattr(integrations_service, "workspace_scope", _scope_factory(conn))

    mock_publish = AsyncMock()
    monkeypatch.setattr("backend.broker.publish_workspace_evict", mock_publish)

    app = FastAPI()
    app.include_router(integrations.router, prefix="/v1")

    mock_ws = MagicMock()
    mock_ws.workspace_id = uuid.UUID(_WS_ID)
    app.dependency_overrides[require_auth] = lambda: mock_ws

    app.state.pool = MagicMock()
    mock_sessions = MagicMock()
    mock_sessions.evict_workspace = AsyncMock()
    app.state.sessions = mock_sessions

    client = TestClient(app)

    # 1. Test PUT /v1/integrations/saleshandy
    res_put = client.put("/v1/integrations/saleshandy", json={"api_key": "sk-12345"})
    assert res_put.status_code == 200
    assert res_put.json() == {"status": "configured", "provider": "saleshandy"}

    # Assert local sessions eviction called with close_in_use=False
    mock_sessions.evict_workspace.assert_called_once_with(_WS_ID, close_in_use=False)
    # Assert broker publish called with reason="credentials_rotated"
    mock_publish.assert_called_once_with(_WS_ID, reason="credentials_rotated")

    # Reset mocks for DELETE
    mock_sessions.evict_workspace.reset_mock()
    mock_publish.reset_mock()

    # 2. Test DELETE /v1/integrations/saleshandy
    res_del = client.delete("/v1/integrations/saleshandy")
    assert res_del.status_code == 200
    assert res_del.json() == {"status": "deleted"}

    # Assert local sessions eviction called with close_in_use=False
    mock_sessions.evict_workspace.assert_called_once_with(_WS_ID, close_in_use=False)
    # Assert broker publish called with reason="credentials_rotated"
    mock_publish.assert_called_once_with(_WS_ID, reason="credentials_rotated")
