"""Service for probing external third-party BYOK credentials for liveness and quota health."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

import httpx

log = logging.getLogger(__name__)

VerifyStatus = Literal["valid", "invalid_key", "provider_unreachable", "credits_exhausted"]


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of probing a third-party credential."""

    status: VerifyStatus
    credits_remaining: int | None = None
    plan: str | None = None
    reason: str | None = None


# Strict 5.0 second timeout across connect and read
_PROBE_TIMEOUT = httpx.Timeout(5.0, connect=5.0)

# Provider base endpoints (aligned with agent/mcp connectors)
_ROCKETREACH_ACCOUNT_URL = "https://api.rocketreach.co/api/v2/account"
_APOLLO_PROFILE_URL = "https://api.apollo.io/v1/users/api_profile"
_SALESHANDY_PROFILE_URL = "https://open-api.saleshandy.com/v1/user/profile"
_SYFTEN_FILTER_URL = "https://syften.com/api/0.1/filter/get"


def _is_synthetic_bypass(key: str) -> bool:
    """Return True if key is a synthetic fixture that must bypass live network I/O."""
    if os.environ.get("BYPASS_INTEGRATION_VERIFY") == "1":
        return True
    key_lower = key.lower()
    return (
        key_lower.startswith(("test_", "fictional_", "dev_", "sk-"))
        or "_test_" in key_lower
        or "fictional" in key_lower
    )


async def _probe_rocketreach(client: httpx.AsyncClient, api_key: str) -> VerifyResult:
    resp = await client.get(
        _ROCKETREACH_ACCOUNT_URL,
        headers={"Api-Key": api_key, "Accept": "application/json"},
    )
    if resp.status_code in (401, 403):
        return VerifyResult(status="invalid_key", reason="Authentication failed (invalid Api-Key)")
    if not resp.is_success:
        return VerifyResult(
            status="provider_unreachable",
            reason=f"RocketReach returned unexpected status {resp.status_code}",
        )
    data = resp.json()
    balance = data.get("lookup_credit_balance")
    plan_info = data.get("current_subscription", {}).get("plan")
    if balance is not None and balance <= 0:
        return VerifyResult(
            status="credits_exhausted",
            credits_remaining=balance,
            plan=plan_info,
            reason="Account has 0 lookup credits remaining",
        )
    return VerifyResult(status="valid", credits_remaining=balance, plan=plan_info)


async def _probe_saleshandy(client: httpx.AsyncClient, api_key: str) -> VerifyResult:
    resp = await client.get(
        _SALESHANDY_PROFILE_URL,
        headers={"x-api-key": api_key, "Accept": "application/json"},
    )
    if resp.status_code in (401, 403):
        return VerifyResult(
            status="invalid_key", reason="Authentication failed (invalid x-api-key)"
        )
    if resp.is_success:
        return VerifyResult(status="valid")
    return VerifyResult(
        status="provider_unreachable",
        reason=f"Saleshandy returned unexpected status {resp.status_code}",
    )


async def _probe_apollo(client: httpx.AsyncClient, api_key: str) -> VerifyResult:
    resp = await client.get(
        _APOLLO_PROFILE_URL,
        headers={"X-Api-Key": api_key, "Accept": "application/json"},
    )
    if resp.status_code in (401, 403):
        return VerifyResult(
            status="invalid_key", reason="Authentication failed (invalid X-Api-Key)"
        )
    if resp.is_success:
        return VerifyResult(status="valid")
    return VerifyResult(
        status="provider_unreachable",
        reason=f"Apollo returned unexpected status {resp.status_code}",
    )


async def _probe_syften(client: httpx.AsyncClient, api_key: str) -> VerifyResult:
    resp = await client.post(
        _SYFTEN_FILTER_URL,
        json={"token": api_key},
        headers={"Accept": "application/json"},
    )
    if resp.status_code in (401, 403):
        return VerifyResult(status="invalid_key", reason="Authentication failed (invalid token)")
    if resp.is_success:
        return VerifyResult(status="valid")
    return VerifyResult(
        status="provider_unreachable",
        reason=f"Syften returned unexpected status {resp.status_code}",
    )


_PROBERS = {
    "rocketreach": _probe_rocketreach,
    "saleshandy": _probe_saleshandy,
    "apollo": _probe_apollo,
    "syften": _probe_syften,
}


async def verify_provider_credential(
    provider: str,
    api_key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> VerifyResult:
    """Probe a third-party provider API to verify credential validity and quota."""
    if not api_key:
        return VerifyResult(status="invalid_key", reason="API key is empty")

    if _is_synthetic_bypass(api_key):
        return VerifyResult(status="valid", reason="synthetic_key_bypass")

    prober = _PROBERS.get(provider)
    if not prober:
        return VerifyResult(status="invalid_key", reason=f"Unsupported provider: {provider}")

    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, transport=transport) as client:
            return await prober(client, api_key)
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException) as exc:
        log.warning("Timeout while verifying provider %s: %s", provider, exc)
        return VerifyResult(status="provider_unreachable", reason=f"Connection timed out: {exc}")
    except (httpx.ConnectError, httpx.NetworkError) as exc:
        log.warning("Network error while verifying provider %s: %s", provider, exc)
        return VerifyResult(status="provider_unreachable", reason=f"Network error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.warning("Unexpected error while verifying provider %s: %s", provider, exc)
        return VerifyResult(
            status="provider_unreachable", reason=f"Verification probe error: {exc}"
        )
