"""gtm_core/gating.toml is the single source of truth for commercial tier
(min_entitlement) and OSS distribution (public/private). This file has two halves:

  1. Golden-roster tests against the REAL packs/ tree and the REAL gating.toml — the
     two founder decisions (airq-scan free+public; marketing pro_plus, skills stay
     public) encoded as regression fixtures, per the PRD's own risk table ("the three
     axes get re-fused by a later change").
  2. Fail-closed unit tests against fixture policy files — one per named rejection rule,
     mirroring tests/contracts/test_pack_loader.py's per-rule style.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gtm_core import gating
from gtm_core.capabilities import entitlement_meets, entitlement_rank
from gtm_core.packs.loader import PackNode, load_pack_graph
from gtm_core.skills.registry import all_skills

REPO = Path(__file__).resolve().parents[2]
PACKS_ROOT = REPO / "packs"

_HEADER = 'defaults.core = "free"\n'  # never used bare; see _write_policy


def _write_policy(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "gating.toml"
    path.write_text(
        '[defaults]\ncore = "free"\npipeline = "pro"\nproduction = "pro_plus"\n' + body,
        encoding="utf-8",
    )
    return path


def _real_graphs():
    return sorted(PACKS_ROOT.glob("*/graphs/*.toml"))


# ── golden roster: the real packs/ tree against the real gating.toml ───────────────


def test_real_gating_toml_loads():
    policy = gating.load_policy()
    assert policy.defaults["production"] == "pro_plus"


@pytest.mark.parametrize(
    "graph_path", _real_graphs(), ids=lambda p: f"{p.parent.parent.name}/{p.stem}"
)
def test_every_real_graph_clears_its_derived_floor(graph_path):
    """The floor rule (PRD §3.1): resolve_graph_entitlement never raises for a real
    graph — an override or literal header that under-shoots its own nodes' floor is
    exactly what this would catch, on every real pack file, every time gating.toml or
    a graph's node list changes."""
    graph = load_pack_graph(graph_path)
    effective = gating.resolve_graph_entitlement(
        graph.pack, graph.variant, graph.nodes, explicit=graph.min_entitlement
    )
    floor = gating.derive_graph_floor(graph.nodes)
    assert entitlement_rank(effective) >= entitlement_rank(floor)


def test_no_private_skill_sits_in_a_free_graph():
    """Cross-axis invariant (PRD §3.3): a free user must never reach a stub.

    The margin here is ONE rung, not the two an earlier version of this docstring
    claimed: the 2026-08-17b creator repricing put every private skill's commercial
    floor at "pro", so a graph carrying one derives at least "pro" and cannot be free.
    That margin is asserted below rather than restated in prose, and the per-graph
    sweep runs regardless — a future override combination that erodes it is caught
    here even if the assertion is later relaxed."""
    for name in gating.stub_list():
        assert entitlement_rank(gating.commercial_floor(name)) > entitlement_rank("free"), (
            f"private skill {name!r} is commercially free — a free user would reach a stub"
        )
    for graph_path in _real_graphs():
        graph = load_pack_graph(graph_path)
        effective = gating.resolve_graph_entitlement(
            graph.pack, graph.variant, graph.nodes, explicit=graph.min_entitlement
        )
        if effective != "free":
            continue
        for n in graph.nodes:
            if n.skill is None:
                continue
            assert gating.oss_visibility(n.skill) == "public", (
                f"{graph.pack}/{graph.variant} is free but node {n.id!r} runs "
                f"private skill {n.skill!r}"
            )


def test_every_stub_bearing_graph_is_declared_in_carve_policy():
    """Distribution counterpart to the free-graph invariant above, and the gap that one
    structurally cannot see: a self-hoster has no entitlement (the unscoped cockpit/VPS
    path runs allowed_skills=None), so the commercial floor keeping a free workspace off
    a stub does nothing for them. Shipping a stub-bearing graph is allowed — the DAG is
    the reusable part — but only as a DECLARED choice, never as a silent by-product of a
    later `oss = "private"` edit."""
    undeclared = gating.undeclared_stub_bearing_graphs(PACKS_ROOT)
    assert not undeclared, (
        "undeclared stub-bearing graph(s) — add to [carve].stub_bearing_graphs in "
        f"gtm_core/gating.toml with a reason, or stop shipping them: {undeclared}"
    )
    stale = gating.stale_stub_bearing_declarations(PACKS_ROOT)
    assert not stale, (
        "[carve].stub_bearing_graphs names graph(s) that carry no private node — a stale "
        f"entry silently widens what a future edit may ship: {stale}"
    )


