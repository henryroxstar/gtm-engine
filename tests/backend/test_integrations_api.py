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
        "11111111-1111-1111-1111-111111111111", close_in_use=False
    )


def test_verify_integration_route(monkeypatch):
    from backend.services.integrations_verify import VerifyResult

    client = TestClient(app)

    # 1. Valid key
    async def mock_verify_valid(provider, key):
        return VerifyResult(status="valid", credits_remaining=42, plan="Pro")

    monkeypatch.setattr(integrations, "verify_provider_credential", mock_verify_valid)
    res_valid = client.post("/integrations/rocketreach/verify", json={"api_key": "real_key"})
    assert res_valid.status_code == 200
    assert res_valid.json() == {
        "valid": True,
        "provider": "rocketreach",
        "credits_remaining": 42,
        "plan": "Pro",
    }

    # 2. Invalid key (401/403)
    async def mock_verify_invalid(provider, key):
        return VerifyResult(status="invalid_key", reason="Auth failed")

    monkeypatch.setattr(integrations, "verify_provider_credential", mock_verify_invalid)
    res_invalid = client.post("/integrations/saleshandy/verify", json={"api_key": "bad_key"})
    assert res_invalid.status_code == 422
    assert res_invalid.json()["detail"]["code"] == "invalid_provider_key"

    # 3. Credits exhausted
    async def mock_verify_exhausted(provider, key):
        return VerifyResult(
            status="credits_exhausted", credits_remaining=0, reason="0 credits remaining"
        )

    monkeypatch.setattr(integrations, "verify_provider_credential", mock_verify_exhausted)
    res_exhausted = client.post("/integrations/rocketreach/verify", json={"api_key": "zero_key"})
    assert res_exhausted.status_code == 422
    assert res_exhausted.json()["detail"]["code"] == "provider_quota_exhausted"

    # 4. Provider unreachable (timeout)
    async def mock_verify_timeout(provider, key):
        return VerifyResult(status="provider_unreachable", reason="Timed out")

    monkeypatch.setattr(integrations, "verify_provider_credential", mock_verify_timeout)
    res_timeout = client.post("/integrations/apollo/verify", json={"api_key": "hang_key"})
    assert res_timeout.status_code == 503
    assert res_timeout.json()["detail"]["code"] == "provider_unreachable"


def test_set_integration_validation(monkeypatch):
    from backend.services.integrations_verify import VerifyResult

    conn = AsyncMock()
    monkeypatch.setattr(integrations, "workspace_scope", _scope_factory(conn))
    monkeypatch.setattr(integrations_service, "workspace_scope", _scope_factory(conn))
    app.state.pool = MagicMock()
    app.state.sessions = AsyncMock()

    client = TestClient(app)

    # 1. Blocks invalid key
    async def mock_invalid(provider, key):
        return VerifyResult(status="invalid_key", reason="Invalid key")

    monkeypatch.setattr(integrations, "verify_provider_credential", mock_invalid)
    res_bad = client.put("/integrations/saleshandy", json={"api_key": "bad_key"})
    assert res_bad.status_code == 422
    assert res_bad.json()["detail"]["code"] == "invalid_provider_key"

    # 2. Blocks zero credits
    async def mock_zero(provider, key):
        return VerifyResult(status="credits_exhausted", credits_remaining=0, reason="0 credits")

    monkeypatch.setattr(integrations, "verify_provider_credential", mock_zero)
    res_zero = client.put("/integrations/rocketreach", json={"api_key": "zero_credits_key"})
    assert res_zero.status_code == 422
    assert res_zero.json()["detail"]["code"] == "provider_quota_exhausted"

    # 3. Fails soft on provider timeout
    async def mock_timeout(provider, key):
        return VerifyResult(status="provider_unreachable", reason="Timed out")

    monkeypatch.setattr(integrations, "verify_provider_credential", mock_timeout)
    res_timeout = client.put("/integrations/apollo", json={"api_key": "timeout_key"})
    assert res_timeout.status_code == 200
    assert res_timeout.json() == {
        "status": "configured",
        "provider": "apollo",
        "warning": "provider_unreachable",
    }

    # 4. Bypass verification via verify=false
    res_bypassed = client.put(
        "/integrations/saleshandy?verify=false", json={"api_key": "unverified_key"}
    )
    assert res_bypassed.status_code == 200
    assert res_bypassed.json() == {"status": "configured", "provider": "saleshandy"}


def test_verify_integration_workspace_rate_limit(monkeypatch):
    """T14: Workspace rate limit enforces 10 req/min per workspace, isolated between workspaces."""
    from backend.deps import WorkspaceCtx, require_auth
    from backend.ratelimit import limiter
    from gtm_core.capabilities import Entitlement

    ws_1 = WorkspaceCtx(
        user_id="user-1", workspace_id="ws-workspace-1111", entitlement=Entitlement.PRO
    )
    ws_2 = WorkspaceCtx(
        user_id="user-2", workspace_id="ws-workspace-2222", entitlement=Entitlement.PRO
    )
    current_ws = {"ws": ws_1}

    async def override_auth():
        return current_ws["ws"]

    app.dependency_overrides[require_auth] = override_auth

    monkeypatch.setattr(limiter, "enabled", True)
    limiter.limiter.storage.reset()

    client = TestClient(app)

    # Workspace 1 can make 10 requests
    for i in range(10):
        resp = client.post("/integrations/saleshandy/verify", json={"api_key": "test_key_ok"})
        assert resp.status_code == 200, f"Request {i + 1} failed: {resp.text}"

    # 11th request for Workspace 1 is rate limited (429)
    resp_11 = client.post("/integrations/saleshandy/verify", json={"api_key": "test_key_ok"})
    assert resp_11.status_code == 429
    assert resp_11.json()["detail"]["code"] == "rate_limit_exceeded"

    # Workspace 2 is unaffected (isolated bucket)
    current_ws["ws"] = ws_2
    resp_ws2 = client.post("/integrations/saleshandy/verify", json={"api_key": "test_key_ok"})
    assert resp_ws2.status_code == 200
