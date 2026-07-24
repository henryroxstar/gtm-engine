"""E-3 verification: tenant pack activation, monotone-stricter override merge (adversarial
gate-removal rejection), pack-skill reachability, and the generated knowledge index.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gtm_core.knowledge_index import build_knowledge_index, find_in_index
from gtm_core.packs.loader import PackNode, PackValidationError, load_pack_graph
from gtm_core.packs.reachability import active_skills_for_profile
from gtm_core.packs.tenant import (
    PackOverride,
    StrengthenEntry,
    load_pack_activation,
    merge_pack_override,
)

REPO = Path(__file__).resolve().parents[2]
LINKEDIN_POST = REPO / "packs" / "marketing" / "graphs" / "linkedin-post.toml"


@pytest.fixture()
def base_graph():
    return load_pack_graph(LINKEDIN_POST)


# ── monotone-stricter override merge ───────────────────────────────────────────


def test_override_can_add_a_new_node_and_extend_a_dependency(base_graph):
    override = PackOverride(
        pack="marketing",
        variant="linkedin-post",
        add_nodes=(
            PackNode(
                id="compliance-review",
                depends_on=("studio",),
                model_role="brain_plan",
                skill="content-plan",
                gate=True,
                external_effect="publish",  # a review gate, not itself the publish call
                prompt="Run a compliance review before publish.",
            ),
        ),
        strengthen=(StrengthenEntry(id="publish", add_depends_on=("compliance-review",)),),
    )
    merged = merge_pack_override(base_graph, override)

    assert "compliance-review" in merged.ids
    assert "compliance-review" in merged.node("publish").depends_on
    assert "studio" in merged.node("publish").depends_on  # original prerequisite kept


def test_override_can_turn_on_a_gate_that_was_off(base_graph):
    override = PackOverride(
        pack="marketing",
        variant="linkedin-post",
        strengthen=(StrengthenEntry(id="research", add_gate=True),),
    )
    merged = merge_pack_override(base_graph, override)
    assert merged.node("research").gate is True
    assert base_graph.node("research").gate is False  # base is untouched


def test_override_rejects_replacing_a_gated_node_via_add_nodes(base_graph):
    """Adversarial (D-01 style): a tenant cannot smuggle in a de-gated replacement of
    'publish' by re-declaring the same node id through add_nodes — the one way this
    override format could otherwise express "remove a gate"."""
    bad_override = PackOverride(
        pack="marketing",
        variant="linkedin-post",
        add_nodes=(PackNode(id="publish", depends_on=("studio",), gate=False),),
    )
    with pytest.raises(PackValidationError) as exc:
        merge_pack_override(base_graph, bad_override)
    assert exc.value.rule == "override_removal"


def test_override_rejects_strengthen_targeting_unknown_node(base_graph):
    override = PackOverride(
        pack="marketing",
        variant="linkedin-post",
        strengthen=(StrengthenEntry(id="does-not-exist", add_gate=True),),
    )
    with pytest.raises(PackValidationError) as exc:
        merge_pack_override(base_graph, override)
    assert exc.value.rule == "override_unknown_target"


def test_override_rejects_mismatched_pack_or_variant(base_graph):
    override = PackOverride(pack="wrong-pack", variant="linkedin-post")
    with pytest.raises(PackValidationError) as exc:
        merge_pack_override(base_graph, override)
    assert exc.value.rule == "override_mismatch"


def test_override_add_nodes_still_goes_through_full_graph_validation(base_graph):
    """An override that introduces a cycle is rejected exactly like a base graph would be."""
    override = PackOverride(
        pack="marketing",
        variant="linkedin-post",
        add_nodes=(PackNode(id="loopy", depends_on=("loopy",)),),
    )
    with pytest.raises(PackValidationError) as exc:
        merge_pack_override(base_graph, override)
    assert exc.value.rule == "cycle"


# ── tenant activation loading ──────────────────────────────────────────────────


def test_load_pack_activation_reads_active_list(tmp_path):
    path = tmp_path / "packs.toml"
    path.write_text('active = ["marketing", "prospecting"]\n')
    activation = load_pack_activation(path)
    assert activation.active == ("marketing", "prospecting")
    assert activation.overrides == {}


def test_load_pack_activation_reads_an_override(tmp_path):
    path = tmp_path / "packs.toml"
    path.write_text(
        'active = ["marketing"]\n\n'
        "[overrides.marketing]\n"
        'variant = "linkedin-post"\n\n'
        "[[overrides.marketing.add_nodes]]\n"
        'id = "compliance-review"\n'
        'depends_on = ["studio"]\n'
        "gate = true\n\n"
        "[[overrides.marketing.strengthen]]\n"
        'id = "publish"\n'
        'add_depends_on = ["compliance-review"]\n'
    )
    activation = load_pack_activation(path)
    assert activation.active == ("marketing",)
    ov = activation.overrides["marketing"]
    assert ov.variant == "linkedin-post"
    assert ov.strengthen[0].id == "publish"
    assert ov.strengthen[0].add_depends_on == ("compliance-review",)
    assert ov.add_nodes[0].id == "compliance-review"


def test_load_pack_activation_rejects_override_missing_variant(tmp_path):
    path = tmp_path / "packs.toml"
    path.write_text('active = ["marketing"]\n\n[overrides.marketing]\nfoo = "bar"\n')
    with pytest.raises(PackValidationError) as exc:
        load_pack_activation(path)
    assert exc.value.rule == "missing_override_variant"


# ── reachability: disabled pack's skills are unreachable ──────────────────────


def test_reachability_returns_empty_with_no_packs_toml(tmp_path):
    (tmp_path / "acme").mkdir()
    result = active_skills_for_profile(tmp_path, "acme", REPO / "packs")
    assert result == frozenset()


def test_reachability_returns_active_packs_skills_only(tmp_path):
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "packs.toml").write_text('active = ["marketing"]\n')
    result = active_skills_for_profile(tmp_path, "acme", REPO / "packs")
    assert result == {
        "content-radar",
        "content-plan",
        "content-research",
        "content-studio",
        "content-publish",
    }


def test_reachability_against_a_real_profile():
    """Grounding: a real profiles/<tenant>/packs.toml that activates the marketing pack
    makes its skills reachable. Discovers the profile rather than hardcoding a tenant,
    so it grounds on whatever bundles the checkout has (skips in a _template-only cut)."""
    from agent.profiles import list_profiles

    for name in sorted(list_profiles(REPO / "profiles")):
        result = active_skills_for_profile(REPO / "profiles", name, REPO / "packs")
        if "content-radar" in result:  # a marketing-activating profile grounds the contract
            return
    pytest.skip("no profile activates the marketing pack in this checkout")


# ── knowledge index ────────────────────────────────────────────────────────────


def test_index_includes_canonical_and_non_canonical_topics(tmp_path):
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    (kdir / "company.md").write_text("canonical")
    (kdir / "hook-matrix.md").write_text("messy tail")
    (kdir / "REFRESH.md").write_text("excluded, not a topic")

    index = build_knowledge_index(kdir)
    topics = {e.topic for e in index}
    assert topics == {"company", "hook-matrix"}

    company = find_in_index(index, "company")
    assert company.canonical is True
    hook_matrix = find_in_index(index, "hook-matrix")
    assert hook_matrix.canonical is False  # non-canonical file still resolves


def test_index_resolves_a_subfolder_file(tmp_path):
    kdir = tmp_path / "knowledge"
    (kdir / "brand").mkdir(parents=True)
    (kdir / "brand" / "guidance.md").write_text("nested")

    index = build_knowledge_index(kdir)
    entry = find_in_index(index, "guidance")
    assert entry is not None
    assert entry.relpath == os.path.join("brand", "guidance.md")


def test_index_returns_empty_tuple_for_missing_directory(tmp_path):
    assert build_knowledge_index(tmp_path / "does-not-exist") == ()


def test_index_computes_freshness_age(tmp_path):
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    path = kdir / "company.md"
    path.write_text("x")
    stale_mtime = time.time() - (10 * 86400)
    os.utime(path, (stale_mtime, stale_mtime))

    index = build_knowledge_index(kdir)
    entry = find_in_index(index, "company")
    assert 9.9 < entry.age_days < 10.1