def test_stub_bearing_graph_roster_is_the_declared_creator_lanes():
    """Pins the roster itself, not just that it is declared: a NEW graph becoming
    stub-bearing is a distribution decision, and reaching this assertion is how it gets
    made deliberately."""
    assert set(gating.stub_bearing_graphs(PACKS_ROOT)) == {
        "creator/cross-modal-campaign",
        "creator/demo-clips",
        "creator/live-action-video",
        "creator/presenter-video",
        "creator/repurpose-clips",
        "creator/restyle-shorts",
        "creator/short-form-video",
        "market-intelligence/market-watch",
        "outcomes-loop/content-outcomes-loop",
        "outcomes-loop/outcomes-loop",
    }


def test_stub_bearing_graphs_all_price_above_free():
    """The two axes must agree on these five: each ships a hosted-only node AND is priced
    so no free workspace reaches it. If a repricing ever drops one to free, the commercial
    invariant and this declaration would contradict each other."""
    for ref in gating.stub_bearing_graphs(PACKS_ROOT):
        pack, variant = ref.split("/", 1)
        graph = load_pack_graph(PACKS_ROOT / pack / "graphs" / f"{variant}.toml")
        effective = gating.resolve_graph_entitlement(
            graph.pack, graph.variant, graph.nodes, explicit=graph.min_entitlement
        )
        assert entitlement_rank(effective) > entitlement_rank("free"), ref


def test_carve_policy_rejects_a_declaration_with_no_reason(tmp_path):
    path = _write_policy(tmp_path, '[carve]\nstub_bearing_graphs = ["creator/x"]\n')
    with pytest.raises(gating.GatingPolicyError) as e:
        gating.load_policy(path)
    assert e.value.rule == "missing_reason"


def test_carve_policy_rejects_unknown_key(tmp_path):
    path = _write_policy(tmp_path, '[carve]\nnope = 1\nreason = "r"\n')
    with pytest.raises(gating.GatingPolicyError) as e:
        gating.load_policy(path)
    assert e.value.rule == "unknown_key"


def test_carve_policy_rejects_malformed_roster(tmp_path):
    path = _write_policy(tmp_path, '[carve]\nstub_bearing_graphs = "creator/x"\nreason = "r"\n')
    with pytest.raises(gating.GatingPolicyError) as e:
        gating.load_policy(path)
    assert e.value.rule == "malformed_carve"


def test_carve_policy_defaults_to_an_empty_roster(tmp_path):
    """Absent [carve] means nothing is declared — so any stub-bearing graph fails closed
    rather than being grandfathered in."""
    policy = gating.load_policy(_write_policy(tmp_path, ""))
    assert policy.carve_stub_bearing_graphs == frozenset()


def test_stub_body_refuses_to_improvise():
    """The stub is what the agent actually reads at that node. It must halt the run, not
    invite a substitute — an improvised replacement spends budget producing something the
    pack never specified."""
    skill = next(s for s in all_skills() if s.name == "video-render")
    body = gating.render_stub_body(skill, ["creator/short-form-video"])
    assert "Stop here." in body
    assert "do not fall back to" in body
    assert "creator/short-form-video" in body


def test_skill_overrides_name_live_skills():
    policy = gating.load_policy()
    known = {s.name for s in all_skills()}
    for name in policy.skill_overrides:
        assert name in known, f"[skills.{name}] in gating.toml names no registered skill"


def test_graph_overrides_match_a_real_graph():
    policy = gating.load_policy()
    real_keys = {f"{p.parent.parent.name}/{p.stem}" for p in _real_graphs()}
    for pattern in policy.graph_overrides:
        import fnmatch

        matched = [k for k in real_keys if fnmatch.fnmatchcase(k, pattern)]
        assert matched, f'[graphs."{pattern}"] in gating.toml matches no real pack/variant'


# ── golden case 1: airq-scan is technically PRODUCTION, sold free, ships public ────


