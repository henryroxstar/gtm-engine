"""Unit tests for third-party BYOK credential liveness and quota verification."""

from __future__ import annotations

import asyncio

import httpx

from backend.services.integrations_verify import (
    verify_provider_credential,
)


def test_synthetic_key_bypass():
    """Synthetic test keys must bypass outbound network I/O immediately."""

    async def _run():
        for key in [
            "test_key_12345",
            "dev_rocketreach_key",
            "fictional_saleshandy_key",
            "sh_test_fictional_key_00000000",
            "rr_test_fictional_key_00000000",
            "sk-12345",
        ]:
            res = await verify_provider_credential("saleshandy", key)
            assert res.status == "valid"
            assert res.reason == "synthetic_key_bypass"

    asyncio.run(_run())


def test_rocketreach_valid_and_quota_exhausted():
    """RocketReach probe checks lookup credit balance."""

    async def _run():
        # 1. Valid with credits
        async def mock_handler_ok(request: httpx.Request):
            assert request.headers["Api-Key"] == "rr_real_valid_key"
            assert str(request.url) == "https://api.rocketreach.co/api/v2/account"
            return httpx.Response(
                200,
                json={
                    "name": "Acme Corp",
                    "lookup_credit_balance": 150,
                    "current_subscription": {"plan": "Pro"},
                },
            )

        transport = httpx.MockTransport(mock_handler_ok)
        res = await verify_provider_credential(
            "rocketreach", "rr_real_valid_key", transport=transport
        )
        assert res.status == "valid"
        assert res.credits_remaining == 150
        assert res.plan == "Pro"

        # 2. Valid key but 0 credits remaining
        async def mock_handler_zero_credits(request: httpx.Request):
            return httpx.Response(
                200,
                json={
                    "name": "Acme Corp",
                    "lookup_credit_balance": 0,
                    "current_subscription": {"plan": "Free Trial"},
                },
            )

        transport_zero = httpx.MockTransport(mock_handler_zero_credits)
        res_zero = await verify_provider_credential(
            "rocketreach", "rr_zero_credits_key", transport=transport_zero
        )
        assert res_zero.status == "credits_exhausted"
        assert res_zero.credits_remaining == 0
        assert "0 lookup credits remaining" in (res_zero.reason or "")

    asyncio.run(_run())


def test_provider_auth_rejection_401():
    """401/403 HTTP statuses from provider indicate invalid key."""

    async def _run():
        async def mock_handler_401(request: httpx.Request):
            return httpx.Response(401, json={"error": "Unauthorized"})

        transport = httpx.MockTransport(mock_handler_401)
        res = await verify_provider_credential(
            "saleshandy", "sh_invalid_key_999", transport=transport
        )
        assert res.status == "invalid_key"
        assert "Authentication failed" in (res.reason or "")

    asyncio.run(_run())


def test_provider_timeout_unreachable():
    """Network timeouts return provider_unreachable."""

    async def _run():
        async def mock_handler_timeout(request: httpx.Request):
            raise httpx.ConnectTimeout("Connection timed out after 5.0s")

        transport = httpx.MockTransport(mock_handler_timeout)
        res = await verify_provider_credential(
            "apollo", "apollo_live_key_timeout", transport=transport
        )
        assert res.status == "provider_unreachable"
        assert "timed out" in (res.reason or "").lower()

    asyncio.run(_run())


def test_saleshandy_and_apollo_probe_headers():
    """Verify Saleshandy uses x-api-key and Apollo uses /users/api_profile with X-Api-Key."""

    async def _run():
        # Saleshandy
        async def mock_saleshandy(request: httpx.Request):
            assert request.headers["x-api-key"] == "sh_live_key_123"
            assert str(request.url) == "https://open-api.saleshandy.com/v1/user/profile"
            return httpx.Response(200, json={"data": {"email": "user@example.com"}})

        res_sh = await verify_provider_credential(
            "saleshandy", "sh_live_key_123", transport=httpx.MockTransport(mock_saleshandy)
        )
        assert res_sh.status == "valid"

        # Apollo
        async def mock_apollo(request: httpx.Request):
            assert request.headers["X-Api-Key"] == "apollo_live_key_123"
            assert str(request.url) == "https://api.apollo.io/v1/users/api_profile"
            return httpx.Response(200, json={"user": {"email": "user@example.com"}})

        res_apollo = await verify_provider_credential(
            "apollo", "apollo_live_key_123", transport=httpx.MockTransport(mock_apollo)
        )
        assert res_apollo.status == "valid"

    asyncio.run(_run())


def test_syften_probe_body():
    """Verify Syften uses POST /api/0.1/filter/get with JSON token body."""

    async def _run():
        async def mock_syften(request: httpx.Request):
            assert str(request.url) == "https://syften.com/api/0.1/filter/get"
            assert request.method == "POST"
            import json

            body = json.loads(request.content.decode("utf-8"))
            assert body == {"token": "syften_token_123"}
            return httpx.Response(200, json={"filters": []})

        res_syften = await verify_provider_credential(
            "syften", "syften_token_123", transport=httpx.MockTransport(mock_syften)
        )
        assert res_syften.status == "valid"

    asyncio.run(_run())
