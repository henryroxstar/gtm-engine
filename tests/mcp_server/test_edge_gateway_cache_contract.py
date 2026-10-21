"""Edge Gateway cache placement and 402 non-caching contract test (T14)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATEWAY_FILE = REPO / "deploy" / "mcp-gateway" / "src" / "index.ts"

if not GATEWAY_FILE.exists():
    pytest.skip("deploy/ not present (private deployment surface)", allow_module_level=True)


def test_t14_gateway_cache_placement_and_ttl():
    """T14: Verify ENTITLEMENTS_KV.put is strictly after Step 7 payment check with 60s TTL."""
    code = GATEWAY_FILE.read_text(encoding="utf-8")

    # 1. 60s default TTL
    assert 'DEFAULT_CACHE_TTL_SECONDS || "60"' in code

    # 2. KV put placement relative to Step 7 (402 return statements)
    # Find positions of Step 7 payment checks and ENTITLEMENTS_KV.put
    step7_idx = code.find("// 7. Enforce subscription / payment requirement")
    assert step7_idx != -1

    last_402_idx = code.rfind("status: 402")
    assert last_402_idx != -1
    assert last_402_idx > step7_idx

    kv_put_idx = code.find("env.ENTITLEMENTS_KV.put")
    assert kv_put_idx != -1

    # ENTITLEMENTS_KV.put must appear AFTER the last 402 check
    assert kv_put_idx > last_402_idx, (
        "env.ENTITLEMENTS_KV.put must be placed AFTER all 402 payment checks"
    )

    # 3. fromKvCache guard prevents redundant puts
    assert "!fromKvCache" in code


def _evaluate_gateway_entitlement(
    data: dict, rc_entitlements: dict | None = None
) -> tuple[int, bool]:
    """Pure-Python behavioral replica of deploy/mcp-gateway/src/index.ts Steps 4-7.

    Returns (http_status, would_cache_in_kv).
    """
    import datetime

    # Wire shape from /v1/api-keys/verify
    entitlement = {
        "workspace_id": data.get("workspace_id", "ws-1"),
        "tier": data.get("tier", "pro"),
        "active": data.get("active", True) is not False,
        "expires_at": data.get("expires_at"),
        "has_credits": data.get("has_credits"),
    }

    # Step 6: RevenueCat verification (if applicable)
    if rc_entitlements is not None:
        active_sub = False
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()
        for e in rc_entitlements.values():
            if not e.get("expires_date") or e["expires_date"] > now_iso:
                active_sub = True
                if e.get("expires_date"):
                    entitlement["expires_at"] = e["expires_date"]
                break
        if active_sub:
            entitlement["active"] = True
        elif len(rc_entitlements) > 0:
            entitlement["active"] = False

    # Step 7: Enforce subscription / payment requirement
    is_expired = False
    if entitlement["expires_at"]:
        now_dt = datetime.datetime.now(datetime.UTC)
        exp_dt = datetime.datetime.fromisoformat(entitlement["expires_at"].replace("Z", "+00:00"))
        is_expired = exp_dt < now_dt

    is_paid_tier = entitlement["tier"] != "free"
    has_active_sub = entitlement["active"] and not is_expired and is_paid_tier

    if not has_active_sub:
        if entitlement["has_credits"] is False:
            return 402, False
        if entitlement["has_credits"] is not True:
            return 402, False

    # Post Step 7 KV cache put:
    would_cache = True
    return 200, would_cache


def test_t14_four_402_equivalence_classes_never_cached():
    """T14: All 402 equivalence classes return 402 and never write to ENTITLEMENTS_KV."""
    # (a) Inactive sub, has_credits: false
    status_a, cached_a = _evaluate_gateway_entitlement({"active": False, "has_credits": False})
    assert status_a == 402
    assert cached_a is False

    # (b) Free tier, has_credits: false
    status_b, cached_b = _evaluate_gateway_entitlement(
        {"active": True, "tier": "free", "has_credits": False}
    )
    assert status_b == 402
    assert cached_b is False

    # (c) Expired subscription, has_credits: false
    status_c, cached_c = _evaluate_gateway_entitlement(
        {"active": True, "expires_at": "2020-01-01T00:00:00Z", "has_credits": False}
    )
    assert status_c == 402
    assert cached_c is False

    # (d) RevenueCat Step 6 override active: false, has_credits: false
    expired_rc = {"pro": {"expires_date": "2020-01-01T00:00:00Z"}}
    status_d, cached_d = _evaluate_gateway_entitlement(
        {"active": True, "has_credits": False}, rc_entitlements=expired_rc
    )
    assert status_d == 402
    assert cached_d is False


def test_t14_instant_recovery_post_topup_and_subscription_cached():
    """T14: Wallet top-up (has_credits: true) or active sub immediately caches and passes 200."""
    # Top-up with has_credits: true
    status_topup, cached_topup = _evaluate_gateway_entitlement(
        {"active": False, "has_credits": True}
    )
    assert status_topup == 200
    assert cached_topup is True

    # Active subscription with tier: pro
    status_sub, cached_sub = _evaluate_gateway_entitlement(
        {"active": True, "tier": "pro", "has_credits": False}
    )
    assert status_sub == 200
    assert cached_sub is True
