"""E-2 verification: the pack graph/inputs loader (gtm_core.packs.loader) is fail-closed
— one test per named rejection rule (docs/prds/2026-07-06-engine-pack-tenant-three-layer.md
§6) — plus a golden-equivalence test proving the first real pack graph
(packs/marketing/graphs/linkedin-post.toml) drives the engine identically to the
hardcoded news pipeline it replaces, and readiness-check tests for the two-class
inputs system (§5).
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from agent.packs import load_engine_graph, make_executor_from_pack
from agent.pipeline import DEFAULT_GRAPH, STAGES, PipelineRunner, StageOutcome
from agent.pipeline_executor import _STAGE_ROLES, STAGE_PROMPTS
from agent.readiness import GREEN, RED, YELLOW, check_readiness
from gtm_core.packs.loader import (
    PackInputKnowledge,
    PackInputs,
    PackInputSetting,
    PackValidationError,
    load_pack_graph,
    load_pack_inputs,
)

REPO = Path(__file__).resolve().parents[2]
PROFILE = "example"

_GRAPH_HEADER = 'pack = "p"\nvariant = "v"\n'


@pytest.fixture()
def cfg(tmp_path):
    """Local twin of tests/agent/conftest.py's ``cfg`` fixture — that conftest's scope
    doesn't reach tests/contracts/. Same shape: real profiles/ tree, throwaway content_root."""
    import dataclasses

    from agent.config import Config

    base = Config.from_env(repo_root=REPO)
    return dataclasses.replace(base, content_root=tmp_path / "content")


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


# ── load_pack_graph: one test per rejection rule ──────────────────────────────


def test_rejects_missing_header(tmp_path):
    path = _write(tmp_path, "g.toml", '[[nodes]]\nid = "a"\n')
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "missing_header"


def test_rejects_empty_graph(tmp_path):
    path = _write(tmp_path, "g.toml", _GRAPH_HEADER)
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "empty_graph"


def test_rejects_node_missing_id(tmp_path):
    path = _write(tmp_path, "g.toml", _GRAPH_HEADER + '[[nodes]]\nmodel_role = "brain_plan"\n')
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "missing_id"


