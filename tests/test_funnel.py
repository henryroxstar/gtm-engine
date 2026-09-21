"""Tests for gtm_core.funnel — the delivery-target sizing gate.

These lock in the failure that motivated the module: on 2026-08-12 an operator asked for
"500 accounts end to end", the skill read it as 500 *discovered*, and delivered 7. The
sizing call must say so up front, and the per-stage tripwire must stop a cold run rather
than let it narrow silently.
"""

from __future__ import annotations

import pytest

from gtm_core.funnel import (
    STAGES,
    TIER_YIELDS,
    WHY_NOW_YIELDS,
    FunnelInfeasible,
    Plan,
    check_stage,
    load_yields,
    record_actuals,
    size,
)


def test_delivery_target_needs_more_discovery_than_the_target() -> None:
    """The whole point: 500 delivered != 500 discovered."""
    plan = size(500, pool_available=100_000)
    assert plan.discovery_needed > 500
    assert plan.stage_counts["contact_usable"] >= 500 * 0.9


def test_tier_a_plus_b_is_cheaper_than_tier_a_alone() -> None:
    a = size(200, tier="a", pool_available=100_000)
    ab = size(200, tier="a+b", pool_available=100_000)
    assert a.discovery_needed > ab.discovery_needed


def test_news_why_now_costs_far_more_discovery_than_structural() -> None:
    """The dominant lever — demanding dated news roughly triples the top of the funnel."""
    structural = size(200, why_now_mode="structural", pool_available=100_000)
    news = size(200, why_now_mode="news", pool_available=100_000)
    assert news.discovery_needed > structural.discovery_needed * 2.5


def test_infeasible_when_pool_too_small_names_the_shortfall() -> None:
    """Fail closed, and say what would fix it — this is the message that was missing."""
    with pytest.raises(FunnelInfeasible) as exc:
        size(500, tier="a", why_now_mode="news", pool_available=1_303)
    msg = str(exc.value)
    assert "1,303" in msg
    assert "discover" in msg


def test_infeasible_when_lookup_credits_short() -> None:
    with pytest.raises(FunnelInfeasible) as exc:
        size(500, pool_available=100_000, lookup_credits_remaining=5, no_fallback=True)
    assert "credits remain" in str(exc.value)


def test_backlog_reduces_discovery_and_is_reported() -> None:
    """An already-qualified account needing only a lookup is the cheapest source."""
    without = size(300, pool_available=100_000)
    with_backlog = size(300, pool_available=100_000, backlog_ready=400)
    assert with_backlog.discovery_needed < without.discovery_needed
    assert any("backlog" in w for w in with_backlog.warnings)


def test_backlog_alone_can_remove_the_need_to_discover() -> None:
    plan = size(50, pool_available=0, backlog_ready=500)
    assert plan.discovery_needed == 0


def test_stage_tripwire_passes_at_modelled_rate() -> None:
    plan = size(100, pool_available=100_000)
    v = check_stage("contact_usable", 100, 68, plan)
    assert v["ok"] is True


def test_stage_tripwire_stops_a_cold_stage_and_projects_the_shortfall() -> None:
    """A stage at half the modelled rate must stop the run, not shrink the delivery."""
    plan = size(500, pool_available=100_000)
    v = check_stage("contact_usable", 100, 20, plan)  # 20% vs 68% modelled
    assert v["ok"] is False
    assert v["projected_delivery"] < 500
    assert "STOP" in v["message"]


def test_bad_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        size(0)
    with pytest.raises(ValueError):
        size(10, tier="nope", pool_available=100)
    with pytest.raises(ValueError):
        size(10, why_now_mode="vibes", pool_available=100)
    with pytest.raises(ValueError):
        check_stage("not_a_stage", 1, 1, size(10, pool_available=100))


def test_profile_yields_override_defaults_and_roundtrip(tmp_path) -> None:
    root = tmp_path / "profiles" / "acme"
    (root / "knowledge").mkdir(parents=True)
    record_actuals(root, {"seat_found": 0.5, "contact_usable": 0.5})
    y = load_yields(root)
    assert y["seat_found"] == 0.5
    assert y["contact_usable"] == 0.5
    # a worse-yielding profile must need more discovery for the same delivery
    worse = size(100, profile_root=root, pool_available=100_000)
    default = size(100, pool_available=100_000)
    assert worse.discovery_needed > default.discovery_needed


def test_missing_yields_file_falls_back_to_defaults(tmp_path) -> None:
    assert load_yields(tmp_path)["seat_found"] > 0
    assert load_yields(None)["contact_usable"] > 0


def test_plan_is_serialisable_and_covers_every_stage() -> None:
    plan = size(100, pool_available=100_000)
    assert isinstance(plan, Plan)
    d = plan.to_dict()
    assert set(d["stage_counts"]) == set(STAGES)
    assert set(d["yields"]) >= set(STAGES)


def test_yield_tables_are_sane() -> None:
    for table in (TIER_YIELDS, WHY_NOW_YIELDS):
        for k, v in table.items():
            assert 0 < v <= 1, f"{k}={v} outside (0,1]"
    # Tier A must be strictly harder to clear than A+B, else the model is inverted.
    assert TIER_YIELDS["a"] < TIER_YIELDS["a+b"]
    assert WHY_NOW_YIELDS["news"] < WHY_NOW_YIELDS["structural"]
