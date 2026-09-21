from __future__ import annotations

import pytest

from gtm_core.funnel import FunnelInfeasible, size


def test_funnel_no_fallback_exits():
    """Verify size raises FunnelInfeasible when credits run out and no_fallback=True"""
    # Setup conditions that will require some credits (more than available)
    # E.g., looking up 500 contacts, but we have 100 credits
    # To get here, pool_available must be large enough.

    # 1. Fallback = false (no_fallback=False) -> Should succeed and add a warning
    plan = size(
        target_delivered=500,
        tier="a+b",
        pool_available=10000,
        lookup_credits_remaining=100,
        no_fallback=False,
    )
    assert plan.target_delivered == 500
    assert any("DEGRADATION WARNING" in w for w in plan.warnings)

    # 2. no_fallback = True -> Should fail with FunnelInfeasible
    with pytest.raises(FunnelInfeasible, match="--no-fallback is active"):
        size(
            target_delivered=500,
            tier="a+b",
            pool_available=10000,
            lookup_credits_remaining=100,
            no_fallback=True,
        )
