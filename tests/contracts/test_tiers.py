"""Phase B contract tests: capability-tier resolver decision matrix.

Every combination of (runtime_kind × entitlement × connector_set) that the
program plan defines as a hard invariant is tested here. These are the
machine-readable form of the boundary table in the program plan and in
gtm_core/capabilities.py's module docstring.

Invariants proven:
  I1. CORE is ALWAYS "allowed" — no runtime, entitlement, or connector state
      can produce any other verdict.
  I2. PRODUCTION is ALWAYS "denied" from PLUGIN and MCP runtimes (even with
      full connectors and PRO_PLUS entitlement).
  I3. PRODUCTION is ALWAYS "denied" on FREE, PRO, and NONE entitlement
      (even on VPS/BACKEND with full connectors).
  I4. PRODUCTION is "allowed" on VPS/BACKEND + PRO_PLUS + compute connectors.
  I5. PRODUCTION is "denied" on VPS/BACKEND + PRO_PLUS + NO compute connectors
      (PRODUCTION has no fallback path).
  I6. PIPELINE is "fallback" (not "denied") on PLUGIN — skills degrade
      gracefully, they are never hard-blocked in the free plugin.
  I7. PIPELINE is "denied" on FREE and NONE entitlement in paid runtimes.
  I8. PIPELINE is "allowed" on PRO/PRO_PLUS + pipeline connectors present.
  I9. PIPELINE is "fallback" on PRO/PRO_PLUS + NO pipeline connectors.
"""

import pytest

from gtm_core.capabilities import (
    ConnectorSet,
    Entitlement,
    RuntimeContext,
    RuntimeKind,
    resolve_effective,
)
from gtm_core.skills import registry
from gtm_core.tiers import Tier

# ── shared fixtures ───────────────────────────────────────────────────────────

ALL_CONNECTORS = ConnectorSet(
    has_news_db=True,
    has_publish=True,
    has_elevenlabs=True,
    has_higgsfield=True,
)
NO_CONNECTORS = ConnectorSet()
PIPELINE_ONLY = ConnectorSet(has_news_db=True, has_publish=True)
COMPUTE_ONLY = ConnectorSet(has_elevenlabs=True, has_higgsfield=True)


def _first(tier: Tier):
    """First registered skill with the given tier (registry is sorted by name)."""
    return next(s for s in registry.all_skills() if s.capability_tier == tier)


CORE_SKILL = _first(Tier.CORE)
PIPELINE_SKILL = _first(Tier.PIPELINE)
PRODUCTION_SKILL = _first(Tier.PRODUCTION)


# ── I1: CORE is always allowed ────────────────────────────────────────────────


@pytest.mark.parametrize("runtime_kind", list(RuntimeKind))
@pytest.mark.parametrize("entitlement", list(Entitlement))
@pytest.mark.parametrize(
    "connectors", [ALL_CONNECTORS, NO_CONNECTORS], ids=["all-connectors", "no-connectors"]
)
def test_core_never_denied(runtime_kind, entitlement, connectors):
    ctx = RuntimeContext(runtime_kind=runtime_kind, entitlement=entitlement, connectors=connectors)
    assert resolve_effective(CORE_SKILL, ctx) == "allowed"


# ── I2: PRODUCTION denied on PLUGIN and MCP (hard lock) ──────────────────────


@pytest.mark.parametrize("runtime_kind", [RuntimeKind.PLUGIN, RuntimeKind.MCP])
@pytest.mark.parametrize("entitlement", list(Entitlement))
def test_production_denied_plugin_and_mcp(runtime_kind, entitlement):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(PRODUCTION_SKILL, ctx) == "denied"


# ── I3: PRODUCTION denied on non-pro_plus entitlement ────────────────────────


@pytest.mark.parametrize("entitlement", [Entitlement.FREE, Entitlement.PRO, Entitlement.NONE])
@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND])
def test_production_denied_insufficient_entitlement(runtime_kind, entitlement):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(PRODUCTION_SKILL, ctx) == "denied"


# ── I4: PRODUCTION allowed on VPS/BACKEND + PRO_PLUS + compute ───────────────


@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND])
def test_production_allowed_pro_plus_with_compute(runtime_kind):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=Entitlement.PRO_PLUS, connectors=COMPUTE_ONLY
    )
    assert resolve_effective(PRODUCTION_SKILL, ctx) == "allowed"


# ── I5: PRODUCTION denied on VPS/BACKEND + PRO_PLUS + NO compute ─────────────


@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND])
def test_production_denied_no_compute_connectors(runtime_kind):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=Entitlement.PRO_PLUS, connectors=NO_CONNECTORS
    )
    assert resolve_effective(PRODUCTION_SKILL, ctx) == "denied"


# ── I6: PIPELINE falls back (not denied) on PLUGIN ───────────────────────────


@pytest.mark.parametrize(
    "connectors", [ALL_CONNECTORS, NO_CONNECTORS], ids=["all-connectors", "no-connectors"]
)
def test_pipeline_fallback_on_plugin(connectors):
    ctx = RuntimeContext(
        runtime_kind=RuntimeKind.PLUGIN, entitlement=Entitlement.FREE, connectors=connectors
    )
    result = resolve_effective(PIPELINE_SKILL, ctx)
    assert result == "fallback", (
        "PIPELINE on PLUGIN must degrade gracefully (fallback), never hard-deny"
    )


# ── I7: PIPELINE denied on FREE/NONE in paid runtimes ────────────────────────


@pytest.mark.parametrize("entitlement", [Entitlement.FREE, Entitlement.NONE])
@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND, RuntimeKind.MCP])
def test_pipeline_denied_insufficient_entitlement(runtime_kind, entitlement):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(PIPELINE_SKILL, ctx) == "denied"


# ── I8: PIPELINE allowed on PRO/PRO_PLUS + pipeline connectors ───────────────


@pytest.mark.parametrize("entitlement", [Entitlement.PRO, Entitlement.PRO_PLUS])
@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND, RuntimeKind.MCP])
def test_pipeline_allowed_paid_with_connectors(runtime_kind, entitlement):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=PIPELINE_ONLY
    )
    assert resolve_effective(PIPELINE_SKILL, ctx) == "allowed"


# ── I9: PIPELINE falls back on PRO/PRO_PLUS + NO pipeline connectors ─────────


@pytest.mark.parametrize("entitlement", [Entitlement.PRO, Entitlement.PRO_PLUS])
@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND, RuntimeKind.MCP])
def test_pipeline_fallback_paid_no_connectors(runtime_kind, entitlement):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=NO_CONNECTORS
    )
    assert resolve_effective(PIPELINE_SKILL, ctx) == "fallback"


# ── Registry completeness ─────────────────────────────────────────────────────


def test_every_skill_has_a_tier():
    skills = registry.all_skills()
    assert skills, "skill registry is empty"
    for skill in skills:
        assert isinstance(skill.capability_tier, Tier), f"{skill.name} missing capability_tier"


def test_all_three_tiers_represented():
    tiers = {s.capability_tier for s in registry.all_skills()}
    missing = {t for t in Tier if t not in tiers}
    assert not missing, f"no skill registered for tier(s): {missing}"


def test_connector_set_helpers():
    assert ConnectorSet(has_news_db=True).has_pipeline_connector()
    assert ConnectorSet(has_publish=True).has_pipeline_connector()
    assert not ConnectorSet().has_pipeline_connector()
    assert ConnectorSet(has_elevenlabs=True).has_compute_connector()
    assert ConnectorSet(has_higgsfield=True).has_compute_connector()
    assert not ConnectorSet().has_compute_connector()