def test_airq_scan_is_free_and_public_despite_being_technically_production():
    """Founder decision (2026-08-17), PRD §2.2/§11.1 — the reference case for why
    commercial tier and technical tier are separate axes."""
    skill = next(s for s in all_skills() if s.name == "airq-scan")
    from gtm_core.tiers import Tier

    assert skill.capability_tier is Tier.PRODUCTION
    assert gating.commercial_floor("airq-scan") == "free"
    assert gating.oss_visibility("airq-scan") == "public"
    assert "airq-scan" not in gating.stub_list()


# ── golden case 2: marketing prices above its node floor; skills stay public ───────


@pytest.mark.parametrize(
    "graph_path",
    sorted((PACKS_ROOT / "marketing" / "graphs").glob("*.toml")),
    ids=lambda p: p.stem,
)
def test_marketing_graphs_price_pro_plus_but_skills_stay_public(graph_path):
    """Founder decision (2026-08-17, restated 2026-08-25): the marketing pack is a paid
    SKU at pro_plus, but every skill it references remains public — what a workspace pays
    is not a reason to withhold the code from a self-hoster.

    Since 2026-08-25 the pro_plus floor is DERIVED from the six marketing skills' own
    [skills.*] floors rather than imposed by a [graphs."marketing/*"] override, so this
    also pins that the removal of that override did not lower the price."""
    graph = load_pack_graph(graph_path)
    effective = gating.resolve_graph_entitlement(
        graph.pack, graph.variant, graph.nodes, explicit=graph.min_entitlement
    )
    assert effective == "pro_plus"
    for n in graph.nodes:
        if n.skill is not None:
            assert gating.oss_visibility(n.skill) == "public", n.skill


def test_creator_pack_graphs_priced_pro_plus_via_shared_marketing_nodes():
    """Founder decision 2026-08-25: raising the six marketing skills to pro_plus pulls
    every creator GRAPH up with them, because five of those skills are creator nodes too
    (content-publish, content-plan, content-radar, content-studio) and a graph's floor is
    max(node floors) with overrides able to raise but never lower it.

    The nine creator RENDER skills keep their own "pro" floor from 2026-08-17b — pinned
    below — so the graph price and the skill price are asserted separately. This test
    exists to make the coupling visible: decoupling creator from marketing means giving
    the shared nodes their own floors, not overriding the graphs."""
    for graph_path in sorted((PACKS_ROOT / "creator" / "graphs").glob("*.toml")):
        graph = load_pack_graph(graph_path)
        effective = gating.resolve_graph_entitlement(
            graph.pack, graph.variant, graph.nodes, explicit=graph.min_entitlement
        )
        assert effective == "pro_plus", graph_path.stem
        for n in graph.nodes:
            if n.skill is not None:
                # commercial repricing must never touch distribution — every private
                # render skill in the pack stays private regardless of tier.
                from gtm_core.tiers import Tier

                skill = next(s for s in all_skills() if s.name == n.skill)
                if skill.capability_tier is Tier.PRODUCTION:
                    assert gating.oss_visibility(n.skill) == "private", n.skill
                    assert gating.commercial_floor(n.skill) == "pro_plus", n.skill


def test_stub_list_matches_the_expected_ten_skill_roster():
    """PRD §3.3's resolved roster plus video planning/finishing, outcomes-loop, and
    market-intelligence. Pins the exact set of private skills."""
    assert gating.stub_list() == frozenset(
        {
            "video-render",
            "video-restyle",
            "video-clip",
            "video-storyboard",
            "video-avatar",
            "carousel-visuals",
            "carousel-auto",
            "demo-capture",
            "infographic-data",
            "infographic-handwritten",
            "creator-brief",
            "video-script",
            "video-finish",
            "video-score",
            "video-router",
            "video-plan",
            "video-preview",
            "video-footage",
            "outcomes-sync",
            "content-outcomes-sync",
            "market-harvest",
            "market-intelligence",
        }
    )


def test_oss_visibility_defaults_from_technical_tier_not_commercial_floor():
    """A PIPELINE skill's commercial floor must never dictate OSS visibility.
    content-radar and content-publish prove that a skill priced at pro_plus defaults to
    public when it has no explicit oss override."""
    assert (
        gating.commercial_floor("content-radar") == "pro_plus"
    )  # non-vacuous: floor is the TOP rung, still public
    assert gating.oss_visibility("content-radar") == "public"
    assert gating.commercial_floor("content-publish") == "pro_plus"
    assert gating.oss_visibility("content-publish") == "public"


