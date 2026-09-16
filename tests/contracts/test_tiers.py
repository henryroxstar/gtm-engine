"""Phase B contract tests: capability-tier resolver decision matrix.

Every combination of (runtime_kind × entitlement × connector_set) that the
program plan defines as a hard invariant is tested here. These are the
machine-readable form of the boundary table in the program plan and in
gtm_core/capabilities.py's module docstring.

``resolve_effective`` applies TWO gates in order, and the split is what these
invariants are really about:

  commercial — "has this workspace paid to reach this skill?" Owned entirely by
               gtm_core/gating.toml, read via ``gating.commercial_floor(name)``.
               Skipped on the PLUGIN runtime (BYO key, no billing relationship).
  technical  — "can this runtime actually provide it?" The ``capability_tier``
               + runtime + connector logic in capabilities.py.

Until 2026-08-25 the resolver carried its OWN copy of the commercial ladder
("PIPELINE needs pro, PRODUCTION needs pro_plus"), which disagreed with
gating.toml in both directions — see I10, the regression test for that bug.

Invariants proven:
  I1. CORE is "allowed" whenever its commercial floor is met (and always on PLUGIN).
  I2. PRODUCTION is ALWAYS "denied" from PLUGIN and MCP runtimes (even with
      full connectors and PRO_PLUS entitlement).
  I3. ANY skill is "denied" in a paid runtime when the workspace's entitlement is
      below that skill's gating.toml floor — whatever its tier.
  I4. PRODUCTION is "allowed" on VPS/BACKEND + floor met + compute connectors.
  I5. PRODUCTION is "denied" on VPS/BACKEND + floor met + NO compute connectors
      (PRODUCTION has no fallback path).
  I6. PIPELINE is "fallback" (not "denied") on PLUGIN — skills degrade
      gracefully, they are never hard-blocked in the free plugin.
  I7. PLUGIN NEVER consults entitlement — the verdict is tier-only.
  I8. PIPELINE is "allowed" on floor met + pipeline connectors present.
  I9. PIPELINE is "fallback" on floor met + NO pipeline connectors.
  I10. The resolver's entitlement decision matches gating.toml for EVERY
      registered skill — no second ladder anywhere in capabilities.py.
"""

import pytest

