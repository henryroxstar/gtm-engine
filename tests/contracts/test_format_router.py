"""Contract tests for the format-router skill and cross-modal-campaign pack graph.

The format-router skill is a prompt-level dispatcher: these tests verify the manifest,
pack graph shape, and that the generated SKILL.md references fatigue, budget, and the
video-router hand-off. Runtime behaviour (actual format selection given a hook and budget)
is exercised in the pack E2E test.
"""

from __future__ import annotations

from pathlib import Path

from agent.packs import load_engine_graph
from gtm_core.packs.loader import load_pack_graph
from gtm_core.skills.registry import all_skills

REPO = Path(__file__).resolve().parents[2]
CROSS_MODAL_GRAPH = REPO / "packs" / "creator" / "graphs" / "cross-modal-campaign.toml"


def test_format_router_skill_is_registered():
    names = {s.name for s in all_skills()}
    assert "format-router" in names


def test_format_router_skill_body_references_key_concepts():
    body = (REPO / "plugin" / "skills" / "format-router" / "body_template.md").read_text()
    assert "fatigue" in body.lower()
    assert "budget" in body.lower()
    assert "video-router" in body
    assert "hook_score" in body
    assert "modal_family" in body


def test_cross_modal_campaign_graph_loads():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    assert pack.pack == "creator"
    assert pack.variant == "cross-modal-campaign"
    assert pack.ids == (
        "radar",
        "format-plan",
        "text-studio",
        "image-studio",
        "video-script",
        "publish",
    )


def test_cross_modal_campaign_plan_node_is_format_router_gate():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    plan = pack.node("format-plan")
    assert plan.skill == "format-router"
    assert plan.gate is True
    assert plan.model_role == "brain_plan"


def test_cross_modal_campaign_fan_out_from_plan():
    _, engine_graph = load_engine_graph(CROSS_MODAL_GRAPH)
    for node_id in ("text-studio", "image-studio", "video-script"):
        assert engine_graph.node(node_id).depends_on == ("format-plan",)


def test_cross_modal_campaign_publish_joins_all_modalities():
    _, engine_graph = load_engine_graph(CROSS_MODAL_GRAPH)
    publish = engine_graph.node("publish")
    assert publish.gate is True
    assert publish.external_effect == "publish"
    assert set(publish.depends_on) == {"text-studio", "image-studio", "video-script"}


def test_cross_modal_campaign_uses_content_studio_for_text_and_image():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    assert pack.node("text-studio").skill == "content-studio"
    assert pack.node("image-studio").skill == "content-studio"


def test_cross_modal_campaign_uses_video_script_for_video():
    pack = load_pack_graph(CROSS_MODAL_GRAPH)
    assert pack.node("video-script").skill == "video-script"


# ── every creator graph reaches the hook guards, by SOME node ────────────────
#
# `format-router` is the only skill that runs format affinity, fatigue and the composite
# score together, and it appears in exactly one of the seven creator graphs. The other six
# are not therefore unguarded — but they are guarded by DIFFERENT skills, and nothing before
# this test stopped one of those skills from quietly dropping a check.
#
# Deliberately NOT fixed by adding a `format-router` node to the six: that skill's job is
# deciding WHICH (format, platform) tuples to produce, and a dedicated `short-form-video` /
# `repurpose-clips` graph has no such decision to make. Adding it there would install a
# human gate on six live lanes purely to reach its side-effect checks.
#
# The guards, and who provides each:
#   * format affinity — the hook must declare the item's `format`
#   * fatigue         — a fatigued hook must not reach a render
# `content-plan` is the gate node on all six and is where a `hook_id` is CHOSEN, so it
# carries both. `video-script` repeats them on the three script-bearing lanes (it is the
# last point before a shot list is written). `demo-clips`, `repurpose-clips` and
# `restyle-shorts` reach neither `format-router` nor `video-script`, which is why
# `content-plan` carrying fatigue is load-bearing rather than belt-and-braces.

CREATOR_GRAPHS = sorted((REPO / "packs" / "creator" / "graphs").glob("*.toml"))

#: skill -> the guards its body actually runs. A skill absent here provides none.
HOOK_GUARDS = {
    "format-router": {"affinity", "fatigue"},
    "video-script": {"affinity", "fatigue"},
    "content-plan": {"affinity", "fatigue"},
    "content-studio": {"affinity"},
}

#: How each guard is DETECTED in a skill body — a substring that only appears when the
#: check is really there. Not a grep for the word "hook": these name the mechanism.
GUARD_MARKERS = {
    "affinity": ("must declare", "declare the format", "does not declare"),
    "fatigue": ("hooks fatigued",),
}


def _body(skill: str) -> str:
    return (REPO / "plugin" / "skills" / skill / "body_template.md").read_text(encoding="utf-8")


def test_hook_guard_table_matches_the_skill_bodies():
    """The table above is a claim about five skill bodies. Prove it, so that removing a
    check from a body fails HERE with the graph it leaves unguarded, instead of silently
    widening the hole the next test measures."""
    for skill, guards in HOOK_GUARDS.items():
        body = _body(skill)
        for guard in ("affinity", "fatigue"):
            present = any(m in body for m in GUARD_MARKERS[guard])
            assert present == (guard in guards), (
                f"{skill} {'lost' if guard in guards else 'gained'} its {guard} check — "
                f"update HOOK_GUARDS and re-check which creator graphs that changes"
            )


def test_every_creator_graph_reaches_affinity_and_fatigue():
    assert len(CREATOR_GRAPHS) == 7, "creator graph count changed — re-check this table"
    for path in CREATOR_GRAPHS:
        pack = load_pack_graph(path)
        covered: set[str] = set()
        for node in pack.nodes:
            covered |= HOOK_GUARDS.get(node.skill or "", set())
        missing = {"affinity", "fatigue"} - covered
        assert not missing, (
            f"creator graph {pack.variant!r} routes through "
            f"{sorted({n.skill for n in pack.nodes if n.skill})} and so never runs: "
            f"{sorted(missing)}. A hook can reach a render unchecked on this lane."
        )


def test_three_creator_graphs_depend_on_content_plan_alone_for_fatigue():
    """The reason `content-plan` may not drop its fatigue check: on these three lanes it is
    the ONLY node that runs one. Named explicitly so the dependency is greppable."""
    alone = set()
    for path in CREATOR_GRAPHS:
        pack = load_pack_graph(path)
        providers = {
            n.skill for n in pack.nodes if "fatigue" in HOOK_GUARDS.get(n.skill or "", set())
        }
        if providers == {"content-plan"}:
            alone.add(pack.variant)
    assert alone == {"demo-clips", "repurpose-clips", "restyle-shorts"}
