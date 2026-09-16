import contextlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.deps import require_auth
from backend.routers import integrations
from backend.services import integrations as integrations_service


@pytest.fixture(autouse=True)
def _test_kek(monkeypatch):
    # Per test, never at import: a module-level os.environ write leaks into every test the
    # full suite collects after it, and a present KEK sends pack runs into the real
    # credential loader.
    monkeypatch.setenv(
        "VAULT_KEK", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )


app = FastAPI()
app.include_router(integrations.router)


def override_require_auth():
    mock_ws = MagicMock()
    mock_ws.workspace_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    return mock_ws


app.dependency_overrides[require_auth] = override_require_auth


def _scope_factory(conn):
    """Fake workspace_scope that yields a fixed connection — real tenant isolation
    (RLS actually filtering rows) is proven live against Postgres in
    tests/backend/test_integrations_rls_live.py; this mock only lets the route/
    service logic run without a real pool."""

    @contextlib.asynccontextmanager
    async def _scope(pool, workspace_id):
        yield conn

    return _scope


def test_set_integration(monkeypatch):
    conn = AsyncMock()
    # Both the router (list/delete) and the service (upsert_credential, used by PUT)
    # bind their own `workspace_scope` name at import time — each needs patching.
    monkeypatch.setattr(integrations, "workspace_scope", _scope_factory(conn))
    monkeypatch.setattr(integrations_service, "workspace_scope", _scope_factory(conn))
    app.state.pool = MagicMock()
    app.state.sessions = AsyncMock()  # Mock the eviction service

    client = TestClient(app)

    res = client.put("/integrations/saleshandy", json={"api_key": "sk-12345"})
    assert res.status_code == 200
    assert res.json() == {"status": "configured", "provider": "saleshandy"}

    # Verify validation rejects empty keys
    res_empty = client.put("/integrations/saleshandy", json={"api_key": "   "})
    assert res_empty.status_code == 422

    # Verify execute was called with correct AESGCM args
    conn.execute.assert_called_once()
    query = conn.execute.call_args[0][0]
    assert "INSERT INTO encrypted_credentials" in query

    # Verify cache invalidation occurred
    app.state.sessions.evict_workspace.assert_called_once_with(
        "11111111-1111-1111-1111-111111111111"
    )