from gtm_core import gating
from gtm_core.capabilities import (
    ConnectorSet,
    Entitlement,
    RuntimeContext,
    RuntimeKind,
    entitlement_meets,
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

PAID_RUNTIMES = [RuntimeKind.VPS, RuntimeKind.BACKEND, RuntimeKind.MCP]


def _skill(name: str, tier: Tier, floor: str):
    """A REAL registered skill, asserted to still hold the (tier, floor) pair the
    test below depends on.

    Deliberately not a synthetic ``GTMSkill``: an unregistered name fail-closes to
    ``pro_plus`` in ``commercial_floor``, so a synthetic skill cannot express "free"
    at all. Pinning real names also means a gating.toml repricing fails HERE, loudly,
    instead of silently turning an invariant into a tautology.
    """
    skill = next((s for s in registry.all_skills() if s.name == name), None)
    assert skill is not None, f"{name!r} is no longer a registered skill"
    assert skill.capability_tier is tier, f"{name!r} is no longer tier {tier}"
    assert gating.commercial_floor(name) == floor, (
        f"{name!r} is no longer priced {floor!r} in gating.toml — pick another fixture "
        f"for this invariant rather than relaxing it"
    )
    return skill


# The (technical tier × commercial floor) cross-product that actually exists today.
# Both axes are exercised because they are independent: a CORE skill can be paid
# (`prospect`) and a PRODUCTION skill can be free (`airq-scan`).
CORE_FREE = _skill("account-dossier", Tier.CORE, "free")
# `prospect` held this slot until 2026-08-25, when it was repriced to free — the guard in
# _skill() is what caught that, which is the point of pinning names instead of scanning.
CORE_PAID = _skill("case-study", Tier.CORE, "pro_plus")
PIPELINE_FREE = _skill("knowledge-refresh", Tier.PIPELINE, "free")
PIPELINE_PAID = _skill("email-quality", Tier.PIPELINE, "pro")
PRODUCTION_FREE = _skill("airq-scan", Tier.PRODUCTION, "free")
PRODUCTION_PAID = _skill("carousel-visuals", Tier.PRODUCTION, "pro")

# Back-compat aliases for the tier-generic invariants (I2/I4/I5/I6).
CORE_SKILL = CORE_FREE
PIPELINE_SKILL = PIPELINE_PAID
PRODUCTION_SKILL = PRODUCTION_PAID


# ── I1: CORE is allowed whenever its commercial floor is met ──────────────────


@pytest.mark.parametrize("runtime_kind", list(RuntimeKind))
@pytest.mark.parametrize("entitlement", list(Entitlement))
@pytest.mark.parametrize(
    "connectors", [ALL_CONNECTORS, NO_CONNECTORS], ids=["all-connectors", "no-connectors"]
)
def test_core_never_denied_when_entitled(runtime_kind, entitlement, connectors):
    """A free CORE skill is allowed under every runtime/entitlement/connector state —
    no technical condition can downgrade CORE."""
    ctx = RuntimeContext(runtime_kind=runtime_kind, entitlement=entitlement, connectors=connectors)
    assert resolve_effective(CORE_FREE, ctx) == "allowed"


@pytest.mark.parametrize("runtime_kind", PAID_RUNTIMES)
def test_core_denied_below_its_commercial_floor(runtime_kind):
    """CORE is a TECHNICAL tier, not a price. A CORE skill gating.toml prices above free
    is denied to a FREE workspace — the axes are independent."""
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=Entitlement.FREE, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(CORE_PAID, ctx) == "denied"


# ── I2: PRODUCTION denied on PLUGIN and MCP (hard lock) ──────────────────────


@pytest.mark.parametrize("runtime_kind", [RuntimeKind.PLUGIN, RuntimeKind.MCP])
@pytest.mark.parametrize("entitlement", list(Entitlement))
@pytest.mark.parametrize(
    "skill", [PRODUCTION_PAID, PRODUCTION_FREE], ids=["paid-production", "free-production"]
)
def test_production_denied_plugin_and_mcp(runtime_kind, entitlement, skill):
    """Runtime lock, not a pricing decision: even a commercially FREE production skill
    is denied here, and even at PRO_PLUS with full connectors."""
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(skill, ctx) == "denied"


# ── I3: below the skill's gating.toml floor is denied, at every tier ─────────


@pytest.mark.parametrize(
    ("skill", "insufficient"),
    [
        (CORE_PAID, Entitlement.FREE),
        (CORE_PAID, Entitlement.PRO),  # pro_plus skill: PRO is below its floor too
        (PIPELINE_PAID, Entitlement.FREE),
        (PIPELINE_PAID, Entitlement.NONE),
        (PRODUCTION_PAID, Entitlement.FREE),
        (PRODUCTION_PAID, Entitlement.NONE),
    ],
    ids=[
        "core-pro_plus/free",
        "core-pro_plus/pro",
        "pipeline-pro/free",
        "pipeline-pro/none",
        "prod-pro/free",
        "prod-pro/none",
    ],
)
@pytest.mark.parametrize("runtime_kind", PAID_RUNTIMES)
def test_denied_below_commercial_floor(runtime_kind, skill, insufficient):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=insufficient, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(skill, ctx) == "denied"


@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND])
def test_not_denied_for_entitlement_when_commercially_free(runtime_kind):
    """The other direction of the same bug: a FREE workspace must NOT be denied a
    skill gating.toml prices free, regardless of how heavy its technical tier is."""
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=Entitlement.FREE, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(PIPELINE_FREE, ctx) == "allowed"
    assert resolve_effective(PRODUCTION_FREE, ctx) == "allowed"


# ── I4: PRODUCTION allowed on VPS/BACKEND + floor met + compute ─────────────


@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND])
@pytest.mark.parametrize("entitlement", [Entitlement.PRO, Entitlement.PRO_PLUS])
def test_production_allowed_when_entitled_with_compute(runtime_kind, entitlement):
    """`video-render` is priced "pro" in gating.toml, so PRO is enough. The old
    hardcoded `entitlement != PRO_PLUS -> denied` rung failed exactly this case."""
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=COMPUTE_ONLY
    )
    assert resolve_effective(PRODUCTION_PAID, ctx) == "allowed"


# ── I5: PRODUCTION denied on VPS/BACKEND + floor met + NO compute ───────────


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


# ── I7: PLUGIN never consults entitlement ────────────────────────────────────


