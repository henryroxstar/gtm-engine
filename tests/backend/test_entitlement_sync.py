"""Unit tests for the service-to-service entitlement-sync endpoint.

Billing is owned by the billing service; the engine receives entitlement + cap over
PUT /v1/entitlement/{workspace_id} (see the billing-boundary design doc).

The security-critical property here is that this route GRANTS entitlement, so it must
authenticate a SERVICE secret and never a user JWT — otherwise a user could self-upgrade
to pro_plus. These tests are SDK-free / mock-based (no live DB); the record+apply
transaction logic is covered in test_hardening_residuals.py, and the live cross-tenant
path in the staging smoke (scripts/entitlement_sync_smoke.py).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.deps import require_service_auth
from backend.routers import entitlement as ent
from backend.schemas import EntitlementSyncRequest

_SECRET = "svc-secret-abc123"  # nosec B105 — throwaway test value, not a real secret


def _req(auth_header: str | None):
    r = MagicMock()
    r.headers = {"Authorization": auth_header} if auth_header is not None else {}
    return r


# ── service auth: the free-self-upgrade guard ─────────────────────────────────


def test_service_auth_missing_secret_env_500(monkeypatch):
    # No configured secret → refuse ALL calls (fail-closed), never fail open.
    monkeypatch.delenv("BILLING_SYNC_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_service_auth(_req(_SECRET))
    assert exc.value.status_code == 500


def test_service_auth_no_header_401(monkeypatch):
    monkeypatch.setenv("BILLING_SYNC_SECRET", _SECRET)
    with pytest.raises(HTTPException) as exc:
        require_service_auth(_req(None))
    assert exc.value.status_code == 401


def test_service_auth_wrong_secret_401(monkeypatch):
    monkeypatch.setenv("BILLING_SYNC_SECRET", _SECRET)
    with pytest.raises(HTTPException) as exc:
        require_service_auth(_req("not-the-secret"))
    assert exc.value.status_code == 401


def test_service_auth_rejects_user_jwt(monkeypatch):
    # A valid-looking user Bearer token must NOT authenticate the service route:
    # it isn't the shared secret, so it's rejected (no self-upgrade path).
    monkeypatch.setenv("BILLING_SYNC_SECRET", _SECRET)
    jwt_like = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.sig"
    with pytest.raises(HTTPException) as exc:
        require_service_auth(_req(jwt_like))
    assert exc.value.status_code == 401


def test_service_auth_valid_passes(monkeypatch):
    monkeypatch.setenv("BILLING_SYNC_SECRET", _SECRET)
    assert require_service_auth(_req(_SECRET)) is None  # no raise → authenticated


# ── endpoint behaviour ────────────────────────────────────────────────────────

_WS = "11111111-1111-4111-8111-111111111111"


def _call(body: EntitlementSyncRequest):
    """Invoke sync_entitlement with _apply_sync patched; return (response, apply_args)."""
    captured = {}

    async def _fake_apply(
        request, workspace_id, entitlement, cap_usd, status_val, sync_id, version
    ):
        captured.update(
            workspace_id=workspace_id,
            entitlement=entitlement,
            cap_usd=cap_usd,
            status_val=status_val,
            sync_id=sync_id,
            version=version,
        )
        return "new"

    async def _go():
        with patch.object(ent, "_apply_sync", AsyncMock(side_effect=_fake_apply)):
            return await ent.sync_entitlement(_WS, body, MagicMock(), None)

    return asyncio.run(_go()), captured


def test_pro_sync_passes_cap_through():
    resp, args = _call(EntitlementSyncRequest(entitlement="pro", cap_usd=50.0, sync_id="s1"))
    assert resp.applied is True and resp.outcome == "new"
    # the engine stores the billing service's priced cap verbatim — it does not decide the number.
    assert args["cap_usd"] == 50.0
    assert args["entitlement"] == "pro"


def test_free_entitlement_clamps_cap_to_zero():
    # FREE invariant: a free plan carries no paid budget, even if a nonzero cap is sent.
    resp, args = _call(EntitlementSyncRequest(entitlement="free", cap_usd=25.0, sync_id="s2"))
    assert resp.applied is True
    assert args["cap_usd"] == 0.0


def test_bad_workspace_id_422():
    async def _go():
        return await ent.sync_entitlement(
            "not-a-uuid",
            EntitlementSyncRequest(entitlement="pro", cap_usd=50.0, sync_id="s3"),
            MagicMock(),
            None,
        )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_go())
    assert exc.value.status_code == 422


def test_non_new_outcome_reports_not_applied():
    async def _go():
        with patch.object(ent, "_apply_sync", AsyncMock(return_value="duplicate")):
            return await ent.sync_entitlement(
                _WS,
                EntitlementSyncRequest(entitlement="pro", cap_usd=50.0, sync_id="s4"),
                MagicMock(),
                None,
            )

    resp = asyncio.run(_go())
    assert resp.applied is False and resp.outcome == "duplicate"