def test_unknown_skill_floors_at_the_highest_rung():
    assert gating.commercial_floor("not-a-real-skill") == "pro_plus"
    assert gating.oss_visibility("not-a-real-skill") == "private"


def test_entitled_skills_intersects_reachability_with_commercial_floor(tmp_path):
    from gtm_core.packs.reachability import entitled_skills_for_profile

    profiles_root = tmp_path / "profiles"
    (profiles_root / "acme").mkdir(parents=True)
    (profiles_root / "acme" / "packs.toml").write_text('active = ["creator"]\n', encoding="utf-8")

    free_reach = entitled_skills_for_profile(profiles_root, "acme", PACKS_ROOT, "free")
    pro_plus_reach = entitled_skills_for_profile(profiles_root, "acme", PACKS_ROOT, "pro_plus")

    assert "video-render" not in free_reach
    assert "video-render" in pro_plus_reach
    assert free_reach <= pro_plus_reach  # monotone: a higher tier never reaches less


# ── fail-closed unit tests against fixture policy files ────────────────────────────


def test_rejects_missing_default_tier(tmp_path):
    path = tmp_path / "gating.toml"
    path.write_text('[defaults]\ncore = "free"\npipeline = "pro"\n', encoding="utf-8")
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.load_policy(path)
    assert exc.value.rule == "missing_default"


def test_rejects_unknown_entitlement_in_defaults(tmp_path):
    path = tmp_path / "gating.toml"
    path.write_text(
        '[defaults]\ncore = "free"\npipeline = "pro"\nproduction = "platinum"\n', encoding="utf-8"
    )
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.load_policy(path)
    assert exc.value.rule == "unknown_entitlement"


def test_rejects_skill_override_missing_reason(tmp_path):
    path = _write_policy(tmp_path, '\n[skills.airq-scan]\nmin_entitlement = "free"\n')
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.load_policy(path)
    assert exc.value.rule == "missing_reason"


def test_rejects_skill_override_unknown_key(tmp_path):
    path = _write_policy(tmp_path, '\n[skills.airq-scan]\nreason = "x"\nprice_usd = 5\n')
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.load_policy(path)
    assert exc.value.rule == "unknown_key"


def test_rejects_graph_override_missing_min_entitlement(tmp_path):
    path = _write_policy(tmp_path, '\n[graphs."marketing/*"]\nreason = "x"\n')
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.load_policy(path)
    assert exc.value.rule == "missing_min_entitlement"


def test_rejects_unknown_oss_value(tmp_path):
    path = _write_policy(tmp_path, '\n[skills.airq-scan]\nreason = "x"\noss = "semi-public"\n')
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.load_policy(path)
    assert exc.value.rule == "unknown_oss"


def test_rejects_unknown_oss_private_tier(tmp_path):
    path = tmp_path / "gating.toml"
    path.write_text(
        '[defaults]\ncore = "free"\npipeline = "pro"\nproduction = "pro_plus"\n'
        'oss_private_tiers = ["legendary"]\n',
        encoding="utf-8",
    )
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.load_policy(path)
    assert exc.value.rule == "unknown_tier"


def test_graph_override_below_derived_floor_is_rejected():
    """A [graphs.*] override that under-shoots what its own nodes require must fail
    loudly (a stale override, not a silent no-op) — construct nodes that need pro_plus
    and a policy that only raises the pack to "pro"."""
    policy = gating.GatingPolicy(
        defaults={"core": "free", "pipeline": "pro", "production": "pro_plus"},
        oss_private_tiers=frozenset({"production"}),
        skill_overrides={},
        graph_overrides={"creator/*": {"min_entitlement": "pro", "reason": "x"}},
    )
    nodes = (PackNode(id="a", skill="video-render"),)  # PRODUCTION -> floor pro_plus
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.resolve_graph_entitlement("creator", "short-form-video", nodes, policy=policy)
    assert exc.value.rule == "override_below_floor"


def test_explicit_header_below_effective_is_rejected():
    policy = gating.load_policy()
    nodes = (PackNode(id="a", skill="video-render"),)
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.resolve_graph_entitlement("creator", "x", nodes, explicit="free", policy=policy)
    assert exc.value.rule == "below_floor"


