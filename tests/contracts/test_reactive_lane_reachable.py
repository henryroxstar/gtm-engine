"""Contract: the reactive skills are reachable from a pack, not just registered.

The 2026-08-27 wiring-gap PRD §2.4 measured that "27 of 58 skills sit in no pack and on no
timer, and they are exactly the reactive ones", and named them. W4 gave `inbound-triage` a
pack. The rest were still reachable from nothing on 2026-08-29, which in backend pack mode
means the tool layer REFUSES them — a skill can be registered, documented, entitled, and
still unrunnable.

This is the "held open by a named test, not a comment" remedy the PRD applies to every
other instance of the shape. Deleting `packs/engagement/` or dropping it from a profile's
`packs.toml` fails here rather than silently restoring the gap.

Scoped to the skills the PRD named. This file is NOT a floor on total reachability — 19
skills remain outside any pack by design (on-demand one-offs and the onboarding family),
and asserting a count here would just break every time a pack legitimately changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core.packs.loader import load_pack_graph
from gtm_core.packs.reachability import active_skills_for_profile
from gtm_core.skills.registry import all_skills

REPO = Path(__file__).resolve().parents[2]

#: Verbatim from the 2026-08-27 wiring-gap-closure design note, §2.4.
REACTIVE_SKILLS = (
    "inbound-triage",
    "linkedin-reply",
    "reddit-reply",
    "linkedin-engagers",
    "market-scan",
    "market-intelligence",
    "community-signal-analysis",
    "call-prep",
)


def test_every_reactive_skill_the_prd_named_is_registered():
    """Guards the assertion below from passing vacuously on a renamed skill."""
    registered = {s.name for s in all_skills()}
    missing = sorted(set(REACTIVE_SKILLS) - registered)
    assert not missing, (
        f"{missing} are named in this contract but no longer registered — rename them here "
        "or the reachability assertion below silently stops covering them"
    )


def _profile_slugs_activating_engagement() -> list[str]:
    """Any tenant whose packs.toml turns on `engagement` — not a hardcoded tenant name.

    Tenant identity is data, not something a company-agnostic test should spell out
    (tests/lint/debrand_check.sh --release scans this file's own scope), and hardcoding
    one tenant would also make this contract silently stop covering a second tenant that
    later activates the same pack.
    """
    slugs = []
    for path in sorted((REPO / "profiles").glob("*/packs.toml")):
        if "engagement" in path.read_text():
            slugs.append(path.parent.name)
    return slugs


PROFILES_WITH_ENGAGEMENT = _profile_slugs_activating_engagement()


@pytest.mark.private_tree  # carve ships profiles/_template only, which activates no pack
def test_a_tenant_actually_activates_the_engagement_pack():
    """Guards the parametrized test below from passing vacuously if none do."""
    assert PROFILES_WITH_ENGAGEMENT, "no profile activates 'engagement' — nothing to check"


@pytest.mark.parametrize("skill", REACTIVE_SKILLS)
@pytest.mark.parametrize("profile", PROFILES_WITH_ENGAGEMENT or ["<none>"])
def test_the_reactive_skill_is_reachable_from_the_tenants_active_packs(profile, skill):
    if not PROFILES_WITH_ENGAGEMENT:
        pytest.skip("no profile activates 'engagement'")
    reachable = active_skills_for_profile(REPO / "profiles", profile, REPO / "packs")
    assert skill in reachable, (
        f"{skill!r} is registered but reachable from no active pack. In backend pack mode "
        "the SDK skills= allowlist hides it and the can_use_tool callback denies it, so it "
        "is unrunnable there however well documented it is."
    )


def test_the_engagement_pack_gates_exactly_the_nodes_that_produce_outbound_drafts():
    """A draft the operator posts by hand pauses for review; an internal doc does not.

    The narrow regression this catches is a gate quietly removed from social/forum reply,
    which would let a timer-driven run produce outbound copy nobody approved.
    """
    graphs = {}
    for path in sorted((REPO / "packs" / "engagement" / "graphs").glob("*.toml")):
        g = load_pack_graph(path)
        graphs[g.variant] = g

    assert set(graphs) == {
        "social-reply",
        "forum-reply",
        "community-watch",
        "call-prep",
    }, f"engagement pack variants changed: {sorted(graphs)}"

    for variant in ("social-reply", "forum-reply"):
        nodes = graphs[variant].nodes
        assert any(n.gate for n in nodes), f"{variant} produces outbound copy with no gate"

    for variant in ("community-watch", "call-prep"):
        for n in graphs[variant].nodes:
            assert n.external_effect is None, (
                f"{variant}.{n.id} declares an external effect; this pack writes internal "
                "documents only"
            )


def test_market_intelligence_pack_writes_internal_documents_only():
    mw_path = REPO / "packs" / "market-intelligence" / "graphs" / "market-watch.toml"
    assert mw_path.is_file(), "market-intelligence pack missing market-watch variant"
    graph = load_pack_graph(mw_path)
    for n in graph.nodes:
        assert n.external_effect is None, f"market-watch.{n.id} declares an external effect"


def test_pii_bearing_engagement_nodes_stay_on_claude():
    """`linkedin-engagers` turns real people into a prospect list; `call-prep` maps real
    attendees. The worker path is prohibited for those exactly as it is for the judge."""
    pii_skills = {"linkedin-engagers", "call-prep", "community-signal-analysis"}
    for path in sorted((REPO / "packs" / "engagement" / "graphs").glob("*.toml")):
        for n in load_pack_graph(path).nodes:
            if n.skill in pii_skills:
                assert n.model_role == "brain_plan", (
                    f"{path.name}:{n.id} runs PII-bearing {n.skill!r} on {n.model_role!r}"
                )