@pytest.mark.parametrize("entitlement", list(Entitlement))
@pytest.mark.parametrize(
    ("skill", "expected"),
    [
        (CORE_FREE, "allowed"),
        (CORE_PAID, "allowed"),
        (PIPELINE_FREE, "fallback"),
        (PIPELINE_PAID, "fallback"),
        (PRODUCTION_FREE, "denied"),
        (PRODUCTION_PAID, "denied"),
    ],
    ids=["core-free", "core-paid", "pipe-free", "pipe-paid", "prod-free", "prod-paid"],
)
def test_plugin_verdict_is_tier_only(entitlement, skill, expected):
    """The plugin is the free, local, BYO-key runtime: there is no workspace and no
    billing relationship to gate on, so the commercial floor is not consulted and the
    verdict depends only on capability_tier."""
    ctx = RuntimeContext(
        runtime_kind=RuntimeKind.PLUGIN, entitlement=entitlement, connectors=ALL_CONNECTORS
    )
    assert resolve_effective(skill, ctx) == expected


# ── I8: PIPELINE allowed when entitled + pipeline connectors ────────────────


@pytest.mark.parametrize("entitlement", [Entitlement.PRO, Entitlement.PRO_PLUS])
@pytest.mark.parametrize("runtime_kind", PAID_RUNTIMES)
def test_pipeline_allowed_paid_with_connectors(runtime_kind, entitlement):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=PIPELINE_ONLY
    )
    assert resolve_effective(PIPELINE_SKILL, ctx) == "allowed"


# ── I9: PIPELINE falls back when entitled + NO pipeline connectors ──────────


@pytest.mark.parametrize("entitlement", [Entitlement.PRO, Entitlement.PRO_PLUS])
@pytest.mark.parametrize("runtime_kind", PAID_RUNTIMES)
def test_pipeline_fallback_paid_no_connectors(runtime_kind, entitlement):
    ctx = RuntimeContext(
        runtime_kind=runtime_kind, entitlement=entitlement, connectors=NO_CONNECTORS
    )
    assert resolve_effective(PIPELINE_SKILL, ctx) == "fallback"


# ── I10: no second commercial ladder anywhere in the resolver ───────────────


@pytest.mark.parametrize("runtime_kind", [RuntimeKind.VPS, RuntimeKind.BACKEND])
@pytest.mark.parametrize("entitlement", [Entitlement.NONE, Entitlement.FREE, Entitlement.PRO])
def test_resolver_entitlement_decision_matches_gating_toml(runtime_kind, entitlement):
    """Regression test for the 2026-08-25 fix. For EVERY registered skill, a "denied"
    verdict with full connectors present must be explainable by gating.toml (or by the
    MCP/PLUGIN production lock) — never by a ladder hardcoded in capabilities.py.
    """
    mismatches = []
    for skill in registry.all_skills():
        entitled = entitlement_meets(entitlement, gating.commercial_floor(skill.name))
        ctx = RuntimeContext(
            runtime_kind=runtime_kind, entitlement=entitlement, connectors=ALL_CONNECTORS
        )
        verdict = resolve_effective(skill, ctx)
        if entitled and verdict == "denied":
            mismatches.append(
                f"{skill.name} ({skill.capability_tier.value}, floor "
                f"{gating.commercial_floor(skill.name)}): entitled at {entitlement.value} but denied"
            )
        if not entitled and verdict != "denied":
            mismatches.append(
                f"{skill.name}: NOT entitled at {entitlement.value} but verdict {verdict!r}"
            )
    assert not mismatches, "resolver disagrees with gating.toml:\n  " + "\n  ".join(mismatches)


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


def test_entitlement_meets_ordering():
    """min_entitlement comparisons (pack API): free < pro < pro_plus; NONE ranks as FREE."""
    assert entitlement_meets("pro_plus", "pro")
    assert entitlement_meets("pro", "pro")
    assert entitlement_meets(Entitlement.PRO, "free")
    assert not entitlement_meets("free", "pro")
    assert not entitlement_meets("none", "pro")  # un-synced never unlocks a paid tier
    assert entitlement_meets("none", "free")
    # Fail-closed on garbage: unknown current ranks FREE; unknown minimum unsatisfiable.
    assert not entitlement_meets("platinum", "pro")
    assert not entitlement_meets("pro_plus", "platinum")