def test_explicit_header_above_effective_is_honoured():
    """A literal header may always RAISE the effective value further — a variant author
    opting into a stricter gate than gating.toml alone would derive."""
    policy = gating.load_policy()
    nodes = (PackNode(id="a"),)  # no skill -> floor "free"
    effective = gating.resolve_graph_entitlement(
        "prospecting", "x", nodes, explicit="pro", policy=policy
    )
    assert effective == "pro"


def test_skill_override_raises_commercial_floor_without_touching_technical_tier():
    policy = gating.GatingPolicy(
        defaults={"core": "free", "pipeline": "pro", "production": "pro_plus"},
        oss_private_tiers=frozenset({"production"}),
        skill_overrides={"prospect": {"min_entitlement": "pro_plus", "reason": "x"}},
        graph_overrides={},
    )
    assert gating.commercial_floor("prospect", policy=policy) == "pro_plus"
    from gtm_core.tiers import Tier

    skill = next(s for s in all_skills() if s.name == "prospect")
    assert skill.capability_tier is Tier.CORE  # manifest untouched by the commercial override


# ── OSS carve stubbing (Gate C) ─────────────────────────────────────────────────────


def test_graph_refs_for_skill_finds_a_real_reference():
    refs = gating._graph_refs_for_skill("video-render", PACKS_ROOT)
    assert "creator/short-form-video" in refs


def test_graph_refs_for_skill_empty_for_a_skill_no_graph_uses():
    assert gating._graph_refs_for_skill("airq-scan", PACKS_ROOT) == []


def test_render_stub_body_names_the_interface_and_refs():
    skill = next(s for s in all_skills() if s.name == "video-render")
    body = gating.render_stub_body(skill, ["creator/short-form-video"])
    assert "hosted product" in body
    assert "`video-render`" in body
    assert "`creator/short-form-video`" in body
    assert "docs/SKILLS.md" in body


def test_stub_carve_wipes_directory_and_writes_a_stub(tmp_path):
    skill_dir = tmp_path / "plugin" / "skills" / "video-render"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: video-render\n---\noriginal body\n")
    (skill_dir / "body_template.md").write_text("the actual prompt IP\n")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "notes.md").write_text("more prompt IP\n")
    for other in gating.stub_list() - {"video-render"}:
        (tmp_path / "plugin" / "skills" / other).mkdir(parents=True)
        (tmp_path / "plugin" / "skills" / other / "SKILL.md").write_text("x")
    (tmp_path / "packs").mkdir()

    stubbed = gating.stub_carve(tmp_path)

    assert "video-render" in stubbed
    assert not (skill_dir / "body_template.md").exists()
    assert not (skill_dir / "references").exists()
    content = (skill_dir / "SKILL.md").read_text()
    assert "name: video-render" in content  # frontmatter preserved
    assert "hosted product" in content
    assert "original body" not in content


def test_stub_carve_raises_on_missing_skill_dir(tmp_path):
    (tmp_path / "plugin" / "skills").mkdir(parents=True)
    (tmp_path / "packs").mkdir()
    with pytest.raises(gating.GatingPolicyError) as exc:
        gating.stub_carve(tmp_path)
    assert exc.value.rule == "missing_skill_dir"