def test_rejects_duplicate_node_ids(tmp_path):
    path = _write(
        tmp_path,
        "g.toml",
        _GRAPH_HEADER + '[[nodes]]\nid = "a"\n\n[[nodes]]\nid = "a"\n',
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "duplicate_node"


def test_rejects_dangling_dependency(tmp_path):
    path = _write(
        tmp_path, "g.toml", _GRAPH_HEADER + '[[nodes]]\nid = "a"\ndepends_on = ["ghost"]\n'
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "dangling_dependency"


def test_rejects_cycle(tmp_path):
    path = _write(
        tmp_path,
        "g.toml",
        _GRAPH_HEADER
        + '[[nodes]]\nid = "a"\ndepends_on = ["b"]\n\n[[nodes]]\nid = "b"\ndepends_on = ["a"]\n',
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "cycle"


def test_rejects_unknown_model_role(tmp_path):
    path = _write(
        tmp_path, "g.toml", _GRAPH_HEADER + '[[nodes]]\nid = "a"\nmodel_role = "not_a_real_role"\n'
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "unknown_model_role"


def test_rejects_unknown_skill(tmp_path):
    path = _write(
        tmp_path, "g.toml", _GRAPH_HEADER + '[[nodes]]\nid = "a"\nskill = "not-a-skill"\n'
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "unknown_skill"


def test_rejects_unbounded_revision(tmp_path):
    """revisable_from without max_visits — checked per-node before any graph-shape check."""
    path = _write(
        tmp_path, "g.toml", _GRAPH_HEADER + '[[nodes]]\nid = "a"\nrevisable_from = ["a"]\n'
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "unbounded_revision"


def test_rejects_unsafe_gate(tmp_path):
    path = _write(
        tmp_path,
        "g.toml",
        _GRAPH_HEADER + '[[nodes]]\nid = "a"\ngate = true\nexternal_effect = "send_email"\n',
    )
    with pytest.raises(PackValidationError) as exc:
        load_pack_graph(path)
    assert exc.value.rule == "unsafe_gate"


def test_gate_with_publish_effect_is_allowed(tmp_path):
    path = _write(
        tmp_path,
        "g.toml",
        _GRAPH_HEADER + '[[nodes]]\nid = "a"\ngate = true\nexternal_effect = "publish"\n',
    )
    graph = load_pack_graph(path)
    assert graph.node("a").gate is True


# ── load_pack_inputs: one test per rejection rule ─────────────────────────────


def test_inputs_rejects_missing_key(tmp_path):
    path = _write(tmp_path, "i.toml", '[[settings]]\nsource = "ask"\n')
    with pytest.raises(PackValidationError) as exc:
        load_pack_inputs(path)
    assert exc.value.rule == "missing_key"


def test_inputs_rejects_unknown_source(tmp_path):
    path = _write(tmp_path, "i.toml", '[[settings]]\nkey = "brand"\nsource = "telepathy"\n')
    with pytest.raises(PackValidationError) as exc:
        load_pack_inputs(path)
    assert exc.value.rule == "unknown_source"


def test_inputs_rejects_missing_topic(tmp_path):
    path = _write(tmp_path, "i.toml", "[[knowledge]]\nrequired = true\n")
    with pytest.raises(PackValidationError) as exc:
        load_pack_inputs(path)
    assert exc.value.rule == "missing_topic"


def test_inputs_rejects_unknown_freshness(tmp_path):
    path = _write(tmp_path, "i.toml", '[[knowledge]]\ntopic = "voice"\nfreshness = "stale-ish"\n')
    with pytest.raises(PackValidationError) as exc:
        load_pack_inputs(path)
    assert exc.value.rule == "unknown_freshness"


# ── golden equivalence: linkedin-post.toml vs. the hardcoded DEFAULT_GRAPH ────


LINKEDIN_POST = REPO / "packs" / "marketing" / "graphs" / "linkedin-post.toml"


def test_pack_prompts_and_roles_exactly_match_hardcoded_pipeline():
    """If pipeline_executor.execute_stage were driven by this pack's node metadata
    instead of the hardcoded STAGE_PROMPTS/_STAGE_ROLES, every SDK call parameter
    (prompt text, model role) would be byte-identical — proving the TOML is a
    faithful data-ization of today's pipeline, not just structurally similar."""
    pack, _ = load_engine_graph(LINKEDIN_POST)
    prompts = {n.id: n.prompt for n in pack.nodes}
    roles = {n.id: n.model_role for n in pack.nodes}

    assert prompts == STAGE_PROMPTS
    assert roles == _STAGE_ROLES


def test_pack_graph_shape_matches_default_graph():
    pack, engine_graph = load_engine_graph(LINKEDIN_POST)
    assert engine_graph.ids == DEFAULT_GRAPH.ids == STAGES
    for name in STAGES:
        assert engine_graph.node(name).depends_on == DEFAULT_GRAPH.node(name).depends_on


def test_pack_driven_runner_matches_default_graph_runner_trajectory(cfg):
    """End-to-end: run the SAME fake executor through (a) DEFAULT_GRAPH and (b) the
    pack-loaded engine graph, and assert an identical stage sequence + manifest."""
    _, pack_engine_graph = load_engine_graph(LINKEDIN_POST)

    ran_default: list[str] = []
    ran_pack: list[str] = []

    def _fake_executor(recorder):
        async def _run(stage, manifest):
            recorder.append(stage)
            return StageOutcome(status="ok")

        return _run

    runner_default = PipelineRunner(cfg, PROFILE, graph=DEFAULT_GRAPH)
    runner_pack = PipelineRunner(cfg, "example-pack-twin", graph=pack_engine_graph)

    m1 = asyncio.run(runner_default.run("r-default", "cron", _fake_executor(ran_default)))
    m2 = asyncio.run(runner_pack.run("r-pack", "cron", _fake_executor(ran_pack)))

    assert ran_default == ran_pack == list(STAGES)
    assert [s["status"] for s in m1["stages"]] == [s["status"] for s in m2["stages"]]


def test_make_executor_from_pack_builds_a_working_executor(cfg, monkeypatch):
    """make_executor_from_pack must actually route through execute_stage with the
    pack's prompts/roles — not just structurally resemble a StageExecutor."""
    pack, engine_graph = load_engine_graph(LINKEDIN_POST)

    seen_prompts = {}
    seen_roles = {}

    async def _fake_execute_stage(
        cfg_arg, profile, stage_name, manifest, prompts=None, stage_roles=None
    ):
        seen_prompts[stage_name] = prompts.get(stage_name)
        seen_roles[stage_name] = stage_roles.get(stage_name)
        return StageOutcome(status="ok")

    monkeypatch.setattr("agent.packs.execute_stage", _fake_execute_stage)
    executor = make_executor_from_pack(cfg, PROFILE, pack)

    runner = PipelineRunner(cfg, "example-pack-exec-twin", graph=engine_graph)
    asyncio.run(runner.run("r-exec", "cron", executor))

    assert seen_prompts == STAGE_PROMPTS
    assert seen_roles == _STAGE_ROLES


# ── readiness check ────────────────────────────────────────────────────────────


def _write_profile(root: Path, name: str, profile_md: str, knowledge: dict[str, str]) -> None:
    pdir = root / name
    (pdir / "knowledge").mkdir(parents=True)
    (pdir / "PROFILE.md").write_text(profile_md)
    for topic, content in knowledge.items():
        (pdir / "knowledge" / f"{topic}.md").write_text(content)


def test_readiness_blocks_on_missing_required_setting(tmp_path):
    _write_profile(tmp_path, "acme", "company: Acme\n", {})
    inputs = PackInputs(settings=(PackInputSetting(key="brand_name", source="ask", required=True),))
    report = check_readiness(tmp_path, "acme", inputs)
    assert report.blocked
    assert report.items[0].status == RED


def test_readiness_blocks_on_missing_required_knowledge(tmp_path):
    _write_profile(tmp_path, "acme", "brand_name: Acme\n", {})
    inputs = PackInputs(
        knowledge=(PackInputKnowledge(topic="voice", required=True, freshness="evergreen"),)
    )
    report = check_readiness(tmp_path, "acme", inputs)
    assert report.blocked


def test_readiness_degrades_but_does_not_block_on_missing_optional_knowledge(tmp_path):
    _write_profile(tmp_path, "acme", "brand_name: Acme\n", {})
    inputs = PackInputs(
        knowledge=(PackInputKnowledge(topic="case-studies", required=False, freshness="evergreen"),)
    )
    report = check_readiness(tmp_path, "acme", inputs)
    assert not report.blocked
    assert report.degraded


def test_readiness_green_when_everything_present_and_fresh(tmp_path):
    _write_profile(tmp_path, "acme", "brand_name: Acme\n", {"voice": "we sound like this"})
    inputs = PackInputs(
        settings=(PackInputSetting(key="brand_name", source="ask", required=True),),
        knowledge=(PackInputKnowledge(topic="voice", required=True, freshness="evergreen"),),
    )
    report = check_readiness(tmp_path, "acme", inputs)
    assert not report.blocked and not report.degraded
    assert all(i.status == GREEN for i in report.items)


def test_readiness_yellow_on_stale_90d_topic(tmp_path):
    _write_profile(tmp_path, "acme", "brand_name: Acme\n", {"company": "old facts"})
    company_path = tmp_path / "acme" / "knowledge" / "company.md"
    stale_mtime = time.time() - (200 * 86400)  # 200 days old
    os.utime(company_path, (stale_mtime, stale_mtime))

    inputs = PackInputs(
        knowledge=(PackInputKnowledge(topic="company", required=True, freshness="90d"),)
    )
    report = check_readiness(tmp_path, "acme", inputs)
    assert not report.blocked  # present, just stale — degrade, don't block
    assert report.degraded
    assert report.items[0].status == YELLOW


def test_readiness_against_a_populated_profile_is_green():
    """Grounding: the real marketing pack's inputs against a real, fully-populated
    profile. Discovers the profile rather than hardcoding a tenant name, so it grounds
    on whatever bundles the checkout has (skips in a _template-only public cut)."""
    from agent.profiles import list_profiles

    inputs = load_pack_inputs(REPO / "packs" / "marketing" / "inputs.toml")
    for name in sorted(list_profiles(REPO / "profiles")):
        report = check_readiness(REPO / "profiles", name, inputs)
        if not report.blocked and not report.degraded:
            return  # a fully-ready profile grounds the pack readiness contract
    pytest.skip("no fully-populated profile present to ground marketing readiness")