def test_find_leaked_content_filters_shared_boilerplate_but_catches_a_real_leak(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(gating, "stub_list", lambda **_kw: frozenset({"priv-skill"}))

    source = tmp_path / "source" / "skills"
    (source / "priv-skill").mkdir(parents=True)
    (source / "priv-skill" / "body_template.md").write_text(
        "this exact sentence is the private skill's own distinguishing prompt text\n"
        "this boilerplate sentence appears in many skill templates across the repo\n"
    )
    (source / "pub-skill").mkdir(parents=True)
    (source / "pub-skill" / "body_template.md").write_text(
        "this boilerplate sentence appears in many skill templates across the repo\n"
    )

    clean_carve = tmp_path / "clean_carve"
    (clean_carve / "plugin" / "skills" / "pub-skill").mkdir(parents=True)
    (clean_carve / "plugin" / "skills" / "pub-skill" / "SKILL.md").write_text(
        "this boilerplate sentence appears in many skill templates across the repo\n"
    )
    assert gating.find_leaked_content(clean_carve, plugin_source_root=tmp_path / "source") == []

    leaked_carve = tmp_path / "leaked_carve"
    (leaked_carve / "plugin" / "skills" / "pub-skill").mkdir(parents=True)
    (leaked_carve / "plugin" / "skills" / "pub-skill" / "SKILL.md").write_text(
        "this exact sentence is the private skill's own distinguishing prompt text\n"
    )
    hits = gating.find_leaked_content(leaked_carve, plugin_source_root=tmp_path / "source")
    assert len(hits) == 1
    assert "distinguishing prompt text" in hits[0]


def test_stub_carve_then_leak_check_clean_on_the_real_plugin_and_packs_tree(tmp_path):
    """End-to-end: copy the real plugin/ + packs/ trees, stub, then confirm the leak
    check reports nothing — the same sequence scripts/oss-export.sh runs on a real
    carve, exercised without touching the actual repo."""
    import shutil

    dest = tmp_path / "carve"
    shutil.copytree(REPO / "plugin", dest / "plugin", symlinks=True)
    shutil.copytree(REPO / "packs", dest / "packs")

    stubbed = gating.stub_carve(dest)
    assert set(stubbed) == gating.stub_list()
    assert gating.find_leaked_content(dest) == []


def test_entitlement_meets_agrees_with_resolve_graph_entitlement_for_real_graphs():
    """Sanity cross-check: a free workspace can never launch a graph this module marks
    non-free, and a pro_plus workspace can always launch every real graph."""
    for graph_path in _real_graphs():
        graph = load_pack_graph(graph_path)
        effective = gating.resolve_graph_entitlement(
            graph.pack, graph.variant, graph.nodes, explicit=graph.min_entitlement
        )
        assert entitlement_meets("pro_plus", effective)
        if effective != "free":
            assert not entitlement_meets("free", effective)


def test_extract_and_strip_interface_contract():
    sample = (
        "# Skill Title\n\n"
        "Introductory guidance.\n\n"
        "## Interface Contract\n\n"
        "- input: target account name\n"
        "- output: account brief\n\n"
        "## Step 1 - Do Private Work\n\n"
        "Top secret instructions.\n"
    )
    contract = gating.extract_interface_contract(sample)
    assert "- input: target account name" in contract
    assert "- output: account brief" in contract
    assert "Top secret instructions" not in contract

    stripped = gating.strip_interface_contract(sample)
    assert "- input: target account name" not in stripped
    assert "Top secret instructions" in stripped
    assert "# Skill Title" in stripped


def test_stub_carve_preserves_interface_contract_and_passes_leak_check(tmp_path, monkeypatch):
    monkeypatch.setattr(gating, "stub_list", lambda **_kw: frozenset({"priv-skill"}))

    from gtm_core.skills.base import GTMSkill
    from gtm_core.tiers import Tier

    mock_skill = GTMSkill(
        name="priv-skill",
        capability_tier=Tier.PRODUCTION,
        version="0.1.0",
        phase="1",
        description="A private skill that does private things.",
    )
    monkeypatch.setattr(gating, "_skills_by_name", lambda: {"priv-skill": mock_skill})

    source = tmp_path / "source" / "skills"
    priv_dir = source / "priv-skill"
    priv_dir.mkdir(parents=True)
    body_content = (
        "# Private Skill\n\n"
        "## Interface Contract\n\n"
        "Declared parameters: foo, bar, baz.\n\n"
        "## Internal Implementation\n\n"
        "This distinguishing sentence must never leak to carved root!\n"
    )
    (priv_dir / "body_template.md").write_text(body_content)

    carve_root = tmp_path / "carve"
    carved_priv = carve_root / "plugin" / "skills" / "priv-skill"
    carved_priv.mkdir(parents=True)
    (carved_priv / "body_template.md").write_text(body_content)
    (carve_root / "packs").mkdir(parents=True)

    stubbed = gating.stub_carve(carve_root)
    assert stubbed == ["priv-skill"]

    carved_skill_md = (carved_priv / "SKILL.md").read_text(encoding="utf-8")
    assert "## Interface Contract" in carved_skill_md
    assert "Declared parameters: foo, bar, baz." in carved_skill_md
    assert "This distinguishing sentence must never leak" not in carved_skill_md

    # Leak check: the interface contract in SKILL.md should NOT fail leak-check,
    # and the withheld implementation was wiped so no hits are found.
    hits = gating.find_leaked_content(carve_root, plugin_source_root=tmp_path / "source")
    assert hits == []
